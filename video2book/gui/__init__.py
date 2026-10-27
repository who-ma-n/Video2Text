"""video2book Desktop GUI package."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from video2book.gui.app import Video2BookApp


def set_dpi_awareness() -> None:
    """Enable high-DPI scaling on Windows platforms."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


def launch_gui(config_path: Optional[Path] = None) -> int:
    """Launch the video2book Desktop Studio application."""
    set_dpi_awareness()
    app = Video2BookApp(config_path=config_path)
    app.mainloop()
    return 0


def main() -> int:
    """CLI entry point for video2book-gui."""
    return launch_gui()


__all__ = ["Video2BookApp", "launch_gui", "main"]
