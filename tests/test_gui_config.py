"""Unit tests for GUI configuration management."""

import json
from pathlib import Path
from video2book.gui.config import GUIConfig


def test_default_config():
    cfg = GUIConfig()
    assert cfg.model == "google/gemini-2.5-flash"
    assert cfg.output_dir == "./output"
    assert cfg.poll_interval == 5.0
    assert cfg.theme == "Dark"
    assert cfg.save_api_key is True


def test_save_and_load_config(tmp_path: Path):
    target = tmp_path / "test_gui_config.json"
    cfg = GUIConfig(
        input_dir=str(tmp_path / "videos"),
        output_dir=str(tmp_path / "out"),
        api_key="test-key-12345",
        model="gemini-2.5-pro",
        extensions="mp4,mov",
        poll_interval=2.5,
        theme="Light",
    )
    cfg.save(target)

    assert target.exists()
    loaded = GUIConfig.load(target)
    assert loaded.input_dir == str(tmp_path / "videos")
    assert loaded.output_dir == str(tmp_path / "out")
    assert loaded.api_key == "test-key-12345"
    assert loaded.model == "gemini-2.5-pro"
    assert loaded.extensions == "mp4,mov"
    assert loaded.poll_interval == 2.5
    assert loaded.theme == "Light"


def test_config_do_not_save_key(tmp_path: Path):
    target = tmp_path / "config_no_key.json"
    cfg = GUIConfig(
        api_key="secret-key",
        save_api_key=False,
    )
    cfg.save(target)

    with open(target, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["api_key"] == ""


def test_load_nonexistent_config(tmp_path: Path):
    target = tmp_path / "does_not_exist.json"
    loaded = GUIConfig.load(target)
    assert loaded.model == "google/gemini-2.5-flash"
