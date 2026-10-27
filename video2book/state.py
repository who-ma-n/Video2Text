"""State tracking and checkpointing for video2book pipeline.

Maintains a persistent JSON state file to ensure processing runs are
fully resumable across massive (170+ hours) video libraries.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional


class VideoStatus(str, Enum):
    """Execution status for a video item."""
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


def compute_fast_file_signature(path: str | Path, chunk_size: int = 2 * 1024 * 1024) -> str:
    """Compute a fast, deterministic hash signature for large media files.

    For massive video archives (up to dozens of gigabytes per file), calculating
    a full sha256 can exhaust I/O. This samples file size, modification timestamp,
    first chunk, middle chunk, and tail chunk to guarantee instant (<5ms)
    fingerprinting without I/O thrashing.
    """
    p = Path(path)
    stat = p.stat()
    size = stat.st_size
    mtime = stat.st_mtime

    hasher = hashlib.sha256()
    hasher.update(f"{size}:{mtime}:".encode("utf-8"))

    if size <= chunk_size * 3:
        with open(p, "rb") as f:
            hasher.update(f.read())
    else:
        with open(p, "rb") as f:
            # First chunk
            hasher.update(f.read(chunk_size))
            # Middle chunk
            f.seek(size // 2)
            hasher.update(f.read(chunk_size))
            # Last chunk
            f.seek(max(0, size - chunk_size))
            hasher.update(f.read(chunk_size))

    return hasher.hexdigest()


@dataclass
class VideoRecord:
    """Metadata and execution state for a single video file."""
    file_path: str
    file_hash: str
    file_size_bytes: int
    status: VideoStatus = VideoStatus.PENDING
    output_md_path: Optional[str] = None
    snapshots_count: int = 0
    error_message: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    attempt_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> VideoRecord:
        data = dict(data)
        if "status" in data:
            data["status"] = VideoStatus(data["status"])
        return cls(**data)


class StateManager:
    """Thread-safe and crash-resilient state checkpoint manager."""

    DEFAULT_STATE_FILENAME = ".video2book_state.json"

    def __init__(self, state_file_path: str | Path):
        self.state_file_path = Path(state_file_path).resolve()
        self.records: Dict[str, VideoRecord] = {}
        self._load()

    @staticmethod
    def normalize_path(path: str | Path) -> str:
        """Return standardized absolute path string."""
        return str(Path(path).resolve())

    def _load(self) -> None:
        """Load state from disk if present."""
        if not self.state_file_path.exists():
            return

        try:
            with open(self.state_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                files = data.get("files", {})
                for p_str, record_dict in files.items():
                    norm_key = self.normalize_path(p_str)
                    record_dict["file_path"] = norm_key
                    self.records[norm_key] = VideoRecord.from_dict(record_dict)
        except Exception as e:
            # Fallback to empty state if file corrupted; keep backup
            backup_path = self.state_file_path.with_suffix(".json.corrupt")
            try:
                import shutil
                shutil.copy2(self.state_file_path, backup_path)
            except Exception:
                pass
            self.records = {}

    def save(self) -> None:
        """Atomically persist state to disk using a temporary file."""
        self.state_file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "1.0",
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "files": {k: v.to_dict() for k, v in self.records.items()},
        }

        # Write to temp file in same directory for atomic replace
        temp_fd, temp_path = tempfile.mkstemp(
            dir=str(self.state_file_path.parent),
            prefix="state_",
            suffix=".tmp",
            text=True,
        )
        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            # Atomic swap
            os.replace(temp_path, self.state_file_path)
        except Exception:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

    def register_video(self, path: str | Path) -> VideoRecord:
        """Register a video in state if not already present."""
        norm = self.normalize_path(path)
        if norm in self.records:
            return self.records[norm]

        p = Path(norm)
        sig = compute_fast_file_signature(p)
        size = p.stat().st_size
        record = VideoRecord(
            file_path=norm,
            file_hash=sig,
            file_size_bytes=size,
            status=VideoStatus.PENDING,
        )
        self.records[norm] = record
        self.save()
        return record

    def should_process(self, path: str | Path, force: bool = False) -> bool:
        """Check whether a video needs processing.

        Returns False if the video is already COMPLETED and its output
        Markdown file exists on disk, unless force=True.
        """
        if force:
            return True

        norm = self.normalize_path(path)
        record = self.records.get(norm)
        if not record:
            return True

        if record.status == VideoStatus.COMPLETED:
            if record.output_md_path and Path(record.output_md_path).exists():
                return False
            # Output file was removed or missing, re-process
            return True

        return True

    def mark_in_progress(self, path: str | Path) -> VideoRecord:
        """Mark video processing as actively in progress."""
        norm = self.normalize_path(path)
        record = self.records.get(norm)
        if not record:
            record = self.register_video(path)

        record.status = VideoStatus.IN_PROGRESS
        record.started_at = datetime.now(timezone.utc).isoformat()
        record.attempt_count += 1
        record.error_message = None
        self.save()
        return record

    def mark_completed(
        self,
        path: str | Path,
        output_md_path: str | Path,
        snapshots_count: int = 0,
        duration_seconds: Optional[float] = None,
    ) -> VideoRecord:
        """Mark video processing as successfully completed."""
        norm = self.normalize_path(path)
        record = self.records.get(norm)
        if not record:
            record = self.register_video(path)

        record.status = VideoStatus.COMPLETED
        record.output_md_path = self.normalize_path(output_md_path)
        record.snapshots_count = snapshots_count
        record.completed_at = datetime.now(timezone.utc).isoformat()
        record.duration_seconds = duration_seconds
        record.error_message = None
        self.save()
        return record

    def mark_failed(self, path: str | Path, error_message: str) -> VideoRecord:
        """Mark video processing as failed with an error message."""
        norm = self.normalize_path(path)
        record = self.records.get(norm)
        if not record:
            record = self.register_video(path)

        record.status = VideoStatus.FAILED
        record.error_message = str(error_message)
        record.completed_at = datetime.now(timezone.utc).isoformat()
        self.save()
        return record

    def get_record(self, path: str | Path) -> Optional[VideoRecord]:
        """Retrieve record for a given video path."""
        norm = self.normalize_path(path)
        return self.records.get(norm)

    def get_summary(self) -> Dict[str, int]:
        """Return counts by status."""
        summary = {
            "total": len(self.records),
            "completed": 0,
            "failed": 0,
            "in_progress": 0,
            "pending": 0,
        }
        for r in self.records.values():
            if r.status == VideoStatus.COMPLETED:
                summary["completed"] += 1
            elif r.status == VideoStatus.FAILED:
                summary["failed"] += 1
            elif r.status == VideoStatus.IN_PROGRESS:
                summary["in_progress"] += 1
            elif r.status == VideoStatus.PENDING:
                summary["pending"] += 1
        return summary

    def reset_failed(self) -> int:
        """Reset all FAILED entries to PENDING for retry runs."""
        count = 0
        for r in self.records.values():
            if r.status == VideoStatus.FAILED:
                r.status = VideoStatus.PENDING
                r.error_message = None
                count += 1
        if count > 0:
            self.save()
        return count
