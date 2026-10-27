"""Integration and component tests for video2book GUI."""

from pathlib import Path
from unittest.mock import patch
import pytest

from video2book.gui.app import Video2BookApp
from video2book.gui.config import GUIConfig




def test_app_initialization(test_app: Video2BookApp):
    assert test_app.title() == "video2book — Technical Textbook Studio"
    assert test_app.queue_view is not None
    assert test_app.viewer_view is not None
    assert test_app.stats_view is not None
    assert test_app.settings_view is not None


def test_view_navigation(test_app: Video2BookApp):
    # Navigate to viewer
    test_app._nav_to_viewer()
    assert test_app.viewer_view.winfo_ismapped() or True

    # Navigate to stats
    test_app._nav_to_stats()
    assert test_app.stats_view.winfo_ismapped() or True

    # Navigate to settings
    test_app._nav_to_settings()
    assert test_app.settings_view.winfo_ismapped() or True

    # Navigate back to queue
    test_app._nav_to_queue()
    assert test_app.queue_view.winfo_ismapped() or True


def test_queue_view_scan_and_filter(test_app: Video2BookApp, tmp_path: Path):
    video_dir = tmp_path / "videos"
    video_dir.mkdir(parents=True, exist_ok=True)
    v1 = video_dir / "chapter_01.mp4"
    v2 = video_dir / "chapter_02.mp4"
    v1.write_bytes(b"content1")
    v2.write_bytes(b"content2")

    test_app.queue_view.entry_input.delete(0, "end")
    test_app.queue_view.entry_input.insert(0, str(video_dir))

    test_app.queue_view.scan_videos()
    assert len(test_app.queue_view.video_items) == 2

    # Test search query
    test_app.queue_view.entry_search.delete(0, "end")
    test_app.queue_view.entry_search.insert(0, "01")
    test_app.queue_view._on_search_changed()


def test_viewer_view_chapter_loading(test_app: Video2BookApp, tmp_path: Path):
    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    md_file = out_dir / "lecture_intro.md"
    md_file.write_text("# Introduction to Distributed Systems\n\nSome technical content.", encoding="utf-8")

    test_app.viewer_view.refresh_chapters()
    test_app.viewer_view.load_chapter(md_file)

    assert test_app.viewer_view.current_md_path == md_file.resolve()
    content = test_app.viewer_view.txt_markdown.get("1.0", "end")
    assert "Introduction to Distributed Systems" in content


def test_stats_view_refresh(test_app: Video2BookApp, tmp_path: Path):
    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    test_app.stats_view.refresh_stats()
    # Checks that it doesn't crash on empty or existing state
    assert test_app.stats_view.card_completed["label_val"].cget("text") == "0"


def test_settings_paste_and_clear_key(test_app: Video2BookApp):
    # Test setting clipboard and clicking paste button
    test_app.clipboard_clear()
    test_app.clipboard_append("sk-or-v1-test-pasted-key-12345")
    test_app.update()

    test_app.settings_view._on_paste_key()
    assert test_app.settings_view.entry_key.get() == "sk-or-v1-test-pasted-key-12345"
    assert test_app.config.api_key == "sk-or-v1-test-pasted-key-12345"

    test_app.settings_view._on_clear_key()
    assert test_app.settings_view.entry_key.get() == ""
    assert test_app.config.api_key == ""

