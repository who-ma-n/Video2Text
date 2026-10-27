"""Book and PDF Rendering Engine for video2book.

Transforms raw Markdown and extracted video snapshot figures into:
1. Publication-grade interactive web textbooks (.html) with figure cards,
   reading metrics, sticky Table of Contents, KaTeX math, and light/dark theme.
2. High-resolution print-ready PDF textbooks (.pdf) compiled headlessly
   via Microsoft Edge or Chromium.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from markdown_it import MarkdownIt

logger = logging.getLogger("video2book.renderer")


def find_headless_chromium_binary() -> Optional[str]:
    """Find an available Microsoft Edge or Chromium/Chrome binary for headless PDF printing.

    Checks standard installation paths on Windows, macOS, and Linux,
    as well as system PATH.
    """
    # 1. Windows common locations
    candidate_paths = [
        # Microsoft Edge
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / r"Microsoft\Edge\Application\msedge.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / r"Microsoft\Edge\Application\msedge.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / r"Microsoft\Edge\Application\msedge.exe",
        # Google Chrome
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / r"Google\Chrome\Application\chrome.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / r"Google\Chrome\Application\chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / r"Google\Chrome\Application\chrome.exe",
    ]

    for p in candidate_paths:
        if p and p.exists() and p.is_file():
            return str(p)

    # 2. Check system PATH
    for name in ("msedge", "edge", "google-chrome", "chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found

    # 3. macOS applications
    mac_paths = [
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    for mp in mac_paths:
        if Path(mp).exists():
            return mp

    return None


def image_to_base64_data_url(image_path: Path) -> str:
    """Read a local image file and encode as a Base64 data URL."""
    mime, _ = mimetypes.guess_type(str(image_path))
    if not mime:
        mime = "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    return f"data:{mime};base64,{encoded}"


def preprocess_markdown_admonitions(markdown_content: str) -> str:
    """Convert GitHub-style alerts (> [!NOTE]) into styled HTML admonition cards."""
    callout_pattern = re.compile(
        r'>\s*\[!(NOTE|WARNING|TIP|IMPORTANT|CAUTION)\]\s*\n((?:>.*(?:\n|$))*)',
        re.MULTILINE
    )

    icons = {
        "NOTE": "ℹ️",
        "TIP": "💡",
        "IMPORTANT": "📌",
        "WARNING": "⚠️",
        "CAUTION": "🛑",
    }

    def replace_callout(match: re.Match) -> str:
        c_type = match.group(1).upper()
        raw_body = match.group(2)
        # Strip leading '> ' from each line
        body_lines = [re.sub(r'^>\s?', '', line) for line in raw_body.splitlines()]
        body_text = "\n".join(body_lines).strip()
        icon = icons.get(c_type, "ℹ️")

        return (
            f'\n<div class="admonition admonition-{c_type.lower()}">\n'
            f'  <div class="admonition-header">\n'
            f'    <span class="admonition-icon">{icon}</span>\n'
            f'    <span class="admonition-tag">{c_type}</span>\n'
            f'  </div>\n'
            f'  <div class="admonition-content">\n\n'
            f'{body_text}\n\n'
            f'  </div>\n'
            f'</div>\n'
        )

    return callout_pattern.sub(replace_callout, markdown_content)


def preprocess_markdown_figures(
    markdown_content: str,
    base_dir: Path,
    embed_base64: bool = False,
) -> str:
    """Format markdown image links and timestamp figure captions into rich <figure> cards.

    Matches pattern:
    ![Alt text](path/to/image.png)
    *Figure: Caption text (Timestamp: HH:MM:SS)*
    """
    # 1. Match images paired with *Figure: ... (Timestamp: ...)* caption
    figure_pattern = re.compile(
        r'!\[(.*?)\]\((.*?)\)\s*\n\s*\*Figure:\s*(.*?)\s*\(Timestamp:\s*(.*?)\)\*',
        re.MULTILINE
    )

    def replace_figure(match: re.Match) -> str:
        alt = match.group(1).strip()
        src = match.group(2).strip()
        caption = match.group(3).strip()
        timestamp = match.group(4).strip()

        img_src = src
        if embed_base64:
            resolved_img = (base_dir / src).resolve()
            if resolved_img.exists():
                img_src = image_to_base64_data_url(resolved_img)

        return (
            f'\n<figure class="book-figure">\n'
            f'  <div class="figure-img-container" onclick="openLightbox(\'{img_src}\', \'{caption}\')">\n'
            f'    <img src="{img_src}" alt="{alt}" loading="lazy" />\n'
            f'    <span class="zoom-badge">🔍 Click to inspect full resolution</span>\n'
            f'  </div>\n'
            f'  <figcaption>\n'
            f'    <span class="ts-pill">⏱ {timestamp}</span>\n'
            f'    <span class="caption-title">{caption}</span>\n'
            f'  </figcaption>\n'
            f'</figure>\n'
        )

    processed = figure_pattern.sub(replace_figure, markdown_content)

    # 2. Match standalone images not already caught by figure pattern
    standalone_img_pattern = re.compile(r'!\[(.*?)\]\((.*?)\)', re.MULTILINE)

    def replace_standalone(match: re.Match) -> str:
        alt = match.group(1).strip()
        src = match.group(2).strip()

        # If already inside an HTML tag, skip
        start = match.start()
        prefix = processed[max(0, start - 20):start]
        if "<figure" in prefix or "src=" in prefix:
            return match.group(0)

        img_src = src
        if embed_base64:
            resolved_img = (base_dir / src).resolve()
            if resolved_img.exists():
                img_src = image_to_base64_data_url(resolved_img)

        return (
            f'\n<figure class="book-figure standalone-figure">\n'
            f'  <div class="figure-img-container" onclick="openLightbox(\'{img_src}\', \'{alt}\')">\n'
            f'    <img src="{img_src}" alt="{alt}" loading="lazy" />\n'
            f'  </div>\n'
            f'  <figcaption><span class="caption-title">{alt}</span></figcaption>\n'
            f'</figure>\n'
        )

    processed = standalone_img_pattern.sub(replace_standalone, processed)
    return processed


def extract_toc_and_add_heading_ids(
    markdown_content: str,
) -> Tuple[str, List[Dict[str, Any]]]:
    """Inject anchor IDs into headings (## and ###) and construct Table of Contents."""
    toc_items: List[Dict[str, Any]] = []
    used_slugs: set[str] = set()

    def slugify(text: str) -> str:
        slug = re.sub(r'[^\w\s-]', '', text.lower()).strip()
        slug = re.sub(r'[\s_]+', '-', slug)
        if not slug:
            slug = "section"
        orig_slug = slug
        counter = 1
        while slug in used_slugs:
            slug = f"{orig_slug}-{counter}"
            counter += 1
        used_slugs.add(slug)
        return slug

    new_lines: List[str] = []
    heading_pattern = re.compile(r'^(#{2,3})\s+(.+)$')

    for line in markdown_content.splitlines():
        match = heading_pattern.match(line)
        if match:
            level = len(match.group(1))
            heading_text = match.group(2).strip()
            # Clean formatting tags if present
            clean_title = re.sub(r'[\*_`]', '', heading_text)
            slug = slugify(clean_title)

            toc_items.append({
                "level": level,
                "title": clean_title,
                "slug": slug,
            })

            # Replace markdown line with HTML heading having an explicit ID
            new_lines.append(f'<h{level} id="{slug}">{heading_text}</h{level}>')
        else:
            new_lines.append(line)

    return "\n".join(new_lines), toc_items


def render_html_chapter(
    markdown_content: str,
    output_html_path: Path,
    title: Optional[str] = None,
    video_stem: Optional[str] = None,
    embed_base64_images: bool = False,
    theme: str = "light",
    is_rtl: Optional[bool] = None,
    lang: str = "en",
) -> Path:
    """Compile Markdown chapter into a publication-grade interactive HTML textbook.

    Args:
        markdown_content: Raw or processed Markdown text.
        output_html_path: File path to save the HTML output.
        title: Optional chapter title (defaults to first H1).
        video_stem: Optional source video name for metadata.
        embed_base64_images: If True, encodes all PNG snapshots as Base64 data URLs.
        theme: Initial theme ('light' or 'dark').
        is_rtl: Force Right-to-Left layout (defaults to auto-detection from filename or lang).
        lang: Language code ('en', 'fa', etc.).

    Returns:
        Path to the saved HTML document.
    """
    output_html_path = Path(output_html_path).resolve()
    base_dir = output_html_path.parent

    # Auto-detect Persian / RTL
    stem_lower = output_html_path.stem.lower()
    if is_rtl is None:
        if lang == "fa" or stem_lower.endswith("_fa") or stem_lower.endswith("-fa"):
            is_rtl = True
            lang = "fa"
        else:
            is_rtl = False

    # 1. Extract chapter title
    chapter_title = title
    if not chapter_title:
        title_match = re.search(r"^#\s+(.+)$", markdown_content, re.MULTILINE)
        if title_match:
            chapter_title = title_match.group(1).strip()
        else:
            chapter_title = video_stem or output_html_path.stem

    # Remove the first H1 from markdown body to avoid duplicate title with hero banner
    body_md = re.sub(r"^#\s+.+$", "", markdown_content, count=1, flags=re.MULTILINE)

    # 2. Preprocess figures & callout admonitions
    processed = preprocess_markdown_admonitions(body_md)
    processed = preprocess_markdown_figures(
        processed,
        base_dir=base_dir,
        embed_base64=embed_base64_images,
    )

    # 3. Extract Headings and build Table of Contents
    processed, toc_items = extract_toc_and_add_heading_ids(processed)

    # 4. Render Markdown via MarkdownIt
    parser = MarkdownIt("commonmark").enable("table")
    rendered_body = parser.render(processed)

    # 5. Calculate chapter metrics
    words = len(re.findall(r'\b\w+\b', markdown_content))
    reading_time_min = max(1, round(words / 200))
    figures_count = len(re.findall(r'<figure class="book-figure', processed))

    # 6. Build Table of Contents HTML
    toc_links_html = ""
    for item in toc_items:
        indent_class = "toc-level-3" if item["level"] == 3 else "toc-level-2"
        toc_links_html += (
            f'<li class="{indent_class}">'
            f'<a href="#{item["slug"]}" class="toc-link">{item["title"]}</a>'
            f'</li>\n'
        )

    # 7. Assemble full HTML document
    html_document = _build_html_template(
        title=chapter_title,
        body_content=rendered_body,
        toc_html=toc_links_html,
        word_count=words,
        reading_time_min=reading_time_min,
        figures_count=figures_count,
        video_name=video_stem or output_html_path.stem,
        default_theme=theme,
        is_rtl=is_rtl,
        lang=lang,
    )

    output_html_path.parent.mkdir(parents=True, exist_ok=True)
    output_html_path.write_text(html_document, encoding="utf-8")
    logger.info(f"Rendered interactive HTML textbook: {output_html_path}")
    return output_html_path


def export_pdf_chapter(
    html_path: Path,
    output_pdf_path: Path,
    timeout: float = 120.0,
) -> Optional[Path]:
    """Headlessly compile an HTML chapter to a publication-ready PDF using Edge/Chromium.

    Args:
        html_path: Path to the input HTML file.
        output_pdf_path: Path where the resulting PDF will be saved.
        timeout: Subprocess timeout in seconds.

    Returns:
        Path to the generated PDF, or None if no Chromium binary is found.
    """
    html_path = Path(html_path).resolve()
    output_pdf_path = Path(output_pdf_path).resolve()

    if not html_path.exists():
        raise FileNotFoundError(f"Source HTML file does not exist: {html_path}")

    browser_bin = find_headless_chromium_binary()
    if not browser_bin:
        logger.warning(
            "Microsoft Edge or Chromium binary not found. Skipping automated PDF generation. "
            "You can print to PDF directly from your browser."
        )
        return None

    output_pdf_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        browser_bin,
        "--headless",
        "--disable-gpu",
        "--no-pdf-header-footer",
        "--disable-background-networking",
        "--run-all-compositor-stages-before-draw",
        f"--print-to-pdf={str(output_pdf_path)}",
        str(html_path),
    ]

    logger.info(f"Compiling PDF via {Path(browser_bin).name} -> {output_pdf_path.name}...")
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
        if output_pdf_path.exists() and output_pdf_path.stat().st_size > 0:
            size_mb = output_pdf_path.stat().st_size / (1024 * 1024)
            logger.info(f"PDF successfully compiled: {output_pdf_path} ({size_mb:.2f} MB)")
            return output_pdf_path
        else:
            logger.warning(f"Headless PDF creation finished but output file is missing or empty. Stderr: {proc.stderr}")
            return None
    except Exception as e:
        logger.warning(f"Failed to generate PDF via headless browser: {e}")
        return None


