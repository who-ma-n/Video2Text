"""Unit tests for video2book state management and checkpointing."""

import json
from pathlib import Path
import pytest
from video2book.state import StateManager, VideoRecord, VideoStatus, compute_fast_file_signature


def test_fast_signature_and_consistency(tmp_path: Path):
    test_file = tmp_path / "sample_video.mp4"
    test_file.write_bytes(b"A" * (1024 * 1024 * 8))  # 8 MB file

    sig1 = compute_fast_file_signature(test_file)
    sig2 = compute_fast_file_signature(test_file)
    assert sig1 == sig2
    assert len(sig1) == 64


def test_state_lifecycle_and_persistence(tmp_path: Path):
    state_file = tmp_path / "state.json"
    manager = StateManager(state_file)

    video_file = tmp_path / "lecture1.mp4"
    video_file.write_bytes(b"sample video bytes")

    # 1. Register video
    record = manager.register_video(video_file)
    assert record.status == VideoStatus.PENDING
    assert state_file.exists()

    # Verify atomic file contents
    with open(state_file, "r", encoding="utf-8") as f:
        data = json.load(f)
        assert len(data["files"]) == 1

    # 2. Mark In Progress
    manager.mark_in_progress(video_file)
    assert manager.get_record(video_file).status == VideoStatus.IN_PROGRESS

    # 3. Mark Completed
    output_md = tmp_path / "lecture1.md"
    output_md.write_text("# Lecture 1\nContent")
    manager.mark_completed(video_file, output_md, snapshots_count=3, duration_seconds=12.5)

    rec = manager.get_record(video_file)
    assert rec.status == VideoStatus.COMPLETED
    assert rec.snapshots_count == 3
    assert rec.output_md_path == str(output_md.resolve())

    # 4. Resumability: should_process should return False
    assert manager.should_process(video_file, force=False) is False
    # If force=True, should_process returns True
    assert manager.should_process(video_file, force=True) is True

    # If output file deleted, should re-process
    output_md.unlink()
    assert manager.should_process(video_file, force=False) is True


def test_state_failure_and_reset(tmp_path: Path):
    state_file = tmp_path / "state.json"
    manager = StateManager(state_file)

    video_file = tmp_path / "failing_lecture.mp4"
    video_file.write_bytes(b"data")

    manager.mark_in_progress(video_file)
    manager.mark_failed(video_file, "Rate limit exceeded or invalid codec")

    rec = manager.get_record(video_file)
    assert rec.status == VideoStatus.FAILED
    assert "Rate limit" in rec.error_message

    summary = manager.get_summary()
    assert summary["failed"] == 1
    assert summary["completed"] == 0

    # Reset failed
    reset_count = manager.reset_failed()
    assert reset_count == 1
    assert manager.get_record(video_file).status == VideoStatus.PENDING
