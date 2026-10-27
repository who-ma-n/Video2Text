"""Unit tests for extractor.py regex, timestamp parsing, and snapshot extraction."""

from pathlib import Path
from unittest.mock import patch
import pytest
from video2book.extractor import (
    SNAP_REGEX,
    normalize_timestamp,
    process_markdown_snapshots,
    slugify,
)


def test_normalize_timestamp():
    # HH:MM:SS
    fmt, sec, san = normalize_timestamp("01:23:45")
    assert fmt == "01:23:45"
    assert sec == 5025.0
    assert san == "01h23m45s"

    # H:MM:SS
    fmt, sec, san = normalize_timestamp("2:15:10")
    assert fmt == "02:15:10"
    assert sec == 8110.0
    assert san == "02h15m10s"

    # MM:SS
    fmt, sec, san = normalize_timestamp("14:30")
    assert fmt == "00:14:30"
    assert sec == 870.0
    assert san == "00h14m30s"

    # M:SS
    fmt, sec, san = normalize_timestamp("3:05")
    assert fmt == "00:03:05"
    assert sec == 185.0
    assert san == "00h03m05s"

    # Raw seconds
    fmt, sec, san = normalize_timestamp("125")
    assert fmt == "00:02:05"
    assert sec == 125.0
    assert san == "00h02m05s"


def test_slugify():
    assert slugify("Lecture 01: Intro to OS & Systems!") == "lecture_01_intro_to_os_systems"
    assert slugify("Simple_File") == "simple_file"


def test_snap_regex():
    sample_text = """
# Chapter 1

Here is the system architecture:
[SNAP: 00:12:45 | Distributed Cluster Topography]

Next, consider the state machine transitions:
[SNAP: 5:10 | Raft Consensus States]

And the memory pool layout:
[SNAP: 45 | Buffer Pool Frame Allocation]
"""
    matches = SNAP_REGEX.findall(sample_text)
    assert len(matches) == 3
    assert matches[0] == ("00:12:45", "Distributed Cluster Topography")
    assert matches[1] == ("5:10", "Raft Consensus States")
    assert matches[2] == ("45", "Buffer Pool Frame Allocation")


def test_process_markdown_snapshots(tmp_path: Path):
    video_file = tmp_path / "lecture_01.mp4"
    video_file.touch()

    markdown_input = """# Chapter 1
Here is the first diagram:
[SNAP: 01:20 | Network Topology]

And here is another diagram at the same timestamp:
[SNAP: 01:20 | Alternate Packet Routing]
"""

    # Mock extract_frame_ffmpeg to simulate successful ffmpeg extraction
    with patch("video2book.extractor.extract_frame_ffmpeg", return_value=True) as mock_extract:
        processed_md, count = process_markdown_snapshots(
            markdown_content=markdown_input,
            video_path=video_file,
            output_dir=tmp_path,
        )

        assert count == 2
        assert mock_extract.call_count == 2

        # Check standard markdown image syntax substitution
        assert "![Network Topology](images/lecture_01_00h01m20s.png)" in processed_md
        # Second image at same timestamp gets collision counter
        assert "![Alternate Packet Routing](images/lecture_01_00h01m20s_2.png)" in processed_md
        assert "*Figure: Network Topology (Timestamp: 00:01:20)*" in processed_md
        assert "[SNAP:" not in processed_md
