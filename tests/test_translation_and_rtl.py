"""Tests for Persian Translation and RTL Web/PDF Book Generation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from video2book.gui.app import Video2BookApp
from video2book.gui.config import GUIConfig
from video2book.openrouter import (
    PERSIAN_TRANSLATION_SYSTEM_PROMPT,
    OpenRouterIngestor,
)
from video2book.renderer import render_html_chapter


def test_persian_system_prompt_constraints() -> None:
    """Ensure system prompt contains strict academic instructions and technical term preservation."""
    assert "Persian" in PERSIAN_TRANSLATION_SYSTEM_PROMPT or "Farsi" in PERSIAN_TRANSLATION_SYSTEM_PROMPT
    assert "English" in PERSIAN_TRANSLATION_SYSTEM_PROMPT
    # Must instruct keeping technical CS jargon, code, math in English
    assert "technical terms" in PERSIAN_TRANSLATION_SYSTEM_PROMPT.lower()
    assert "code blocks" in PERSIAN_TRANSLATION_SYSTEM_PROMPT.lower()
    assert "latex" in PERSIAN_TRANSLATION_SYSTEM_PROMPT.lower()


def test_translate_to_persian_mock_api() -> None:
    """Test translate_to_persian dispatches to OpenRouter chat completion with proper payload."""
    ingestor = OpenRouterIngestor(api_key="sk-or-v1-mockkey", model="google/gemini-2.5-flash")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "# فصل ۱: مبانی Python Development Environment\n\nمحیط توسعه یا Development Environment شامل...",
                }
            }
        ]
    }

    status_messages = []

    def status_callback(msg: str) -> None:
        status_messages.append(msg)

    with patch("httpx.Client.post", return_value=mock_response) as mock_post:
        result = ingestor.translate_to_persian(
            markdown_content="# Chapter 1: Foundations of Python Development Environment",
            status_callback=status_callback,
        )

        assert mock_post.called
        call_kwargs = mock_post.call_args[1]
        payload = call_kwargs["json"]
        assert payload["model"] == "google/gemini-2.5-flash"
        assert payload["temperature"] == 0.2
        assert len(payload["messages"]) == 2
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][0]["content"] == PERSIAN_TRANSLATION_SYSTEM_PROMPT
        assert "Chapter 1" in payload["messages"][1]["content"]

        assert "مبانی Python Development Environment" in result
        assert len(status_messages) > 0


def test_render_html_persian_rtl(tmp_path: Path) -> None:
    """Test rendering HTML with RTL layout, Vazirmatn font, and LTR code/math isolation."""
    md_content = """# مبانی Virtual Environment در Python

محیط‌های ایزوله شده یا Virtual Environments به توسعه‌دهنده اجازه می‌دهند وابستگی‌ها را مدیریت کند:

```python
import sys
print(sys.executable)
```

فرمول ریاضی به صورت $R_{target} \\equiv R_{local}$ محاسبه می‌شود.

![ساختار دایرکتوری](images/01_intro_00m10s.png)
*Figure: ساختار دایرکتوری در VS Code (Timestamp: 00:00:10)*
"""
    output_html = tmp_path / "chapter_01_fa.html"
    images_dir = tmp_path / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    (images_dir / "01_intro_00m10s.png").write_bytes(b"\x89PNG\r\n\x1a\nfakeimage")

    render_html_chapter(
        markdown_content=md_content,
        output_html_path=output_html,
        is_rtl=True,
        lang="fa",
    )

    assert output_html.exists()
    html_text = output_html.read_text(encoding="utf-8")

    # 1. RTL direction and lang attributes
    assert 'dir="rtl"' in html_text
    assert 'lang="fa"' in html_text

    # 2. Persian Font (Vazirmatn)
    assert "Vazirmatn" in html_text

    # 3. Localized Persian UI elements
    assert "فهرست مطالب" in html_text  # Table of contents
    assert "دقیقه مطالعه" in html_text   # Reading time
    assert "تصویر درس" in html_text    # Figure label
    assert "کپی" in html_text          # Copy code button

    # 4. Strict LTR protection for code and math
    assert "direction: ltr !important;" in html_text
    assert "unicode-bidi: isolate;" in html_text


def test_gui_config_persian_setting(tmp_path: Path) -> None:
    """Test GUIConfig default and serialization for translate_persian option."""
    cfg_file = tmp_path / "config.json"
    cfg = GUIConfig()
    assert cfg.translate_persian is True

    cfg.translate_persian = False
    cfg.save(cfg_file)

    cfg_loaded = GUIConfig.load(cfg_file)
    assert cfg_loaded.translate_persian is False


def test_viewer_view_persian_toggle(test_app: Video2BookApp, tmp_path: Path) -> None:
    """Test switching languages in the GUI viewer between English and Persian."""
    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    test_app.config.output_dir = str(out_dir)

    en_md = out_dir / "01_intro.md"
    en_md.write_text("# Intro in English\n\nContent EN", encoding="utf-8")

    fa_md = out_dir / "01_intro_fa.md"
    fa_md.write_text("# مقدمه به زبان فارسی\n\nContent FA", encoding="utf-8")

    test_app.viewer_view.refresh_chapters()
    assert "01_intro" in test_app.viewer_view.base_chapters

    # Load chapter in English
    test_app.viewer_view.load_chapter("01_intro")
    assert test_app.viewer_view.current_lang == "en"
    assert test_app.viewer_view.current_md_path == en_md.resolve()
    assert "Intro in English" in test_app.viewer_view.txt_markdown.get("1.0", "end")

    # Toggle to Persian
    test_app.viewer_view._on_lang_changed("🇮🇷 فارسی")
    assert test_app.viewer_view.current_lang == "fa"
    assert test_app.viewer_view.current_md_path == fa_md.resolve()
    assert "مقدمه به زبان فارسی" in test_app.viewer_view.txt_markdown.get("1.0", "end")

    # Toggle back to English
    test_app.viewer_view._on_lang_changed("🇺🇸 English")
    assert test_app.viewer_view.current_lang == "en"
    assert test_app.viewer_view.current_md_path == en_md.resolve()
    assert "Intro in English" in test_app.viewer_view.txt_markdown.get("1.0", "end")
