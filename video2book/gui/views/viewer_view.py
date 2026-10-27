"""Chapter Viewer and Figures Gallery for video2book Desktop GUI."""

from __future__ import annotations

import os
import re
import subprocess
import threading
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import customtkinter as ctk
from PIL import Image

from video2book.renderer import export_pdf_chapter, render_html_chapter

if TYPE_CHECKING:
    from video2book.gui.app import Video2BookApp


class ViewerView(ctk.CTkFrame):
    """View to preview generated textbook chapters in English and Persian (RTL) and inspect figures."""

    def __init__(self, master: Any, app: Video2BookApp):
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self.app = app

        self.current_stem: Optional[str] = None
        self.current_lang: str = "en"  # "en" or "fa"
        self.current_md_path: Optional[Path] = None
        self.base_chapters: List[str] = []
        self._image_cache: List[ctk.CTkImage] = []  # Prevent garbage collection of thumbnails

        self._build_ui()

    def _build_ui(self) -> None:
        """Construct the split-pane chapter viewer layout."""
        self.grid_columnconfigure(0, weight=0, minsize=270)  # Sidebar list
        self.grid_columnconfigure(1, weight=1)               # Content reader
        self.grid_rowconfigure(0, weight=1)

        # ---------------- Left Sidebar: Chapter List ----------------
        sidebar_frame = ctk.CTkFrame(self, corner_radius=10, width=280)
        sidebar_frame.grid(row=0, column=0, sticky="nsew", padx=(15, 8), pady=15)
        sidebar_frame.grid_columnconfigure(0, weight=1)
        sidebar_frame.grid_rowconfigure(2, weight=1)

        # Header
        lbl_list_title = ctk.CTkLabel(
            sidebar_frame,
            text="Chapters",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        lbl_list_title.grid(row=0, column=0, sticky="w", padx=15, pady=(15, 5))

        # Search box
        self.entry_filter = ctk.CTkEntry(
            sidebar_frame,
            placeholder_text="Search chapters...",
            height=28,
        )
        self.entry_filter.grid(row=1, column=0, sticky="ew", padx=10, pady=5)
        self.entry_filter.bind("<KeyRelease>", self._on_filter_changed)

        # Scrollable chapter list
        self.scroll_chapters = ctk.CTkScrollableFrame(sidebar_frame, corner_radius=6)
        self.scroll_chapters.grid(row=2, column=0, sticky="nsew", padx=10, pady=5)
        self.scroll_chapters.grid_columnconfigure(0, weight=1)

        # Refresh button
        btn_refresh = ctk.CTkButton(
            sidebar_frame,
            text="↻ Refresh Chapters",
            height=30,
            fg_color="#3A3D40",
            hover_color="#4E5256",
            command=self.refresh_chapters,
        )
        btn_refresh.grid(row=3, column=0, sticky="ew", padx=10, pady=(5, 15))

        # ---------------- Right Pane: Content Tabs ----------------
        content_frame = ctk.CTkFrame(self, corner_radius=10)
        content_frame.grid(row=0, column=1, sticky="nsew", padx=(8, 15), pady=15)
        content_frame.grid_columnconfigure(0, weight=1)
        content_frame.grid_rowconfigure(1, weight=1)

        # Action toolbar on top
        toolbar = ctk.CTkFrame(content_frame, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", padx=15, pady=(12, 5))
        toolbar.grid_columnconfigure(0, weight=1)

        self.lbl_active_chapter = ctk.CTkLabel(
            toolbar,
            text="Select a chapter from the list to preview",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.lbl_active_chapter.grid(row=0, column=0, sticky="w")

        # Language Switcher (Segmented Button: EN vs FA)
        self.seg_lang = ctk.CTkSegmentedButton(
            toolbar,
            values=["🇺🇸 English", "🇮🇷 فارسی"],
            width=170,
            command=self._on_lang_changed,
        )
        self.seg_lang.set("🇺🇸 English")
        self.seg_lang.grid(row=0, column=1, padx=4)

        # On-demand Persian translation button
        self.btn_translate_fa = ctk.CTkButton(
            toolbar,
            text="🌐 Translate FA",
            width=130,
            height=30,
            font=ctk.CTkFont(weight="bold"),
            fg_color="#D97706",
            hover_color="#B45309",
            command=self._on_translate_active_chapter,
        )
        self.btn_translate_fa.grid(row=0, column=2, padx=4)

        self.btn_open_html = ctk.CTkButton(
            toolbar,
            text="📖 Open EN Book",
            width=120,
            height=30,
            font=ctk.CTkFont(weight="bold"),
            fg_color="#2EA043",
            hover_color="#238636",
            command=self._on_open_html,
        )
        self.btn_open_html.grid(row=0, column=3, padx=4)

        self.btn_open_pdf = ctk.CTkButton(
            toolbar,
            text="📄 Open PDF",
            width=100,
            height=30,
            font=ctk.CTkFont(weight="bold"),
            fg_color="#1F6FEB",
            hover_color="#388BFD",
            command=self._on_open_pdf,
        )
        self.btn_open_pdf.grid(row=0, column=4, padx=4)

        self.btn_regenerate = ctk.CTkButton(
            toolbar,
            text="↻ Re-render",
            width=90,
            height=30,
            fg_color="#3A3D40",
            hover_color="#4E5256",
            command=self._on_regenerate_book,
        )
        self.btn_regenerate.grid(row=0, column=5, padx=4)

        self.btn_open_external = ctk.CTkButton(
            toolbar,
            text="📝 Markdown",
            width=85,
            height=30,
            fg_color="#3A3D40",
            hover_color="#4E5256",
            command=self._on_open_external,
        )
        self.btn_open_external.grid(row=0, column=6, padx=4)

        self.btn_reveal = ctk.CTkButton(
            toolbar,
            text="📁 Reveal",
            width=75,
            height=30,
            fg_color="#3A3D40",
            hover_color="#4E5256",
            command=self._on_reveal_explorer,
        )
        self.btn_reveal.grid(row=0, column=7, padx=(4, 0))

        # Tab view: Markdown Reader vs Figures Gallery
        self.tabview = ctk.CTkTabview(content_frame, corner_radius=8)
        self.tabview.grid(row=1, column=0, sticky="nsew", padx=10, pady=(5, 10))

        tab_reader = self.tabview.add("📖 Markdown Reader")
        tab_gallery = self.tabview.add("🖼 Figure Snapshots Gallery")

        # Tab 1: Reader
        tab_reader.grid_columnconfigure(0, weight=1)
        tab_reader.grid_rowconfigure(0, weight=1)

        self.txt_markdown = ctk.CTkTextbox(
            tab_reader,
            font=ctk.CTkFont(family="Consolas", size=12),
            wrap="word",
        )
        self.txt_markdown.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        self.txt_markdown.configure(state="disabled")

        # Tab 2: Gallery
        tab_gallery.grid_columnconfigure(0, weight=1)
        tab_gallery.grid_rowconfigure(0, weight=1)

        self.scroll_gallery = ctk.CTkScrollableFrame(tab_gallery, corner_radius=6)
        self.scroll_gallery.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        self.scroll_gallery.grid_columnconfigure(0, weight=1)

    # ---------------- Chapter Management ----------------

    def refresh_chapters(self) -> None:
        """Scan output folder for all chapters and group by base stem."""
        out_dir = Path(self.app.config.output_dir or "./output").resolve()
        if not out_dir.exists():
            self.base_chapters = []
        else:
            stem_set = set()
            for p in out_dir.glob("*.md"):
                name = p.stem
                if name.endswith("_fa"):
                    stem_set.add(name[:-3])
                elif name.endswith("-fa"):
                    stem_set.add(name[:-3])
                else:
                    stem_set.add(name)

            # Sort by newest mtime
            def get_mtime(stem: str) -> float:
                p_en = out_dir / f"{stem}.md"
                p_fa = out_dir / f"{stem}_fa.md"
                mtime = 0.0
                if p_en.exists():
                    mtime = max(mtime, p_en.stat().st_mtime)
                if p_fa.exists():
                    mtime = max(mtime, p_fa.stat().st_mtime)
                return mtime

            self.base_chapters = sorted(list(stem_set), key=get_mtime, reverse=True)

        self._render_chapter_list()

    def _render_chapter_list(self) -> None:
        """Render base chapter buttons in sidebar with EN and FA badges."""
        for widget in self.scroll_chapters.winfo_children():
            widget.destroy()

        query = self.entry_filter.get().lower().strip()
        matching = [s for s in self.base_chapters if query in s.lower()] if query else self.base_chapters

        if not matching:
            lbl = ctk.CTkLabel(
                self.scroll_chapters,
                text="No chapters found in\noutput directory.",
                font=ctk.CTkFont(size=11),
                text_color="#8B949E",
            )
            lbl.pack(pady=20)
            return

        out_dir = Path(self.app.config.output_dir or "./output").resolve()

        for stem in matching:
            is_active = (self.current_stem == stem)

            has_en = (out_dir / f"{stem}.md").exists()
            has_fa = (out_dir / f"{stem}_fa.md").exists()
            has_en_html = (out_dir / f"{stem}.html").exists()
            has_en_pdf = (out_dir / f"{stem}.pdf").exists()
            has_fa_html = (out_dir / f"{stem}_fa.html").exists()
            has_fa_pdf = (out_dir / f"{stem}_fa.pdf").exists()

            badges = []
            if has_en:
                en_fmt = []
                if has_en_html:
                    en_fmt.append("HTML")
                if has_en_pdf:
                    en_fmt.append("PDF")
                fmt_str = f" ({'/'.join(en_fmt)})" if en_fmt else ""
                badges.append(f"EN{fmt_str}")
            if has_fa:
                fa_fmt = []
                if has_fa_html:
                    fa_fmt.append("HTML")
                if has_fa_pdf:
                    fa_fmt.append("PDF")
                fmt_str = f" ({'/'.join(fa_fmt)})" if fa_fmt else ""
                badges.append(f"FA{fmt_str}")

            badge_str = f" [{ ' • '.join(badges) }]" if badges else ""

            btn = ctk.CTkButton(
                self.scroll_chapters,
                text=f"{stem}{badge_str}",
                anchor="w",
                height=34,
                font=ctk.CTkFont(size=11, weight="bold" if is_active else "normal"),
                fg_color="#1F6FEB" if is_active else "#21262D",
                hover_color="#388BFD" if is_active else "#30363D",
                command=lambda target=stem: self.load_chapter(target),
            )
            btn.pack(fill="x", padx=2, pady=2)

    def _on_filter_changed(self, event: Any = None) -> None:
        self._render_chapter_list()

    def _on_lang_changed(self, val: str) -> None:
        """Handle language toggle switch."""
        if "English" in val or val.lower().startswith("en"):
            self.current_lang = "en"
        else:
            self.current_lang = "fa"

        if self.current_stem:
            self.load_chapter(self.current_stem)

    def load_chapter(self, stem: Path | str) -> None:
        """Load and display the given chapter stem in the selected language."""
        if isinstance(stem, Path):
            out_dir = stem.parent
            base_stem = stem.stem
            if base_stem.endswith("_fa"):
                base_stem = base_stem[:-3]
            elif base_stem.endswith("-fa"):
                base_stem = base_stem[:-3]
            self.current_stem = base_stem
        else:
            self.current_stem = str(stem)
            out_dir = Path(self.app.config.output_dir or "./output").resolve()

        stem = self.current_stem

        if self.current_lang == "fa":
            target_md = (out_dir / f"{stem}_fa.md").resolve()
            self.btn_open_html.configure(text="📖 Open FA Book")
            self.btn_open_pdf.configure(text="📄 Open FA PDF")
            lang_label = "🇮🇷 فارسی"
        else:
            target_md = (out_dir / f"{stem}.md").resolve()
            self.btn_open_html.configure(text="📖 Open EN Book")
            self.btn_open_pdf.configure(text="📄 Open EN PDF")
            lang_label = "🇺🇸 English"

        self.current_md_path = target_md
        self.lbl_active_chapter.configure(text=f"{stem} ({lang_label})")
        self._render_chapter_list()

        # Update Translate button styling depending on whether Persian exists
        fa_exists = (out_dir / f"{stem}_fa.md").exists()
        if fa_exists:
            self.btn_translate_fa.configure(
                text="↻ Re-translate FA",
                fg_color="#3A3D40",
                hover_color="#4E5256",
            )
        else:
            self.btn_translate_fa.configure(
                text="🌐 Translate FA",
                fg_color="#D97706",
                hover_color="#B45309",
            )

        # 1. Load Markdown content
        if target_md.exists():
            try:
                with open(target_md, "r", encoding="utf-8") as f:
                    content = f.read()

                self.txt_markdown.configure(state="normal")
                self.txt_markdown.delete("1.0", "end")
                self.txt_markdown.insert("1.0", content)
                self.txt_markdown.configure(state="disabled")
            except Exception as e:
                self.txt_markdown.configure(state="normal")
                self.txt_markdown.delete("1.0", "end")
                self.txt_markdown.insert("1.0", f"Failed to load chapter content: {e}")
                self.txt_markdown.configure(state="disabled")
        else:
            self.txt_markdown.configure(state="normal")
            self.txt_markdown.delete("1.0", "end")
            if self.current_lang == "fa":
                msg = (
                    "🇮🇷 Persian edition has not been generated yet for this chapter.\n\n"
                    "Click the '🌐 Translate FA' button in the toolbar above to generate the Persian translation, "
                    "preserving technical terms in English, and automatically compile the RTL web book and publication PDF."
                )
            else:
                msg = f"English source file not found: {target_md.name}"
            self.txt_markdown.insert("1.0", msg)
            self.txt_markdown.configure(state="disabled")

        # 2. Load Figures
        self._load_chapter_figures()

    def _load_chapter_figures(self) -> None:
        """Find and display extracted snapshot figures for this chapter."""
        for widget in self.scroll_gallery.winfo_children():
            widget.destroy()
        self._image_cache.clear()

        if not self.current_stem:
            return

        out_dir = Path(self.app.config.output_dir or "./output").resolve()
        images_dir = out_dir / "images"

        if not images_dir.exists():
            lbl_none = ctk.CTkLabel(
                self.scroll_gallery,
                text="No images directory found.",
                font=ctk.CTkFont(size=12),
                text_color="#8B949E",
            )
            lbl_none.pack(pady=30)
            return

        slug = self.current_stem
        # Matching images starting with slug or chapter name
        matching_images = sorted(list(images_dir.glob(f"{slug}_*.png")))

        if not matching_images:
            # Also check if any images in images_dir
            all_imgs = sorted(list(images_dir.glob("*.png")))
            matching_images = all_imgs if len(self.base_chapters) == 1 else []

        if not matching_images:
            lbl_empty = ctk.CTkLabel(
                self.scroll_gallery,
                text="No snapshot figures extracted for this chapter.",
                font=ctk.CTkFont(size=13),
                text_color="#8B949E",
            )
            lbl_empty.pack(pady=40)
            return

        lbl_info = ctk.CTkLabel(
            self.scroll_gallery,
            text=f"Extracted Figures ({len(matching_images)} snapshots) — Click any image to open in full size",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#58A6FF",
            anchor="w",
        )
        lbl_info.pack(fill="x", padx=10, pady=(5, 10))

        # Render grid/list of image cards
        for img_path in matching_images:
            self._render_figure_card(img_path)

    def _render_figure_card(self, img_path: Path) -> None:
        """Render a single snapshot figure card with thumbnail and metadata."""
        card = ctk.CTkFrame(self.scroll_gallery, corner_radius=8, fg_color="#21262D")
        card.pack(fill="x", padx=10, pady=6)
        card.grid_columnconfigure(1, weight=1)

        # Load thumbnail with PIL
        try:
            pil_img = Image.open(img_path)
            pil_img.thumbnail((240, 140))
            ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=pil_img.size)
            self._image_cache.append(ctk_img)

            btn_img = ctk.CTkButton(
                card,
                image=ctk_img,
                text="",
                fg_color="transparent",
                hover_color="#30363D",
                width=pil_img.size[0] + 10,
                height=pil_img.size[1] + 10,
                command=lambda p=img_path: self._open_image(p),
            )
            btn_img.grid(row=0, column=0, padx=10, pady=10)
        except Exception:
            lbl_err = ctk.CTkLabel(card, text="[Image Preview Unavailable]", text_color="#DA3633")
            lbl_err.grid(row=0, column=0, padx=10, pady=10)

        # Details
        details_frame = ctk.CTkFrame(card, fg_color="transparent")
        details_frame.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)
        details_frame.grid_columnconfigure(0, weight=1)

        # Parse timestamp from filename
        ts_match = re.search(r"(\d+h\d+m\d+s|\d+m\d+s)", img_path.name)
        ts_str = ts_match.group(1) if ts_match else "Timestamp Unknown"

        lbl_ts = ctk.CTkLabel(
            details_frame,
            text=f"⏱ Timestamp: {ts_str}",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#58A6FF",
            anchor="w",
        )
        lbl_ts.pack(fill="x", pady=(2, 2))

        lbl_name = ctk.CTkLabel(
            details_frame,
            text=f"File: {img_path.name}",
            font=ctk.CTkFont(size=11),
            text_color="#8B949E",
            anchor="w",
        )
        lbl_name.pack(fill="x", pady=(0, 4))

        btn_open = ctk.CTkButton(
            details_frame,
            text="🔍 Open Full Size Image",
            width=150,
            height=28,
            fg_color="#3A3D40",
            hover_color="#4E5256",
            font=ctk.CTkFont(size=11),
            command=lambda p=img_path: self._open_image(p),
        )
        btn_open.pack(anchor="w", pady=(4, 2))

    def _open_image(self, img_path: Path) -> None:
        """Open the image file in the default OS photo viewer."""
        try:
            if os.name == "nt":
                os.startfile(str(img_path))
            else:
                subprocess.run(["xdg-open", str(img_path)], check=False)
        except Exception as e:
            self.app.queue_view.append_log(f"Failed to open image {img_path}: {e}", level="error")

    # ---------------- On-demand Persian Translation ----------------

    def _on_translate_active_chapter(self) -> None:
        """Trigger on-demand Persian translation of the active chapter in a background thread."""
        if not self.current_stem:
            return

        out_dir = Path(self.app.config.output_dir or "./output").resolve()
        en_md_path = out_dir / f"{self.current_stem}.md"

        if not en_md_path.exists():
            self.app.queue_view.append_log(f"Cannot translate: English source {en_md_path.name} not found.", level="error")
            return

        api_key = self.app.config.api_key or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            self.app.queue_view.append_log("Cannot translate: API Key is missing. Set it in Settings.", level="error")
            return

        self.btn_translate_fa.configure(state="disabled", text="Translating...")
        stem = self.current_stem

        def _bg_translate() -> None:
            try:
                from video2book.openrouter import OpenRouterIngestor

                ingestor = OpenRouterIngestor(
                    api_key=api_key,
                    model=self.app.config.model,
                )

                def _status_cb(msg: str) -> None:
                    self.app.queue_view.append_log(f"Persian translation: {msg}", level="info")

                en_content = en_md_path.read_text(encoding="utf-8")
                self.app.queue_view.append_log(f"Starting Persian translation for {stem}...", level="info")
                fa_md = ingestor.translate_to_persian(en_content, status_callback=_status_cb)

                # 1. Save _fa.md
                fa_md_path = out_dir / f"{stem}_fa.md"
                fa_md_path.write_text(fa_md, encoding="utf-8")
                self.app.queue_view.append_log(f"✓ Saved {fa_md_path.name}", level="success")

                # 2. Compile _fa.html (RTL)
                fa_html_path = out_dir / f"{stem}_fa.html"
                render_html_chapter(
                    markdown_content=fa_md,
                    output_html_path=fa_html_path,
                    video_stem=stem,
                    embed_base64_images=getattr(self.app.config, "standalone_html", False),
                    is_rtl=True,
                    lang="fa",
                )
                self.app.queue_view.append_log(f"✓ Compiled Persian web book: {fa_html_path.name}", level="success")

                # 3. Compile _fa.pdf (RTL)
                fa_pdf_path = out_dir / f"{stem}_fa.pdf"
                pdf_res = export_pdf_chapter(fa_html_path, fa_pdf_path)
                if pdf_res:
                    self.app.queue_view.append_log(f"✓ Compiled Persian PDF book: {fa_pdf_path.name}", level="success")

                def _finish():
                    self.btn_translate_fa.configure(state="normal", text="↻ Re-translate FA", fg_color="#3A3D40", hover_color="#4E5256")
                    self.current_lang = "fa"
                    self.seg_lang.set("🇮🇷 فارسی")
                    self.refresh_chapters()
                    self.load_chapter(stem)

                if self.winfo_exists():
                    self.after(0, _finish)

            except Exception as e:
                self.app.queue_view.append_log(f"Persian translation failed: {e}", level="error")

                def _fail():
                    self.btn_translate_fa.configure(state="normal", text="🌐 Translate FA", fg_color="#D97706", hover_color="#B45309")

                if self.winfo_exists():
                    self.after(0, _fail)

        threading.Thread(target=_bg_translate, daemon=True).start()

    # ---------------- External Actions ----------------

    def _on_open_external(self) -> None:
        """Open the active Markdown file in the default text editor or Obsidian."""
        if not self.current_md_path or not self.current_md_path.exists():
            return
        try:
            if os.name == "nt":
                os.startfile(str(self.current_md_path))
            else:
                subprocess.run(["xdg-open", str(self.current_md_path)], check=False)
        except Exception as e:
            self.app.queue_view.append_log(f"Failed to open chapter: {e}", level="error")

    def _on_copy_markdown(self) -> None:
        """Copy active markdown text to system clipboard."""
        if not self.current_md_path or not self.current_md_path.exists():
            return
        text = self.txt_markdown.get("1.0", "end")
        self.clipboard_clear()
        self.clipboard_append(text)
        self.app.queue_view.append_log("Chapter Markdown copied to clipboard.", level="info")

    def _on_reveal_explorer(self) -> None:
        """Reveal active file or directory in Windows Explorer."""
        if not self.current_md_path:
            return
        out_dir = Path(self.app.config.output_dir or "./output").resolve()
        target = self.current_md_path if self.current_md_path.exists() else out_dir
        try:
            if os.name == "nt":
                if target.is_file():
                    subprocess.run(["explorer", f"/select,{str(target)}"], check=False)
                else:
                    subprocess.run(["explorer", str(target)], check=False)
            else:
                subprocess.run(["xdg-open", str(target.parent if target.is_file() else target)], check=False)
        except Exception as e:
            self.app.queue_view.append_log(f"Failed to reveal in explorer: {e}", level="error")

    def _on_open_html(self) -> None:
        """Open the interactive HTML textbook in the default browser."""
        if not self.current_stem:
            return

        out_dir = Path(self.app.config.output_dir or "./output").resolve()
        suffix = "_fa.html" if self.current_lang == "fa" else ".html"
        md_suffix = "_fa.md" if self.current_lang == "fa" else ".md"

        html_path = out_dir / f"{self.current_stem}{suffix}"
        md_path = out_dir / f"{self.current_stem}{md_suffix}"

        if not html_path.exists():
            if not md_path.exists():
                if self.current_lang == "fa":
                    self.app.queue_view.append_log(
                        "Persian edition is not generated yet. Click '🌐 Translate FA' first.",
                        level="warning",
                    )
                else:
                    self.app.queue_view.append_log(f"Markdown file {md_path.name} not found.", level="error")
                return

            self.app.queue_view.append_log(f"Compiling HTML book for {html_path.name}...", level="info")
            try:
                content = md_path.read_text(encoding="utf-8")
                render_html_chapter(
                    markdown_content=content,
                    output_html_path=html_path,
                    video_stem=self.current_stem,
                    embed_base64_images=getattr(self.app.config, "standalone_html", False),
                    is_rtl=(self.current_lang == "fa"),
                    lang=self.current_lang,
                )
                self.app.queue_view.append_log(f"Compiled {html_path.name}", level="success")
                self._render_chapter_list()
            except Exception as e:
                self.app.queue_view.append_log(f"Failed to render HTML: {e}", level="error")
                return

        try:
            webbrowser.open(f"file:///{html_path.resolve().as_posix()}")
        except Exception as e:
            self.app.queue_view.append_log(f"Failed to open browser: {e}", level="error")

    def _on_open_pdf(self) -> None:
        """Open the compiled PDF book in the system PDF reader."""
        if not self.current_stem:
            return

        out_dir = Path(self.app.config.output_dir or "./output").resolve()
        suffix = "_fa.pdf" if self.current_lang == "fa" else ".pdf"
        html_suffix = "_fa.html" if self.current_lang == "fa" else ".html"
        md_suffix = "_fa.md" if self.current_lang == "fa" else ".md"

        pdf_path = out_dir / f"{self.current_stem}{suffix}"
        html_path = out_dir / f"{self.current_stem}{html_suffix}"
        md_path = out_dir / f"{self.current_stem}{md_suffix}"

        if not pdf_path.exists():
            if not html_path.exists():
                if not md_path.exists():
                    if self.current_lang == "fa":
                        self.app.queue_view.append_log(
                            "Persian edition is not generated yet. Click '🌐 Translate FA' first.",
                            level="warning",
                        )
                    else:
                        self.app.queue_view.append_log(f"Markdown file {md_path.name} not found.", level="error")
                    return

                try:
                    content = md_path.read_text(encoding="utf-8")
                    render_html_chapter(
                        markdown_content=content,
                        output_html_path=html_path,
                        video_stem=self.current_stem,
                        embed_base64_images=getattr(self.app.config, "standalone_html", False),
                        is_rtl=(self.current_lang == "fa"),
                        lang=self.current_lang,
                    )
                except Exception as e:
                    self.app.queue_view.append_log(f"Failed to render HTML prerequisite: {e}", level="error")
                    return

            self.app.queue_view.append_log(f"Compiling PDF book via Edge/Chromium for {pdf_path.name}...", level="info")
            res = export_pdf_chapter(html_path, pdf_path)
            if not res or not pdf_path.exists():
                self.app.queue_view.append_log("PDF compilation failed or no Chromium binary found.", level="error")
                return
            self.app.queue_view.append_log(f"Compiled {pdf_path.name}", level="success")
            self._render_chapter_list()

        try:
            if os.name == "nt":
                os.startfile(str(pdf_path))
            else:
                subprocess.run(["xdg-open", str(pdf_path)], check=False)
        except Exception as e:
            self.app.queue_view.append_log(f"Failed to open PDF: {e}", level="error")

    def _on_regenerate_book(self) -> None:
        """Force re-render both HTML and PDF for the active chapter and selected language."""
        if not self.current_stem:
            return

        out_dir = Path(self.app.config.output_dir or "./output").resolve()
        suffix_html = "_fa.html" if self.current_lang == "fa" else ".html"
        suffix_pdf = "_fa.pdf" if self.current_lang == "fa" else ".pdf"
        suffix_md = "_fa.md" if self.current_lang == "fa" else ".md"

        html_path = out_dir / f"{self.current_stem}{suffix_html}"
        pdf_path = out_dir / f"{self.current_stem}{suffix_pdf}"
        md_path = out_dir / f"{self.current_stem}{suffix_md}"

        if not md_path.exists():
            self.app.queue_view.append_log(f"Cannot re-render: {md_path.name} not found.", level="error")
            return

        try:
            content = md_path.read_text(encoding="utf-8")
            self.app.queue_view.append_log(f"Re-rendering {html_path.name} and {pdf_path.name}...", level="info")
            render_html_chapter(
                markdown_content=content,
                output_html_path=html_path,
                video_stem=self.current_stem,
                embed_base64_images=getattr(self.app.config, "standalone_html", False),
                is_rtl=(self.current_lang == "fa"),
                lang=self.current_lang,
            )
            export_pdf_chapter(html_path, pdf_path)

            self.app.queue_view.append_log(f"✓ Re-rendered {html_path.name} and {pdf_path.name}", level="success")
            self._render_chapter_list()
        except Exception as e:
            self.app.queue_view.append_log(f"Re-render failed: {e}", level="error")
