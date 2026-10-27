"""Snapshot extraction and Markdown processing via ffmpeg and regex."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Regex matching [SNAP: timestamp | caption]
# Tolerates optional brackets, whitespace, and variable timestamp formats
SNAP_REGEX = re.compile(
    r"\[SNAP:\s*([0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?|[0-9]+)\s*\|\s*([^\]]+?)\]",
    re.IGNORECASE,
)


@dataclass
class SnapshotMarker:
    """Parsed visual marker from the LLM output."""
    raw_tag: str
    raw_timestamp: str
    caption: str
    normalized_timestamp: str
    seconds: float
    sanitized_timestamp: str


def check_ffmpeg_installed() -> Tuple[bool, Optional[str]]:
    """Verify that system ffmpeg is available in PATH.

    Returns:
        (is_available, version_or_path_or_none)
    """
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        return False, None

    try:
        res = subprocess.run(
            [ffmpeg_bin, "-version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if res.returncode == 0:
            first_line = res.stdout.splitlines()[0] if res.stdout else ffmpeg_bin
            return True, first_line
    except Exception as e:
        logger.debug(f"ffmpeg check encountered: {e}")

    return bool(ffmpeg_bin), ffmpeg_bin


def normalize_timestamp(ts_str: str) -> Tuple[str, float, str]:
    """Parse and normalize various timestamp representations.

    Supports:
        - "HH:MM:SS" (e.g. "01:23:45")
        - "H:MM:SS"  (e.g. "1:23:45")
        - "MM:SS"    (e.g. "05:30")
        - "M:SS"     (e.g. "5:30")
        - "SS"       (e.g. "330")

    Returns:
        Tuple of (formatted_hh_mm_ss, total_seconds, filename_safe_string)
    """
    ts_str = ts_str.strip()
    parts = ts_str.split(":")

    if len(parts) == 3:
        h, m, s = int(parts[0]), int(parts[1]), float(parts[2])
    elif len(parts) == 2:
        h, m, s = 0, int(parts[0]), float(parts[1])
    elif len(parts) == 1:
        total = float(parts[0])
        h = int(total // 3600)
        rem = total % 3600
        m = int(rem // 60)
        s = rem % 60
    else:
        raise ValueError(f"Unrecognized timestamp format: '{ts_str}'")

    total_seconds = (h * 3600) + (m * 60) + s
    formatted = f"{h:02d}:{m:02d}:{int(s):02d}"
    sanitized = f"{h:02d}h{m:02d}m{int(s):02d}s"

    return formatted, total_seconds, sanitized


def slugify(text: str) -> str:
    """Generate a clean, filesystem-safe slug from a string."""
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "_", text)


def extract_frame_ffmpeg(
    video_path: str | Path,
    timestamp: str,
    output_image_path: str | Path,
    ffmpeg_binary: str = "ffmpeg",
) -> bool:
    """Extract a single frame from video at the specified timestamp using ffmpeg.

    Args:
        video_path: Path to the input video file.
        timestamp: Time string (e.g. "00:15:30" or seconds).
        output_image_path: Destination path for the extracted frame.
        ffmpeg_binary: Path or name of the ffmpeg executable.

    Returns:
        True if extraction was successful and file exists, False otherwise.
    """
    out_path = Path(output_image_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # -y: overwrite output
    # -ss before -i: fast seek to keyframe
    # -frames:v 1: extract 1 frame
    # -q:v 2: high quality JPEG/PNG
    cmd = [
        ffmpeg_binary,
        "-y",
        "-ss",
        timestamp,
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(out_path),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if result.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0:
            return True
        else:
            logger.warning(
                f"ffmpeg frame extraction failed for {video_path} at {timestamp} "
                f"(exit code {result.returncode}): {result.stderr.strip()[-200:]}"
            )
            return False
    except subprocess.TimeoutExpired:
        logger.error(f"ffmpeg timed out extracting frame at {timestamp} from {video_path}")
        return False
    except Exception as e:
        logger.error(f"Exception during ffmpeg frame extraction: {e}")
        return False


def process_markdown_snapshots(
    markdown_content: str,
    video_path: str | Path,
    output_dir: str | Path,
    ffmpeg_binary: str = "ffmpeg",
) -> Tuple[str, int]:
    """Parse [SNAP: ...] markers in markdown, extract frames, and substitute markdown image links.

    Screenshots are saved under `<output_dir>/images/<video_slug>_<timestamp_sanitized>.png`.
    Markers are replaced with:
    `![Caption](images/<video_slug>_<timestamp_sanitized>.png)`

    Returns:
        Tuple of (processed_markdown_text, number_of_extracted_snapshots)
    """
    video_p = Path(video_path)
    out_dir = Path(output_dir)
    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    video_slug = slugify(video_p.stem)

    # Track seen filenames to avoid collisions if multiple snapshots share the exact same timestamp
    used_filenames: dict[str, int] = {}
    extracted_count = 0

    def replace_snap(match: re.Match) -> str:
        nonlocal extracted_count
        raw_tag = match.group(0)
        ts_raw = match.group(1).strip()
        caption = match.group(2).strip()

        try:
            norm_ts, total_sec, sanitized_ts = normalize_timestamp(ts_raw)
        except Exception as e:
            logger.warning(f"Failed to parse timestamp '{ts_raw}': {e}")
            return raw_tag

        base_name = f"{video_slug}_{sanitized_ts}"
        if base_name in used_filenames:
            used_filenames[base_name] += 1
            image_name = f"{base_name}_{used_filenames[base_name]}.png"
        else:
            used_filenames[base_name] = 1
            image_name = f"{base_name}.png"

        image_path = images_dir / image_name
        relative_markdown_path = f"images/{image_name}"

        # Extract frame via ffmpeg
        success = extract_frame_ffmpeg(
            video_path=video_p,
            timestamp=norm_ts,
            output_image_path=image_path,
            ffmpeg_binary=ffmpeg_binary,
        )

        if success:
            extracted_count += 1
            return f"\n\n![{caption}]({relative_markdown_path})\n*Figure: {caption} (Timestamp: {norm_ts})*\n"
        else:
            # Fallback format preserving caption even if ffmpeg failed
            return f"\n\n> ⚠️ *[Visual Figure at {norm_ts}: {caption}]*\n"

    processed_md = SNAP_REGEX.sub(replace_snap, markdown_content)
    return processed_md, extracted_count