def _build_html_template(
    title: str,
    body_content: str,
    toc_html: str,
    word_count: int,
    reading_time_min: int,
    figures_count: int,
    video_name: str,
    default_theme: str = "light",
    is_rtl: bool = False,
    lang: str = "en",
) -> str:
    """Construct complete HTML template with responsive layout, KaTeX, RTL support, and styles."""
    if is_rtl:
        font_link = '<link href="https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;500;600;700;800&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">'
        font_sans = "'Vazirmatn', 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Tahoma, sans-serif"
        str_brand = "کتاب‌خوان video2book"
        str_theme = "🌓 پوسته"
        str_print = "🖨 چاپ / PDF"
        str_toc = "فهرست مطالب"
        str_badge = "فصل کتاب آموزشی ویدیو"
        str_read_time = f"{reading_time_min} دقیقه مطالعه"
        str_word_count = f"{word_count:,} کلمه"
        str_figures = f"{figures_count} تصویر درس"
        str_copy = "کپی"
        str_copied = "کپی شد!"
    else:
        font_link = '<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">'
        font_sans = "'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
        str_brand = "video2book Reader"
        str_theme = "🌓 Theme"
        str_print = "🖨 Print / PDF"
        str_toc = "Contents"
        str_badge = "Video Lecture Chapter"
        str_read_time = f"{reading_time_min} min read"
        str_word_count = f"{word_count:,} words"
        str_figures = f"{figures_count} lecture figures"
        str_copy = "Copy"
        str_copied = "Copied!"

    dir_attr = 'dir="rtl"' if is_rtl else 'dir="ltr"'

    return f"""<!DOCTYPE html>
<html lang="{lang}" {dir_attr} data-theme="{default_theme}">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} | video2book</title>

  <!-- KaTeX Math Rendering -->
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.css">
  <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.js"></script>
  <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/contrib/auto-render.min.js"
          onload="renderMathInElement(document.body, {{
            delimiters: [
              {{left: '$$', right: '$$', display: true}},
              {{left: '$', right: '$', display: false}}
            ],
            throwOnError: false
          }});"></script>

  <!-- Typography: Modern System & Web Fonts -->
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  {font_link}

  <style>
    :root {{
      --bg-page: #f8fafc;
      --bg-surface: #ffffff;
      --bg-card: #ffffff;
      --text-main: #0f172a;
      --text-muted: #64748b;
      --accent: #4f46e5;
      --accent-hover: #4338ca;
      --accent-soft: #eef2ff;
      --border: #e2e8f0;
      --border-subtle: #f1f5f9;
      --code-bg: #0f172a;
      --code-text: #e2e8f0;
      --shadow-sm: 0 1px 3px rgba(0,0,0,0.05);
      --shadow-md: 0 4px 20px -2px rgba(0,0,0,0.06);
      --shadow-lg: 0 10px 30px -4px rgba(79, 70, 229, 0.2);
      --font-sans: {font_sans};
      --font-mono: 'JetBrains Mono', Consolas, "Courier New", monospace;
    }}

    [data-theme="dark"] {{
      --bg-page: #0b0f19;
      --bg-surface: #131b2e;
      --bg-card: #182238;
      --text-main: #f1f5f9;
      --text-muted: #94a3b8;
      --accent: #6366f1;
      --accent-hover: #818cf8;
      --accent-soft: rgba(99, 102, 241, 0.15);
      --border: #243049;
      --border-subtle: #1c273e;
      --code-bg: #070b14;
      --code-text: #cbd5e1;
      --shadow-sm: 0 1px 3px rgba(0,0,0,0.3);
      --shadow-md: 0 4px 24px -2px rgba(0,0,0,0.4);
      --shadow-lg: 0 10px 30px -4px rgba(0, 0, 0, 0.5);
    }}

    * {{ box-sizing: border-box; margin: 0; padding: 0; }}

    html {{
      scroll-behavior: smooth;
    }}

    body {{
      background-color: var(--bg-page);
      color: var(--text-main);
      font-family: var(--font-sans);
      font-size: 16px;
      line-height: 1.75;
      letter-spacing: -0.01em;
      transition: background-color 0.2s, color 0.2s;
    }}

    /* Top Sticky Navigation Bar */
    .top-navbar {{
      position: sticky;
      top: 0;
      z-index: 100;
      background: rgba(255, 255, 255, 0.88);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
      border-bottom: 1px solid var(--border);
      padding: 12px 28px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      transition: background 0.2s, border-color 0.2s;
    }}
    [data-theme="dark"] .top-navbar {{
      background: rgba(19, 27, 46, 0.88);
    }}

    .nav-brand {{
      display: flex;
      align-items: center;
      gap: 10px;
      font-weight: 800;
      font-size: 15px;
      color: var(--accent);
      text-decoration: none;
    }}
    .nav-brand-icon {{
      font-size: 18px;
    }}

    .nav-actions {{
      display: flex;
      gap: 10px;
      align-items: center;
    }}

    .nav-btn {{
      background: var(--bg-surface);
      border: 1px solid var(--border);
      color: var(--text-main);
      padding: 7px 14px;
      border-radius: 8px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: all 0.15s ease;
      font-family: inherit;
    }}
    .nav-btn:hover {{
      border-color: var(--accent);
      color: var(--accent);
      transform: translateY(-1px);
    }}
    .nav-btn-primary {{
      background: var(--accent);
      color: white;
      border-color: var(--accent);
    }}
    .nav-btn-primary:hover {{
      background: var(--accent-hover);
      color: white;
      border-color: var(--accent-hover);
    }}

    /* Reading Progress Bar */
    .reading-progress-bar {{
      position: fixed;
      top: 0;
      left: 0;
      height: 3px;
      background: linear-gradient(90deg, var(--accent) 0%, #a855f7 100%);
      width: 0%;
      z-index: 101;
      transition: width 0.1s;
    }}

    /* Main Grid Layout (Sticky TOC + Reading Content) */
    .page-layout {{
      max-width: 1320px;
      margin: 0 auto;
      padding: 36px 24px 100px;
      display: grid;
      grid-template-columns: 280px minmax(0, 1fr);
      gap: 48px;
      align-items: start;
    }}

    @media (max-width: 1024px) {{
      .page-layout {{
        grid-template-columns: 1fr;
        gap: 24px;
      }}
      .toc-sidebar {{
        display: none;
      }}
    }}

    /* Sidebar Table of Contents */
    .toc-sidebar {{
      position: sticky;
      top: 76px;
      max-height: calc(100vh - 100px);
      overflow-y: auto;
      background: var(--bg-surface);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 20px 18px;
      box-shadow: var(--shadow-sm);
    }}

    .toc-title {{
      font-size: 13px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-muted);
      margin-bottom: 14px;
      display: flex;
      align-items: center;
      gap: 6px;
    }}

    .toc-list {{
      list-style: none;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }}

    .toc-link {{
      color: var(--text-muted);
      text-decoration: none;
      font-size: 13px;
      line-height: 1.45;
      display: block;
      padding: 4px 8px;
      border-radius: 6px;
      transition: all 0.15s;
    }}
    .toc-link:hover, .toc-link.active {{
      color: var(--accent);
      background: var(--accent-soft);
      font-weight: 600;
    }}
    .toc-level-3 {{
      padding-left: 16px;
      font-size: 12px;
    }}

    /* Reading Column */
    .reading-column {{
      max-width: 860px;
    }}

    /* Hero Header */
    .hero-header {{
      background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%);
      color: white;
      border-radius: 20px;
      padding: 44px 38px;
      margin-bottom: 44px;
      box-shadow: var(--shadow-lg);
    }}

    .hero-badge {{
      display: inline-block;
      background: rgba(255, 255, 255, 0.2);
      backdrop-filter: blur(8px);
      padding: 4px 12px;
      border-radius: 20px;
      font-size: 11.5px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      margin-bottom: 16px;
    }}

    .hero-title {{
      font-size: 32px;
      font-weight: 800;
      line-height: 1.25;
      margin-bottom: 22px;
      letter-spacing: -0.02em;
    }}

    .hero-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 18px;
      font-size: 13px;
      opacity: 0.95;
    }}
    .hero-meta-item {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }}

    /* Typography & Hierarchy */
    h2 {{
      font-size: 24px;
      font-weight: 800;
      color: var(--text-main);
      margin-top: 52px;
      margin-bottom: 20px;
      padding-bottom: 10px;
      border-bottom: 2px solid var(--border);
      letter-spacing: -0.02em;
    }}

    h3 {{
      font-size: 18.5px;
      font-weight: 700;
      color: var(--text-main);
      margin-top: 36px;
      margin-bottom: 14px;
      letter-spacing: -0.01em;
    }}

    p {{
      margin-bottom: 20px;
      color: var(--text-main);
      font-size: 16px;
      word-break: break-word;
    }}

    strong {{
      font-weight: 700;
      color: var(--text-main);
    }}

    ul, ol {{
      margin-bottom: 24px;
      padding-left: 28px;
    }}
    li {{
      margin-bottom: 8px;
    }}

    hr {{
      border: 0;
      height: 1px;
      background: var(--border);
      margin: 44px 0;
    }}

    /* Figures & Image Cards */
    .book-figure {{
      margin: 38px 0;
      background: var(--bg-surface);
      border: 1px solid var(--border);
      border-radius: 16px;
      overflow: hidden;
      box-shadow: var(--shadow-md);
      transition: transform 0.2s, box-shadow 0.2s;
    }}
    .book-figure:hover {{
      box-shadow: 0 8px 30px rgba(0,0,0,0.09);
    }}
    [data-theme="dark"] .book-figure:hover {{
      box-shadow: 0 8px 32px rgba(0,0,0,0.5);
    }}

    .figure-img-container {{
      position: relative;
      cursor: zoom-in;
      background: #000000;
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
    }}
    .figure-img-container img {{
      display: block;
      width: 100%;
      height: auto;
      max-height: 640px;
      object-fit: contain;
      transition: transform 0.3s ease;
    }}
    .figure-img-container:hover img {{
      transform: scale(1.015);
    }}

    .zoom-badge {{
      position: absolute;
      bottom: 12px;
      right: 12px;
      background: rgba(0, 0, 0, 0.72);
      backdrop-filter: blur(6px);
      color: white;
      font-size: 11px;
      font-weight: 600;
      padding: 5px 11px;
      border-radius: 6px;
      opacity: 0;
      transition: opacity 0.2s;
      pointer-events: none;
    }}
    .figure-img-container:hover .zoom-badge {{
      opacity: 1;
    }}

    figcaption {{
      padding: 14px 20px;
      background: var(--bg-surface);
      border-top: 1px solid var(--border);
      display: flex;
      align-items: center;
      gap: 12px;
      font-size: 13.5px;
      flex-wrap: wrap;
    }}

    .ts-pill {{
      background: var(--accent-soft);
      color: var(--accent);
      font-weight: 700;
      font-family: var(--font-mono);
      font-size: 12px;
      padding: 3px 9px;
      border-radius: 6px;
      white-space: nowrap;
    }}

    .caption-title {{
      color: var(--text-main);
      font-weight: 500;
      line-height: 1.4;
    }}

    /* Callouts / Admonitions */
    .admonition {{
      margin: 28px 0;
      border-radius: 12px;
      border-left: 4px solid var(--accent);
      background: var(--bg-surface);
      border-top: 1px solid var(--border);
      border-right: 1px solid var(--border);
      border-bottom: 1px solid var(--border);
      padding: 18px 22px;
      box-shadow: var(--shadow-sm);
    }}
    .admonition-note {{ border-left-color: #3b82f6; }}
    .admonition-warning {{ border-left-color: #f59e0b; }}
    .admonition-tip {{ border-left-color: #10b981; }}
    .admonition-important {{ border-left-color: #8b5cf6; }}
    .admonition-caution {{ border-left-color: #ef4444; }}

    .admonition-header {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 10px;
    }}
    .admonition-icon {{
      font-size: 16px;
    }}
    .admonition-tag {{
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      padding: 2px 8px;
      border-radius: 4px;
    }}
    .admonition-note .admonition-tag {{ background: #dbeafe; color: #1e40af; }}
    .admonition-warning .admonition-tag {{ background: #fef3c7; color: #92400e; }}
    .admonition-tip .admonition-tag {{ background: #d1fae5; color: #065f46; }}
    .admonition-important .admonition-tag {{ background: #ede9fe; color: #5b21b6; }}
    .admonition-caution .admonition-tag {{ background: #fee2e2; color: #991b1b; }}

    [data-theme="dark"] .admonition-note .admonition-tag {{ background: rgba(59, 130, 246, 0.2); color: #93c5fd; }}
    [data-theme="dark"] .admonition-warning .admonition-tag {{ background: rgba(245, 158, 11, 0.2); color: #fcd34d; }}
    [data-theme="dark"] .admonition-tip .admonition-tag {{ background: rgba(16, 185, 129, 0.2); color: #6ee7b7; }}
    [data-theme="dark"] .admonition-important .admonition-tag {{ background: rgba(139, 92, 246, 0.2); color: #c4b5fd; }}
    [data-theme="dark"] .admonition-caution .admonition-tag {{ background: rgba(239, 68, 68, 0.2); color: #fca5a5; }}

    .admonition-content {{
      font-size: 14.5px;
      line-height: 1.65;
    }}

    /* Code Blocks & Architecture Diagrams */
    pre {{
      position: relative;
      background: var(--code-bg);
      color: var(--code-text);
      font-family: var(--font-mono);
      font-size: 13px;
      line-height: 1.6;
      padding: 22px;
      border-radius: 12px;
      overflow-x: auto;
      margin: 26px 0;
      border: 1px solid var(--border);
    }}
    code {{
      font-family: var(--font-mono);
      font-size: 0.9em;
      background: var(--accent-soft);
      color: var(--accent);
      padding: 2px 6px;
      border-radius: 4px;
    }}
    pre code {{
      background: transparent;
      color: inherit;
      padding: 0;
    }}

    .copy-code-btn {{
      position: absolute;
      top: 10px;
      right: 10px;
      background: rgba(255, 255, 255, 0.1);
      border: 1px solid rgba(255, 255, 255, 0.2);
      color: #cbd5e1;
      padding: 4px 8px;
      border-radius: 6px;
      font-size: 11px;
      cursor: pointer;
      opacity: 0;
      transition: opacity 0.2s, background 0.2s;
    }}
    pre:hover .copy-code-btn {{
      opacity: 1;
    }}
    .copy-code-btn:hover {{
      background: rgba(255, 255, 255, 0.25);
      color: white;
    }}

    /* Tables */
    table {{
      width: 100%;
      border-collapse: separate;
      border-spacing: 0;
      margin: 30px 0;
      border: 1px solid var(--border);
      border-radius: 12px;
      overflow: hidden;
      background: var(--bg-surface);
      box-shadow: var(--shadow-sm);
    }}
    th, td {{
      padding: 13px 18px;
      text-align: left;
      font-size: 14px;
      border-bottom: 1px solid var(--border);
    }}
    th {{
      background: var(--accent-soft);
      font-weight: 700;
      color: var(--text-main);
    }}
    tr:last-child td {{
      border-bottom: none;
    }}
    tr:nth-child(even) td {{
      background: rgba(0, 0, 0, 0.015);
    }}
    [data-theme="dark"] tr:nth-child(even) td {{
      background: rgba(255, 255, 255, 0.02);
    }}

    /* Lightbox Modal */
    #lightbox {{
      display: none;
      position: fixed;
      top: 0; left: 0; right: 0; bottom: 0;
      background: rgba(0, 0, 0, 0.94);
      z-index: 1000;
      justify-content: center;
      align-items: center;
      flex-direction: column;
      cursor: zoom-out;
      padding: 20px;
    }}
    #lightbox img {{
      max-width: 94vw;
      max-height: 86vh;
      object-fit: contain;
      border-radius: 8px;
      box-shadow: 0 10px 50px rgba(0,0,0,0.8);
    }}
    #lightbox-caption {{
      color: #f1f5f9;
      margin-top: 14px;
      font-size: 14.5px;
      font-weight: 500;
      text-align: center;
      max-width: 800px;
    }}

    /* RTL & Bidirectional Adaptations */
    html[dir="rtl"] body {{
      direction: rtl;
      text-align: right;
    }}
    html[dir="rtl"] .top-navbar {{
      direction: rtl;
    }}
    html[dir="rtl"] .toc-sidebar {{
      direction: rtl;
      text-align: right;
    }}
    html[dir="rtl"] .toc-level-3 {{
      padding-right: 16px;
      padding-left: 0;
    }}
    html[dir="rtl"] .admonition {{
      border-right: 4px solid var(--accent);
      border-left: 1px solid var(--border);
      direction: rtl;
      text-align: right;
    }}
    html[dir="rtl"] .admonition-header {{
      flex-direction: row;
    }}
    html[dir="rtl"] figcaption {{
      flex-direction: row;
    }}
    html[dir="rtl"] .caption-title {{
      text-align: right;
    }}
    html[dir="rtl"] table {{
      direction: rtl;
    }}
    html[dir="rtl"] th, html[dir="rtl"] td {{
      text-align: right;
    }}

    /* STRICT LTR FOR CODE, MATH, TERMINAL & ASSETS */
    pre, code, .katex, .katex-display, .figure-img-container, .ts-pill {{
      direction: ltr !important;
      text-align: left !important;
      unicode-bidi: isolate;
    }}
    html[dir="rtl"] pre {{
      direction: ltr !important;
      text-align: left !important;
    }}
    html[dir="rtl"] .copy-code-btn {{
      left: 10px;
      right: auto;
    }}

    /* Print Styles (@media print) */
    @media print {{
      .top-navbar, .toc-sidebar, .zoom-badge, .copy-code-btn, .reading-progress-bar {{
        display: none !important;
      }}
      body {{
        background: white !important;
        color: black !important;
        font-size: 11pt !important;
        line-height: 1.6 !important;
      }}
      .page-layout {{
        display: block !important;
        padding: 0 !important;
        max-width: 100% !important;
      }}
      .reading-column {{
        max-width: 100% !important;
      }}
      .hero-header {{
        background: #2563eb !important;
        color: white !important;
        -webkit-print-color-adjust: exact;
        print-color-adjust: exact;
        border-radius: 8px !important;
        padding: 24px !important;
        margin-bottom: 24px !important;
      }}
      .book-figure {{
        break-inside: avoid;
        page-break-inside: avoid;
        border: 1px solid #ccc !important;
        box-shadow: none !important;
        margin: 20px 0 !important;
      }}
      .book-figure img {{
        max-height: 480px !important;
      }}
      h2 {{
        break-before: page;
        page-break-before: always;
        padding-top: 20px !important;
      }}
      table, pre, .admonition {{
        break-inside: avoid;
        page-break-inside: avoid;
      }}
    }}
  </style>
</head>
<body>
  <div class="reading-progress-bar" id="reading-bar"></div>

  <!-- Top Sticky Navigation -->
  <header class="top-navbar">
    <a href="#" class="nav-brand">
      <span class="nav-brand-icon">📖</span>
      <span>{str_brand}</span>
    </a>
    <div class="nav-actions">
      <button class="nav-btn" onclick="toggleTheme()" title="Toggle Light/Dark Theme">{str_theme}</button>
      <button class="nav-btn nav-btn-primary" onclick="window.print()" title="Print or Save as PDF">{str_print}</button>
    </div>
  </header>

  <!-- Main Layout -->
  <div class="page-layout">
    <!-- Sticky Table of Contents -->
    <aside class="toc-sidebar">
      <div class="toc-title">
        <span>📑</span>
        <span>{str_toc}</span>
      </div>
      <ul class="toc-list">
        {toc_html}
      </ul>
    </aside>

    <!-- Reading Content -->
    <main class="reading-column">
      <!-- Hero Banner -->
      <section class="hero-header">
        <span class="hero-badge">{str_badge}</span>
        <h1 class="hero-title">{title}</h1>
        <div class="hero-meta">
          <span class="hero-meta-item">⏱ <strong>{str_read_time}</strong></span>
          <span class="hero-meta-item">📝 <strong>{str_word_count}</strong></span>
          <span class="hero-meta-item">🖼 <strong>{str_figures}</strong></span>
          <span class="hero-meta-item">📹 <strong>{video_name}</strong></span>
        </div>
      </section>

      <!-- Chapter Body -->
      <article class="chapter-content">
        {body_content}
      </article>
    </main>
  </div>

  <!-- Fullscreen Lightbox Modal -->
  <div id="lightbox" onclick="closeLightbox()">
    <img id="lightbox-img" src="" alt="Enlarged view">
    <div id="lightbox-caption"></div>
  </div>

  <script>
    // Lightbox handlers
    function openLightbox(src, caption) {{
      const lb = document.getElementById('lightbox');
      document.getElementById('lightbox-img').src = src;
      document.getElementById('lightbox-caption').innerText = caption;
      lb.style.display = 'flex';
    }}

    function closeLightbox() {{
      document.getElementById('lightbox').style.display = 'none';
    }}

    // Theme toggler
    function toggleTheme() {{
      const current = document.documentElement.getAttribute('data-theme');
      const target = current === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', target);
      localStorage.setItem('v2b-reader-theme', target);
    }}

    // Initialize Theme
    const savedTheme = localStorage.getItem('v2b-reader-theme');
    if (savedTheme) {{
      document.documentElement.setAttribute('data-theme', savedTheme);
    }}

    // Reading progress & scrollspy for TOC
    window.addEventListener('scroll', () => {{
      const winScroll = document.documentElement.scrollTop;
      const height = document.documentElement.scrollHeight - document.documentElement.clientHeight;
      const scrolled = (winScroll / height) * 100;
      document.getElementById('reading-bar').style.width = scrolled + '%';

      // Scrollspy
      const headings = document.querySelectorAll('h2[id], h3[id]');
      const links = document.querySelectorAll('.toc-link');
      let activeId = '';
      headings.forEach(h => {{
        const rect = h.getBoundingClientRect();
        if (rect.top <= 120) {{
          activeId = h.id;
        }}
      }});
      links.forEach(link => {{
        if (link.getAttribute('href') === '#' + activeId) {{
          link.classList.add('active');
        }} else {{
          link.classList.remove('active');
        }}
      }});
    }});

    // Inject copy buttons onto code blocks
    document.querySelectorAll('pre').forEach(pre => {{
      const btn = document.createElement('button');
      btn.className = 'copy-code-btn';
      btn.innerText = '{str_copy}';
      btn.onclick = () => {{
        const code = pre.querySelector('code') ? pre.querySelector('code').innerText : pre.innerText;
        navigator.clipboard.writeText(code);
        btn.innerText = '{str_copied}';
        setTimeout(() => btn.innerText = '{str_copy}', 2000);
      }};
      pre.appendChild(btn);
    }});
  </script>
</body>
</html>
"""
