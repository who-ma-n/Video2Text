"""Unit tests for cli.py."""

from pathlib import Path
from unittest.mock import patch
from click.testing import CliRunner
import pytest

from video2book.cli import discover_video_files, main, natural_sort_key


def test_natural_sorting():
    files = ["lecture 10.mp4", "lecture 2.mp4", "lecture 1.mp4", "lecture 20.mp4"]
    sorted_files = sorted(files, key=natural_sort_key)
    assert sorted_files == ["lecture 1.mp4", "lecture 2.mp4", "lecture 10.mp4", "lecture 20.mp4"]


def test_discover_video_files(tmp_path: Path):
    sub = tmp_path / "week1"
    sub.mkdir()
    (sub / "lec2.mp4").touch()
    (sub / "lec1.mkv").touch()
    (sub / "notes.txt").touch()

    found = discover_video_files(tmp_path, ["mp4", "mkv"])
    assert len(found) == 2
    assert any(f.name == "lec1.mkv" for f in found)
    assert any(f.name == "lec2.mp4" for f in found)


def test_cli_missing_ffmpeg(tmp_path: Path):
    runner = CliRunner()
    with patch("video2book.cli.check_ffmpeg_installed", return_value=(False, None)):
        result = runner.invoke(main, ["-i", str(tmp_path)])
        assert result.exit_code != 0
        assert "System ffmpeg is required" in result.output


def test_cli_dry_run_success(tmp_path: Path):
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    (video_dir / "lecture_01.mp4").write_bytes(b"data1")
    (video_dir / "lecture_02.mp4").write_bytes(b"data2")

    out_dir = tmp_path / "output"

    runner = CliRunner()
    with patch("video2book.cli.check_ffmpeg_installed", return_value=(True, "ffmpeg version 7.0")):
        result = runner.invoke(
            main,
            [
                "-i", str(video_dir),
                "-o", str(out_dir),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert "DRY RUN AUDIT" in result.output
        assert "lecture_01.mp4" in result.output
        assert "lecture_02.mp4" in result.output


def test_cli_missing_input_dir():
    runner = CliRunner()
    result = runner.invoke(main, [])
    assert result.exit_code != 0
    assert "Missing required option '--input-dir'" in result.output


def test_cli_gui_flag():
    runner = CliRunner()
    with patch("video2book.gui.launch_gui", return_value=0) as mock_launch:
        result = runner.invoke(main, ["--gui"])
        assert result.exit_code == 0
        mock_launch.assert_called_once()


def test_cli_list_models():
    runner = CliRunner()
    mock_models = [
        {"id": "google/gemini-2.5-flash", "name": "Gemini 2.5 Flash", "video_capable": True, "context_length": 1000000}
    ]
    with patch("video2book.cli.fetch_openrouter_gemini_models", return_value=mock_models):
        result = runner.invoke(main, ["--list-models"])
        assert result.exit_code == 0
        assert "Available Gemini Models on OpenRouter" in result.output
        assert "google/gemini-2.5-flash" in result.output


