"""Unit and integration tests for video2book Book and PDF Renderer."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from video2book.renderer import (
    export_pdf_chapter,
    extract_toc_and_add_heading_ids,
    find_headless_chromium_binary,
    image_to_base64_data_url,
    preprocess_markdown_admonitions,
    preprocess_markdown_figures,
    render_html_chapter,
)


def test_preprocess_markdown_admonitions():
    md = (
        "> [!NOTE]\n"
        "> This is a note callout with important context.\n\n"
        "> [!WARNING]\n"
        "> This is a warning regarding state mutation.\n"
    )
    result = preprocess_markdown_admonitions(md)
    assert '<div class="admonition admonition-note">' in result
    assert '<span class="admonition-tag">NOTE</span>' in result
    assert "This is a note callout with important context." in result
    assert '<div class="admonition admonition-warning">' in result
    assert '<span class="admonition-tag">WARNING</span>' in result


def test_preprocess_markdown_figures(tmp_path):
    img_file = tmp_path / "slide.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4")

    md = (
        f"![Architecture Overview]({img_file.name})\n"
        f"*Figure: High-Level Architecture Overview (Timestamp: 00:04:12)*\n"
    )

    # Relative mode
    res_rel = preprocess_markdown_figures(md, base_dir=tmp_path, embed_base64=False)
    assert '<figure class="book-figure">' in res_rel
    assert f'src="{img_file.name}"' in res_rel
    assert "⏱ 00:04:12" in res_rel
    assert "High-Level Architecture Overview" in res_rel

    # Base64 embedded mode
    res_b64 = preprocess_markdown_figures(md, base_dir=tmp_path, embed_base64=True)
    assert 'src="data:image/png;base64,' in res_b64


def test_extract_toc_and_add_heading_ids():
    md = (
        "## 1. Introduction to Systems\n\n"
        "Some body text.\n\n"
        "### 1.1 Architecture & Topology\n\n"
        "More body text.\n"
    )
    new_md, toc = extract_toc_and_add_heading_ids(md)
    assert '<h2 id="1-introduction-to-systems">1. Introduction to Systems</h2>' in new_md
    assert '<h3 id="11-architecture-topology">1.1 Architecture & Topology</h3>' in new_md
    assert len(toc) == 2
    assert toc[0]["slug"] == "1-introduction-to-systems"
    assert toc[1]["slug"] == "11-architecture-topology"


def test_render_html_chapter(tmp_path):
    md_content = (
        "# Chapter: Deep Dive into Toolchains\n\n"
        "## 1. Foundation\n\n"
        "Mathematical formula: $R_{local} \\equiv R_{target}$.\n\n"
        "> [!TIP]\n"
        "> Use modern environments.\n\n"
        "```python\n"
        "def hello():\n"
        "    return 42\n"
        "```\n\n"
        "| Tool | Speed |\n"
        "|---|---|\n"
        "| Conda | Fast |\n"
    )

    out_html = tmp_path / "chapter_test.html"
    res_path = render_html_chapter(
        markdown_content=md_content,
        output_html_path=out_html,
        video_stem="lecture_01",
    )

    assert res_path.exists()
    content = res_path.read_text(encoding="utf-8")
    assert "Chapter: Deep Dive into Toolchains" in content
    assert "katex.min.js" in content
    assert "admonition-tip" in content
    assert "lecture_01" in content
    assert "reading-progress-bar" in content
    assert "@media print" in content


def test_find_headless_chromium_binary():
    # Should not crash on any OS
    bin_path = find_headless_chromium_binary()
    assert bin_path is None or isinstance(bin_path, str)


def test_export_pdf_chapter_missing_html(tmp_path):
    missing_html = tmp_path / "non_existent.html"
    out_pdf = tmp_path / "out.pdf"
    with pytest.raises(FileNotFoundError):
        export_pdf_chapter(missing_html, out_pdf)


def test_export_pdf_chapter_mock_success(tmp_path):
    dummy_html = tmp_path / "page.html"
    dummy_html.write_text("<h1>Test</h1>", encoding="utf-8")
    out_pdf = tmp_path / "page.pdf"

    # Mock finding binary and subprocess execution
    with patch("video2book.renderer.find_headless_chromium_binary", return_value="dummy_edge"), \
         patch("subprocess.run") as mock_run:
        # Simulate subprocess creating the PDF file
        def fake_run(*args, **kwargs):
            out_pdf.write_bytes(b"%PDF-1.4 dummy pdf bytes")
            class Result:
                returncode = 0
                stderr = ""
            return Result()

        mock_run.side_effect = fake_run
        res = export_pdf_chapter(dummy_html, out_pdf)
        assert res == out_pdf
        assert out_pdf.exists()
