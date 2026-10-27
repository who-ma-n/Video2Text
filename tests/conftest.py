"""Shared pytest fixtures for video2book test suite."""

from __future__ import annotations

from pathlib import Path
import pytest

from unittest.mock import patch

from video2book.gui.app import Video2BookApp
from video2book.gui.config import GUIConfig


@pytest.fixture(autouse=True, scope="session")
def isolate_gui_config(tmp_path_factory: pytest.TempPathFactory):
    """Ensure all tests use an isolated temporary config path instead of user's home config."""
    isolated_dir = tmp_path_factory.mktemp("isolated_test_config")
    isolated_file = isolated_dir / "gui_config.json"
    with patch.object(GUIConfig, "get_default_path", return_value=isolated_file):
        yield


@pytest.fixture(scope="session")
def test_app(tmp_path_factory: pytest.TempPathFactory):
    """Provide a headless instance of Video2BookApp for GUI tests."""
    tmp_path = tmp_path_factory.mktemp("gui_test_shared")
    cfg_file = tmp_path / "gui_config.json"
    cfg = GUIConfig(
        input_dir=str(tmp_path / "videos"),
        output_dir=str(tmp_path / "output"),
    )
    cfg.save(cfg_file)

    app = Video2BookApp(config_path=cfg_file)
    app.withdraw()  # Keep window hidden during test
    yield app
    try:
        app.destroy()
    except Exception:
        pass
