"""Persistent configuration management for video2book Desktop GUI."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path.home() / ".video2book" / "gui_config.json"


@dataclass
class GUIConfig:
    """User preferences and session configuration for the GUI."""

    input_dir: str = ""
    output_dir: str = "./output"
    api_key: str = ""
    model: str = "google/gemini-2.5-flash"
    extensions: str = "mp4,mkv,mov,webm,avi"
    poll_interval: float = 5.0
    theme: str = "Dark"  # "Dark", "Light", or "System"
    dry_run: bool = False
    force: bool = False
    reset_failed: bool = False
    custom_system_instruction: str = ""
    custom_user_prompt: str = ""
    save_api_key: bool = True
    auto_generate_html: bool = True
    auto_generate_pdf: bool = True
    standalone_html: bool = False
    translate_persian: bool = True
    _loaded_path: Optional[Path] = field(default=None, repr=False, compare=False)

    @classmethod
    def get_default_path(cls) -> Path:
        """Get standard config file location."""
        return DEFAULT_CONFIG_PATH

    @classmethod
    def load(cls, path: Optional[Path] = None) -> GUIConfig:
        """Load configuration from disk, falling back to defaults."""
        target = path or cls.get_default_path()
        if not target.exists():
            cfg = cls()
            cfg._loaded_path = target
            return cfg

        try:
            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Filter only known fields
            valid_keys = set(cls.__annotations__.keys()) - {"_loaded_path"}
            filtered = {k: v for k, v in data.items() if k in valid_keys}
            cfg = cls(**filtered)
            cfg._loaded_path = target
            return cfg
        except Exception as e:
            logger.warning(f"Failed to load GUI config from {target}: {e}")
            cfg = cls()
            cfg._loaded_path = target
            return cfg

    def save(self, path: Optional[Path] = None) -> None:
        """Persist configuration to disk."""
        target = path or self._loaded_path or self.get_default_path()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            data = asdict(self)
            data.pop("_loaded_path", None)
            if not self.save_api_key:
                data["api_key"] = ""
            with open(target, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self._loaded_path = target
        except Exception as e:
            logger.error(f"Failed to save GUI config to {target}: {e}")
