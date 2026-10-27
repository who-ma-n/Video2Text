"""Integration test verifying real ffmpeg extraction on the host machine."""

import subprocess
from pathlib import Path
import pytest
from video2book.extractor import check_ffmpeg_installed, process_markdown_snapshots


def test_real_ffmpeg_frame_extraction(tmp_path: Path):
    is_installed, _ = check_ffmpeg_installed()
    if not is_installed:
        pytest.skip("ffmpeg not available on host system")

    # Generate a synthetic 3-second test video with ffmpeg:
    # 320x240 test pattern video
    test_video = tmp_path / "synthetic_lecture.mp4"
    gen_cmd = [
        "ffmpeg",
        "-y",
        "-f", "lavfi",
        "-i", "testsrc=duration=3:size=320x240:rate=10",
        "-pix_fmt", "yuv420p",
        str(test_video),
    ]
    res = subprocess.run(gen_cmd, capture_output=True, text=True, check=False)
    assert res.returncode == 0
    assert test_video.exists() and test_video.stat().st_size > 0

    markdown_input = """# Synthetic Lecture
[SNAP: 00:00:01 | Test Pattern Frame]
"""
    processed_md, count = process_markdown_snapshots(
        markdown_content=markdown_input,
        video_path=test_video,
        output_dir=tmp_path,
    )

    assert count == 1
    expected_image = tmp_path / "images" / "synthetic_lecture_00h00m01s.png"
    assert expected_image.exists()
    assert expected_image.stat().st_size > 0
    assert "![Test Pattern Frame](images/synthetic_lecture_00h00m01s.png)" in processed_md
