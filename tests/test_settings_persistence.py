"""Tests for settings persistence and UI-to-config synchronization."""

from pathlib import Path
import json
import pytest

from video2book.gui.app import Video2BookApp
from video2book.gui.config import GUIConfig


def test_guiconfig_loaded_path_preservation(tmp_path: Path):
    """Test that GUIConfig records loaded_path and saves back to it."""
    cfg_file = tmp_path / "custom_config.json"
    cfg = GUIConfig(input_dir="init_in", output_dir="init_out", api_key="sk-test-123")
    cfg.save(cfg_file)

    loaded = GUIConfig.load(cfg_file)
    assert loaded._loaded_path == cfg_file
    assert loaded.input_dir == "init_in"

    # Modify and save without specifying path
    loaded.input_dir = "updated_in"
    loaded.save()

    # Verify updated content on disk
    reloaded = GUIConfig.load(cfg_file)
    assert reloaded.input_dir == "updated_in"


def test_app_save_current_settings_syncs_entries(tmp_path: Path):
    """Test that app.save_current_settings() pulls live values from UI entries and persists them."""
    cfg_file = tmp_path / "test_app_sync.json"
    cfg = GUIConfig(input_dir="old_in", output_dir="old_out", api_key="old_key")
    cfg.save(cfg_file)

    app = Video2BookApp(config_path=cfg_file)
    app.withdraw()

    try:
        # User changes input directory in QueueView entry
        app.queue_view.entry_input.delete(0, "end")
        app.queue_view.entry_input.insert(0, str(tmp_path / "new_videos"))

        # User changes output directory in QueueView entry
        app.queue_view.entry_output.delete(0, "end")
        app.queue_view.entry_output.insert(0, str(tmp_path / "new_output"))

        # User toggles dry_run
        app.queue_view.switch_dry_run.select()

        # User enters API key in SettingsView entry
        app.settings_view.entry_key.delete(0, "end")
        app.settings_view.entry_key.insert(0, "sk-or-v1-persistent-key-999")

        # Save settings
        app.save_current_settings()

        # Check in-memory config
        assert app.config.input_dir == str(tmp_path / "new_videos")
        assert app.config.output_dir == str(tmp_path / "new_output")
        assert app.config.dry_run is True
        assert app.config.api_key == "sk-or-v1-persistent-key-999"

        # Check badge updated
        assert "OpenRouter Key Set" in app.lbl_key_status.cget("text")

        # Check disk file
        disk_cfg = GUIConfig.load(cfg_file)
        assert disk_cfg.input_dir == str(tmp_path / "new_videos")
        assert disk_cfg.output_dir == str(tmp_path / "new_output")
        assert disk_cfg.dry_run is True
        assert disk_cfg.api_key == "sk-or-v1-persistent-key-999"
    finally:
        app.destroy()


def test_window_close_persists_settings(tmp_path: Path):
    """Test that closing window invokes save_current_settings and writes to disk."""
    cfg_file = tmp_path / "test_close_save.json"
    cfg = GUIConfig(input_dir="start_in")
    cfg.save(cfg_file)

    app = Video2BookApp(config_path=cfg_file)
    app.withdraw()

    # User modifies input without clicking save button
    app.queue_view.entry_input.delete(0, "end")
    app.queue_view.entry_input.insert(0, "persisted_on_close_dir")

    # Simulate window close
    app._on_window_close()

    disk_cfg = GUIConfig.load(cfg_file)
    assert disk_cfg.input_dir == "persisted_on_close_dir"


def test_tab_navigation_triggers_save(tmp_path: Path):
    """Test that navigating between tabs automatically syncs and saves settings."""
    cfg_file = tmp_path / "test_nav_save.json"
    cfg = GUIConfig()
    cfg.save(cfg_file)

    app = Video2BookApp(config_path=cfg_file)
    app.withdraw()

    try:
        app.settings_view.entry_key.delete(0, "end")
        app.settings_view.entry_key.insert(0, "sk-or-v1-nav-saved-key")

        # Navigate to queue tab
        app._nav_to_queue()

        disk_cfg = GUIConfig.load(cfg_file)
        assert disk_cfg.api_key == "sk-or-v1-nav-saved-key"
    finally:
        try:
            app.destroy()
        except Exception:
            pass
