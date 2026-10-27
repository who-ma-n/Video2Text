"""Main Application Window for video2book Desktop GUI."""

from __future__ import annotations

import logging
import os
import queue
from pathlib import Path
from tkinter import messagebox
from typing import List, Optional

import customtkinter as ctk

from video2book.extractor import check_ffmpeg_installed
from video2book.gui.config import GUIConfig
from video2book.gui.views.queue_view import QueueView
from video2book.gui.views.settings_view import SettingsView
from video2book.gui.views.stats_view import StatsView
from video2book.gui.views.viewer_view import ViewerView
from video2book.gui.worker import PipelineWorker, WorkerMessage

logger = logging.getLogger(__name__)


class Video2BookApp(ctk.CTk):
    """Main desktop application window for video2book."""

    def __init__(self, config_path: Optional[Path] = None):
        super().__init__()

        self.config_path = config_path
        # Load configuration
        self.config = GUIConfig.load(config_path)

        # Apply theme
        ctk.set_appearance_mode(self.config.theme or "Dark")
        ctk.set_default_color_theme("blue")

        # Window configuration
        self.title("video2book — Technical Textbook Studio")
        self.geometry("1220x820")
        self.minsize(1050, 700)

        # Center on screen
        self._center_window(1220, 820)

        # Threading & Event Queue
        self.event_queue: queue.Queue = queue.Queue()
        self.worker: Optional[PipelineWorker] = None

        # Build Layout
        self._build_layout()

        # Start Event Polling loop
        self.after(50, self._poll_worker_events)

        # Protocol
        self.protocol("WM_DELETE_WINDOW", self._on_window_close)

    def _center_window(self, width: int, height: int) -> None:
        """Center the application window on screen."""
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = max(0, (sw - width) // 2)
        y = max(0, (sh - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _build_layout(self) -> None:
        """Create the responsive sidebar and content container."""
        self.grid_columnconfigure(0, weight=0, minsize=230)  # Sidebar
        self.grid_columnconfigure(1, weight=1)               # Content
        self.grid_rowconfigure(0, weight=1)

        # ---------------- Left Navigation Sidebar ----------------
        self.sidebar = ctk.CTkFrame(self, corner_radius=0, width=230, fg_color="#161B22")
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_columnconfigure(0, weight=1)
        self.sidebar.grid_rowconfigure(5, weight=1)  # Spacer pushes status to bottom

        # App Brand Header
        lbl_logo = ctk.CTkLabel(
            self.sidebar,
            text="📚 video2book",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="#58A6FF",
        )
        lbl_logo.grid(row=0, column=0, padx=20, pady=(25, 2), sticky="w")

        lbl_subtitle = ctk.CTkLabel(
            self.sidebar,
            text="Textbook & Diagram Studio",
            font=ctk.CTkFont(size=11),
            text_color="#8B949E",
        )
        lbl_subtitle.grid(row=1, column=0, padx=20, pady=(0, 25), sticky="w")

        # Navigation Buttons
        self.btn_nav_queue = self._create_nav_button("📋 Queue & Processing", 2, self._nav_to_queue)
        self.btn_nav_viewer = self._create_nav_button("📖 Chapter Viewer", 3, self._nav_to_viewer)
        self.btn_nav_stats = self._create_nav_button("📊 Analytics & State", 4, self._nav_to_stats)
        self.btn_nav_settings = self._create_nav_button("⚙️ Settings", 5, self._nav_to_settings)

        # Bottom Diagnostics Badge
        status_box = ctk.CTkFrame(self.sidebar, corner_radius=8, fg_color="#21262D")
        status_box.grid(row=6, column=0, padx=15, pady=20, sticky="ew")

        ff_ok, _ = check_ffmpeg_installed()
        ff_text = "✓ FFmpeg Ready" if ff_ok else "⚠️ FFmpeg Missing"
        ff_col = "#2EA043" if ff_ok else "#DA3633"

        lbl_ff = ctk.CTkLabel(
            status_box,
            text=ff_text,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=ff_col,
        )
        lbl_ff.pack(anchor="w", padx=12, pady=(10, 4))

        has_key = bool(self.config.api_key or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("GEMINI_API_KEY"))
        key_text = "✓ OpenRouter Key Set" if has_key else "⚠️ OpenRouter Key Missing"
        key_col = "#2EA043" if has_key else "#E3B341"

        self.lbl_key_status = ctk.CTkLabel(
            status_box,
            text=key_text,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=key_col,
        )
        self.lbl_key_status.pack(anchor="w", padx=12, pady=(2, 10))

        # ---------------- Main Content Views ----------------
        self.container = ctk.CTkFrame(self, corner_radius=0, fg_color="#0D1117")
        self.container.grid(row=0, column=1, sticky="nsew")
        self.container.grid_columnconfigure(0, weight=1)
        self.container.grid_rowconfigure(0, weight=1)

        # Initialize View Frames
        self.queue_view = QueueView(self.container, self)
        self.viewer_view = ViewerView(self.container, self)
        self.stats_view = StatsView(self.container, self)
        self.settings_view = SettingsView(self.container, self)

        # Show initial view
        self._nav_to_queue()

    def _create_nav_button(self, text: str, row: int, command: Any) -> ctk.CTkButton:
        btn = ctk.CTkButton(
            self.sidebar,
            text=text,
            anchor="w",
            height=40,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="transparent",
            hover_color="#21262D",
            command=command,
        )
        btn.grid(row=row, column=0, padx=12, pady=4, sticky="ew")
        return btn

    def _set_active_nav(self, active_btn: ctk.CTkButton) -> None:
        """Update navigation button highlighting."""
        for btn in (self.btn_nav_queue, self.btn_nav_viewer, self.btn_nav_stats, self.btn_nav_settings):
            btn.configure(fg_color="#1F6FEB" if btn == active_btn else "transparent")

    def update_key_status_badge(self) -> None:
        """Update the sidebar badge indicating whether an API key is available."""
        if not hasattr(self, "lbl_key_status"):
            return
        has_key = bool(
            self.config.api_key
            or os.environ.get("OPENROUTER_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
        )
        key_text = "✓ OpenRouter Key Set" if has_key else "⚠️ OpenRouter Key Missing"
        key_col = "#2EA043" if has_key else "#E3B341"
        try:
            self.lbl_key_status.configure(text=key_text, text_color=key_col)
        except Exception:
            pass

    def save_current_settings(self) -> None:
        """Collect current settings from all active views and persist to disk."""
        # 1. Sync from QueueView if initialized
        if hasattr(self, "queue_view") and self.queue_view is not None:
            try:
                in_val = self.queue_view.entry_input.get().strip()
                self.config.input_dir = in_val
                out_val = self.queue_view.entry_output.get().strip()
                if out_val:
                    self.config.output_dir = out_val
                self.config.dry_run = bool(self.queue_view.switch_dry_run.get())
                self.config.force = bool(self.queue_view.switch_force.get())
            except Exception as e:
                logger.debug(f"Error syncing queue view settings: {e}")

        # 2. Sync from SettingsView if initialized
        if hasattr(self, "settings_view") and self.settings_view is not None:
            try:
                self.config.api_key = self.settings_view.entry_key.get().strip()
                self.config.model = self.settings_view.menu_model.get().strip()
                self.config.extensions = self.settings_view.entry_ext.get().strip()
                try:
                    self.config.poll_interval = float(self.settings_view.entry_poll.get().strip())
                except (ValueError, TypeError):
                    pass
                self.config.custom_system_instruction = self.settings_view.txt_sys_instruction.get("1.0", "end").strip()
                self.config.custom_user_prompt = self.settings_view.txt_user_prompt.get("1.0", "end").strip()
                self.config.theme = self.settings_view.menu_theme.get()
                self.config.auto_generate_html = bool(self.settings_view.switch_auto_html.get())
                self.config.auto_generate_pdf = bool(self.settings_view.switch_auto_pdf.get())
                self.config.standalone_html = bool(self.settings_view.switch_standalone_html.get())
                self.config.translate_persian = bool(self.settings_view.switch_translate_fa.get())
                if hasattr(self.settings_view, "switch_save_api_key"):
                    self.config.save_api_key = bool(self.settings_view.switch_save_api_key.get())
            except Exception as e:
                logger.debug(f"Error syncing settings view: {e}")

        # 3. Save to disk
        target = self.config_path or self.config._loaded_path
        self.config.save(target)

        # 4. Refresh badge
        self.update_key_status_badge()

    # ---------------- Navigation Routes ----------------

    def _nav_to_queue(self) -> None:
        self.save_current_settings()
        self._set_active_nav(self.btn_nav_queue)
        self.viewer_view.grid_forget()
        self.stats_view.grid_forget()
        self.settings_view.grid_forget()
        self.queue_view.grid(row=0, column=0, sticky="nsew")

    def _nav_to_viewer(self) -> None:
        self.save_current_settings()
        self._set_active_nav(self.btn_nav_viewer)
        self.queue_view.grid_forget()
        self.stats_view.grid_forget()
        self.settings_view.grid_forget()
        self.viewer_view.grid(row=0, column=0, sticky="nsew")
        self.viewer_view.refresh_chapters()

    def _nav_to_stats(self) -> None:
        self.save_current_settings()
        self._set_active_nav(self.btn_nav_stats)
        self.queue_view.grid_forget()
        self.viewer_view.grid_forget()
        self.settings_view.grid_forget()
        self.stats_view.grid(row=0, column=0, sticky="nsew")
        self.stats_view.refresh_stats()

    def _nav_to_settings(self) -> None:
        self.save_current_settings()
        self._set_active_nav(self.btn_nav_settings)
        self.queue_view.grid_forget()
        self.viewer_view.grid_forget()
        self.stats_view.grid_forget()
        self.settings_view.grid(row=0, column=0, sticky="nsew")

    def show_chapter(self, md_path: Path) -> None:
        """Switch to Chapter Viewer and focus on the given chapter."""
        self._nav_to_viewer()
        self.viewer_view.load_chapter(md_path)

    # ---------------- Worker Management ----------------

    def start_worker(
        self,
        video_files: List[Path],
        output_dir: Path,
        dry_run: bool = False,
        force: bool = False,
    ) -> None:
        """Launch background pipeline worker thread."""
        self.save_current_settings()
        api_key = (
            self.config.api_key
            or os.environ.get("OPENROUTER_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
        )

        self.worker = PipelineWorker(
            event_queue=self.event_queue,
            video_files=video_files,
            output_dir=output_dir,
            api_key=api_key,
            model=self.config.model,
            poll_interval=self.config.poll_interval,
            dry_run=dry_run,
            force=force,
            reset_failed=self.config.reset_failed,
            system_instruction=self.config.custom_system_instruction or None,
            user_prompt=self.config.custom_user_prompt or None,
            auto_generate_html=getattr(self.config, "auto_generate_html", True),
            auto_generate_pdf=getattr(self.config, "auto_generate_pdf", True),
            standalone_html=getattr(self.config, "standalone_html", False),
            translate_persian=getattr(self.config, "translate_persian", True),
        )
        self.worker.start()

    def stop_worker(self) -> None:
        """Signal active worker thread to stop."""
        if self.worker and self.worker.is_alive():
            self.worker.cancel()

    # ---------------- Event Queue Polling ----------------

    def _poll_worker_events(self) -> None:
        """Read and dispatch messages from worker thread queue."""
        try:
            while True:
                msg: WorkerMessage = self.event_queue.get_nowait()
                self._handle_worker_message(msg)
        except queue.Empty:
            pass
        finally:
            self.after(50, self._poll_worker_events)

    def _handle_worker_message(self, msg: WorkerMessage) -> None:
        """Route worker message to appropriate UI components."""
        kind = msg.kind
        data = msg.data

        if kind == "LOG":
            self.queue_view.append_log(data.get("text", ""), level=data.get("level", "info"))

        elif kind == "BATCH_START":
            self.queue_view.on_batch_start(
                total=data.get("total", 0),
                to_process=data.get("to_process", 0),
                skipped=data.get("skipped", 0),
            )

        elif kind == "FILE_START":
            self.queue_view.on_file_start(
                video_path=data.get("video_path", ""),
                filename=data.get("filename", ""),
                index=data.get("index", 1),
                total=data.get("total", 1),
            )

        elif kind == "FILE_STATUS":
            self.queue_view.on_file_status(
                video_path=data.get("video_path", ""),
                status_msg=data.get("status", ""),
            )

        elif kind == "FILE_COMPLETED":
            self.queue_view.on_file_completed(
                video_path=data.get("video_path", ""),
                output_md_path=data.get("output_md_path", ""),
                snap_count=data.get("snapshots_count", 0),
                duration=data.get("duration", 0.0),
            )

        elif kind == "FILE_FAILED":
            self.queue_view.on_file_failed(
                video_path=data.get("video_path", ""),
                error=data.get("error", ""),
            )

        elif kind == "BATCH_FINISHED":
            self.queue_view.on_batch_finished(data.get("summary", {}))
            self.worker = None

        elif kind == "ERROR":
            messagebox.showerror("Pipeline Error", data.get("message", "An unexpected error occurred."))

    # ---------------- Window Close Handler ----------------

    def _on_window_close(self) -> None:
        """Gracefully handle window close request."""
        if self.worker and self.worker.is_alive():
            confirm = messagebox.askyesno(
                "Batch in Progress",
                "A video processing batch is currently active.\nDo you want to stop processing and exit?",
            )
            if not confirm:
                return
            self.stop_worker()

        # Save config
        self.save_current_settings()
        self.destroy()
