"""Queue and Execution View for video2book Desktop GUI."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

import customtkinter as ctk

from video2book.cli import discover_video_files
from video2book.gui.utils import add_context_menu
from video2book.state import StateManager, VideoStatus

if TYPE_CHECKING:
    from video2book.gui.app import Video2BookApp


class QueueView(ctk.CTkFrame):
    """Main dashboard view with file queue, batch controls, and live logs."""

    def __init__(self, master: Any, app: Video2BookApp):
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self.app = app

        # Data state
        self.video_items: List[Path] = []
        self.item_widgets: Dict[str, Dict[str, Any]] = {}
        self.filter_status: str = "ALL"
        self.search_query: str = ""

        self._build_ui()

        # Auto-scan if saved input_dir is a valid directory
        if self.app.config.input_dir:
            try:
                p = Path(self.app.config.input_dir)
                if p.exists() and p.is_dir():
                    self.scan_videos()
            except Exception:
                pass

    def _build_ui(self) -> None:
        """Construct the UI layout."""
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)  # Queue table expands
        self.grid_rowconfigure(4, weight=0)  # Log console expands less

        # ---------------- 1. Top Section: Paths & Selectors ----------------
        paths_frame = ctk.CTkFrame(self, corner_radius=10)
        paths_frame.grid(row=0, column=0, sticky="ew", padx=15, pady=(15, 8))
        paths_frame.grid_columnconfigure(1, weight=1)

        # Input Directory
        lbl_in = ctk.CTkLabel(paths_frame, text="Input Videos:", font=ctk.CTkFont(weight="bold"))
        lbl_in.grid(row=0, column=0, padx=(15, 10), pady=(10, 5), sticky="w")

        self.entry_input = ctk.CTkEntry(
            paths_frame,
            placeholder_text="Select folder or files containing video lectures...",
            height=32,
        )
        self.entry_input.grid(row=0, column=1, padx=5, pady=(10, 5), sticky="ew")
        add_context_menu(self.entry_input)
        if self.app.config.input_dir:
            self.entry_input.insert(0, self.app.config.input_dir)
        self.entry_input.bind("<FocusOut>", lambda e: self._on_input_changed())
        self.entry_input.bind("<Return>", lambda e: self._on_input_return())

        btn_browse_in = ctk.CTkButton(
            paths_frame,
            text="📁 Browse Folder",
            width=120,
            command=self._on_browse_input_folder,
        )
        btn_browse_in.grid(row=0, column=2, padx=5, pady=(10, 5))

        btn_add_files = ctk.CTkButton(
            paths_frame,
            text="📄 Add Files",
            width=100,
            fg_color="#3A3D40",
            hover_color="#4E5256",
            command=self._on_browse_input_files,
        )
        btn_add_files.grid(row=0, column=3, padx=(5, 15), pady=(10, 5))

        # Output Directory
        lbl_out = ctk.CTkLabel(paths_frame, text="Output Folder:", font=ctk.CTkFont(weight="bold"))
        lbl_out.grid(row=1, column=0, padx=(15, 10), pady=(5, 10), sticky="w")

        self.entry_output = ctk.CTkEntry(
            paths_frame,
            placeholder_text="Target directory for Markdown & snapshots (e.g. ./output)...",
            height=32,
        )
        self.entry_output.grid(row=1, column=1, padx=5, pady=(5, 10), sticky="ew")
        add_context_menu(self.entry_output)
        self.entry_output.insert(0, self.app.config.output_dir or "./output")
        self.entry_output.bind("<FocusOut>", lambda e: self._on_output_changed())
        self.entry_output.bind("<Return>", lambda e: self._on_output_changed())

        btn_browse_out = ctk.CTkButton(
            paths_frame,
            text="📁 Browse Output",
            width=120,
            command=self._on_browse_output_folder,
        )
        btn_browse_out.grid(row=1, column=2, padx=5, pady=(5, 10))

        btn_open_out = ctk.CTkButton(
            paths_frame,
            text="↗ Open Folder",
            width=100,
            fg_color="#3A3D40",
            hover_color="#4E5256",
            command=self._on_open_output_folder,
        )
        btn_open_out.grid(row=1, column=3, padx=(5, 15), pady=(5, 10))

        # ---------------- 2. Action Controls Bar ----------------
        actions_frame = ctk.CTkFrame(self, corner_radius=10)
        actions_frame.grid(row=1, column=0, sticky="ew", padx=15, pady=6)
        actions_frame.grid_columnconfigure(6, weight=1)

        # Primary Action: Start Batch
        self.btn_start = ctk.CTkButton(
            actions_frame,
            text="▶ Start Processing Batch",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#2EA043",
            hover_color="#238636",
            height=38,
            width=190,
            command=self._on_start_batch,
        )
        self.btn_start.grid(row=0, column=0, padx=(15, 10), pady=10)

        # Stop Action
        self.btn_stop = ctk.CTkButton(
            actions_frame,
            text="⏹ Stop",
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#DA3633",
            hover_color="#B62324",
            height=38,
            width=90,
            state="disabled",
            command=self._on_stop_batch,
        )
        self.btn_stop.grid(row=0, column=1, padx=5, pady=10)

        # Toggles
        self.switch_dry_run = ctk.CTkSwitch(
            actions_frame,
            text="Dry Run Audit",
            font=ctk.CTkFont(size=12),
            command=self._on_switch_changed,
        )
        if self.app.config.dry_run:
            self.switch_dry_run.select()
        self.switch_dry_run.grid(row=0, column=2, padx=10, pady=10)

        self.switch_force = ctk.CTkSwitch(
            actions_frame,
            text="Force Reprocess",
            font=ctk.CTkFont(size=12),
            command=self._on_switch_changed,
        )
        if self.app.config.force:
            self.switch_force.select()
        self.switch_force.grid(row=0, column=3, padx=10, pady=10)

        self.btn_reset_failed = ctk.CTkButton(
            actions_frame,
            text="🔄 Reset Failed",
            width=110,
            height=32,
            fg_color="#3A3D40",
            hover_color="#4E5256",
            command=self._on_reset_failed,
        )
        self.btn_reset_failed.grid(row=0, column=4, padx=10, pady=10)

        self.btn_scan = ctk.CTkButton(
            actions_frame,
            text="↻ Scan Library",
            width=110,
            height=32,
            command=self.scan_videos,
        )
        self.btn_scan.grid(row=0, column=5, padx=10, pady=10)

        # ---------------- 3. Queue List Section ----------------
        queue_container = ctk.CTkFrame(self, corner_radius=10)
        queue_container.grid(row=2, column=0, sticky="nsew", padx=15, pady=6)
        queue_container.grid_columnconfigure(0, weight=1)
        queue_container.grid_rowconfigure(1, weight=1)

        # Header with Search & Filter
        filter_header = ctk.CTkFrame(queue_container, fg_color="transparent")
        filter_header.grid(row=0, column=0, sticky="ew", padx=15, pady=(10, 5))
        filter_header.grid_columnconfigure(0, weight=1)

        self.lbl_queue_count = ctk.CTkLabel(
            filter_header,
            text="Video Library Queue (0 videos)",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.lbl_queue_count.grid(row=0, column=0, sticky="w")

        # Search box
        self.entry_search = ctk.CTkEntry(
            filter_header,
            placeholder_text="🔍 Filter videos...",
            width=200,
            height=28,
        )
        self.entry_search.grid(row=0, column=1, padx=10)
        self.entry_search.bind("<KeyRelease>", self._on_search_changed)
        add_context_menu(self.entry_search)

        # Status Segment Filter
        self.seg_filter = ctk.CTkSegmentedButton(
            filter_header,
            values=["All", "Pending", "Completed", "Failed"],
            command=self._on_filter_changed,
        )
        self.seg_filter.set("All")
        self.seg_filter.grid(row=0, column=2)

        # Scrollable list for queue cards
        self.scroll_queue = ctk.CTkScrollableFrame(queue_container, corner_radius=8)
        self.scroll_queue.grid(row=1, column=0, sticky="nsew", padx=10, pady=(5, 10))
        self.scroll_queue.grid_columnconfigure(0, weight=1)

        # ---------------- 4. Progress Section ----------------
        progress_frame = ctk.CTkFrame(self, corner_radius=10)
        progress_frame.grid(row=3, column=0, sticky="ew", padx=15, pady=6)
        progress_frame.grid_columnconfigure(0, weight=1)

        prog_header = ctk.CTkFrame(progress_frame, fg_color="transparent")
        prog_header.grid(row=0, column=0, sticky="ew", padx=15, pady=(8, 2))
        prog_header.grid_columnconfigure(0, weight=1)

        self.lbl_overall_prog = ctk.CTkLabel(
            prog_header,
            text="Overall Progress: Ready",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.lbl_overall_prog.grid(row=0, column=0, sticky="w")

        self.lbl_step_status = ctk.CTkLabel(
            prog_header,
            text="",
            font=ctk.CTkFont(size=12),
            text_color="#8B949E",
        )
        self.lbl_step_status.grid(row=0, column=1, sticky="e")

        self.prog_bar = ctk.CTkProgressBar(progress_frame, height=12)
        self.prog_bar.grid(row=1, column=0, sticky="ew", padx=15, pady=(2, 10))
        self.prog_bar.set(0)

        # ---------------- 5. Live Log Drawer ----------------
        log_frame = ctk.CTkFrame(self, corner_radius=10)
        log_frame.grid(row=4, column=0, sticky="ew", padx=15, pady=(6, 15))
        log_frame.grid_columnconfigure(0, weight=1)

        log_header = ctk.CTkFrame(log_frame, fg_color="transparent")
        log_header.grid(row=0, column=0, sticky="ew", padx=15, pady=(8, 4))
        log_header.grid_columnconfigure(0, weight=1)

        lbl_log = ctk.CTkLabel(
            log_header,
            text="Live Execution Logs",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        lbl_log.grid(row=0, column=0, sticky="w")

        btn_copy_log = ctk.CTkButton(
            log_header,
            text="Copy Logs",
            width=80,
            height=24,
            font=ctk.CTkFont(size=11),
            fg_color="#3A3D40",
            hover_color="#4E5256",
            command=self._on_copy_logs,
        )
        btn_copy_log.grid(row=0, column=1, padx=5)

        btn_clear_log = ctk.CTkButton(
            log_header,
            text="Clear",
            width=60,
            height=24,
            font=ctk.CTkFont(size=11),
            fg_color="#3A3D40",
            hover_color="#4E5256",
            command=self._on_clear_logs,
        )
        btn_clear_log.grid(row=0, column=2)

        self.txt_logs = ctk.CTkTextbox(
            log_frame,
            height=110,
            font=ctk.CTkFont(family="Consolas", size=11),
            wrap="none",
        )
        self.txt_logs.grid(row=1, column=0, sticky="ew", padx=15, pady=(2, 10))
        self.txt_logs.configure(state="disabled")

    # ---------------- Folder & File Sync Handlers ----------------

    def _on_input_changed(self) -> None:
        """Sync manually typed or pasted input directory to app config."""
        in_dir = self.entry_input.get().strip()
        self.app.config.input_dir = in_dir
        self.app.save_current_settings()

    def _on_input_return(self) -> None:
        """Handle Enter key in input directory entry: sync and scan."""
        self._on_input_changed()
        self.scan_videos()

    def _on_output_changed(self) -> None:
        """Sync manually typed or pasted output directory to app config."""
        out_dir = self.entry_output.get().strip() or "./output"
        self.app.config.output_dir = out_dir
        self.app.save_current_settings()

    def _on_switch_changed(self) -> None:
        """Sync dry run and force switch toggles to app config."""
        self.app.config.dry_run = bool(self.switch_dry_run.get())
        self.app.config.force = bool(self.switch_force.get())
        self.app.save_current_settings()

    # ---------------- Folder & File Browsing ----------------

    def _on_browse_input_folder(self) -> None:
        """Open folder picker dialog for input directory."""
        from tkinter import filedialog
        folder = filedialog.askdirectory(
            title="Select Video Lecture Folder",
            initialdir=self.entry_input.get() or os.getcwd(),
        )
        if folder:
            self.entry_input.delete(0, "end")
            self.entry_input.insert(0, folder)
            self.app.config.input_dir = folder
            self.app.save_current_settings()
            self.scan_videos()

    def _on_browse_input_files(self) -> None:
        """Open file picker to select specific video files."""
        from tkinter import filedialog
        ext_list = [f"*.{e.strip()}" for e in self.app.config.extensions.split(",")]
        file_pattern = ";".join(ext_list)
        files = filedialog.askopenfilenames(
            title="Select Video Files",
            filetypes=[("Video Files", file_pattern), ("All Files", "*.*")],
        )
        if files:
            paths = [Path(f) for f in files]
            if paths and not self.entry_input.get().strip():
                parent_dir = str(paths[0].parent)
                self.entry_input.delete(0, "end")
                self.entry_input.insert(0, parent_dir)
                self.app.config.input_dir = parent_dir
            self.app.save_current_settings()
            self.set_video_files(paths)

    def _on_browse_output_folder(self) -> None:
        """Open folder picker dialog for output directory."""
        from tkinter import filedialog
        folder = filedialog.askdirectory(
            title="Select Output Folder",
            initialdir=self.entry_output.get() or "./output",
        )
        if folder:
            self.entry_output.delete(0, "end")
            self.entry_output.insert(0, folder)
            self.app.config.output_dir = folder
            self.app.save_current_settings()

    def _on_open_output_folder(self) -> None:
        """Open output directory in file explorer."""
        out_path = Path(self.entry_output.get() or "./output").resolve()
        out_path.mkdir(parents=True, exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(str(out_path))
            else:
                subprocess.run(["xdg-open", str(out_path)], check=False)
        except Exception as e:
            self.append_log(f"Failed to open output directory: {e}", level="error")

    # ---------------- Video Library Scanning ----------------

    def scan_videos(self) -> None:
        """Scan input directory for videos according to configured extensions."""
        in_str = self.entry_input.get().strip()
        self.app.config.input_dir = in_str
        self.app.save_current_settings()
        if not in_str:
            self.append_log("Please specify an input directory or select video files.", level="warning")
            return

        in_dir = Path(in_str)
        if not in_dir.exists() or not in_dir.is_dir():
            self.append_log(f"Input path does not exist or is not a directory: {in_dir}", level="error")
            return

        ext_list = [e.strip() for e in self.app.config.extensions.split(",") if e.strip()]
        files = discover_video_files(in_dir, ext_list)
        self.set_video_files(files)

    def set_video_files(self, files: List[Path]) -> None:
        """Update active list of video files and refresh UI queue."""
        self.video_items = files
        self.lbl_queue_count.configure(text=f"Video Library Queue ({len(self.video_items)} videos)")
        self.refresh_queue_table()
        self.append_log(f"Discovered {len(files)} video files ready for processing.", level="info")

    def _get_state_manager(self) -> StateManager:
        """Get or initialize StateManager for current output directory."""
        out_dir = Path(self.entry_output.get() or "./output").resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        return StateManager(out_dir / StateManager.DEFAULT_STATE_FILENAME)

    def refresh_queue_table(self) -> None:
        """Re-render queue items matching current search and filter."""
        # Clear existing items
        for widget in self.scroll_queue.winfo_children():
            widget.destroy()
        self.item_widgets.clear()

        state = self._get_state_manager()
        query = self.search_query.lower()
        active_filter = self.filter_status.upper()

        visible_count = 0

        for idx, vf in enumerate(self.video_items, 1):
            name = vf.name
            if query and query not in name.lower():
                continue

            # State check
            rec = state.get_record(vf)
            status = rec.status if rec else VideoStatus.PENDING

            if active_filter != "ALL" and status.value != active_filter:
                continue

            visible_count += 1
            self._render_queue_card(idx, vf, rec, status)

        if visible_count == 0:
            lbl_empty = ctk.CTkLabel(
                self.scroll_queue,
                text="No video files matching current filter criteria.",
                font=ctk.CTkFont(size=13),
                text_color="#8B949E",
            )
            lbl_empty.pack(pady=30)

    def _render_queue_card(
        self,
        index: int,
        vf: Path,
        record: Any,
        status: VideoStatus,
    ) -> None:
        """Render a single video row card in the scrollable queue."""
        card = ctk.CTkFrame(self.scroll_queue, corner_radius=8, fg_color="#21262D")
        card.pack(fill="x", padx=5, pady=4)
        card.grid_columnconfigure(1, weight=1)

        # Index label
        lbl_idx = ctk.CTkLabel(
            card,
            text=f"#{index}",
            width=35,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#8B949E",
        )
        lbl_idx.grid(row=0, column=0, rowspan=2, padx=(10, 5), pady=8)

        # Title & Path
        lbl_title = ctk.CTkLabel(
            card,
            text=vf.name,
            font=ctk.CTkFont(size=13, weight="bold"),
            anchor="w",
        )
        lbl_title.grid(row=0, column=1, sticky="w", padx=5, pady=(8, 0))

        size_mb = (vf.stat().st_size / (1024 * 1024)) if vf.exists() else 0.0
        subtext = f"{size_mb:.1f} MB  •  {vf.parent.name}/{vf.name}"
        if record and record.duration_seconds:
            subtext += f"  •  Took {record.duration_seconds:.1f}s"

        lbl_sub = ctk.CTkLabel(
            card,
            text=subtext,
            font=ctk.CTkFont(size=11),
            text_color="#8B949E",
            anchor="w",
        )
        lbl_sub.grid(row=1, column=1, sticky="w", padx=5, pady=(0, 8))

        # Status Badge
        badge_color = {
            VideoStatus.PENDING: ("#30363D", "#C9D1D9"),
            VideoStatus.IN_PROGRESS: ("#1F6FEB", "#FFFFFF"),
            VideoStatus.COMPLETED: ("#238636", "#FFFFFF"),
            VideoStatus.FAILED: ("#DA3633", "#FFFFFF"),
        }.get(status, ("#30363D", "#C9D1D9"))

        lbl_status = ctk.CTkLabel(
            card,
            text=status.value,
            fg_color=badge_color[0],
            text_color=badge_color[1],
            corner_radius=6,
            font=ctk.CTkFont(size=11, weight="bold"),
            width=90,
            height=24,
        )
        lbl_status.grid(row=0, column=2, rowspan=2, padx=10, pady=8)

        # Figures count
        snaps = record.snapshots_count if record else 0
        lbl_snaps = ctk.CTkLabel(
            card,
            text=f"🖼 {snaps} Figs" if snaps > 0 else "-",
            font=ctk.CTkFont(size=11),
            text_color="#8B949E" if snaps == 0 else "#58A6FF",
            width=65,
        )
        lbl_snaps.grid(row=0, column=3, rowspan=2, padx=5, pady=8)

        # Actions
        btn_frame = ctk.CTkFrame(card, fg_color="transparent")
        btn_frame.grid(row=0, column=4, rowspan=2, padx=(5, 10), pady=8)

        if status == VideoStatus.COMPLETED:
            btn_view = ctk.CTkButton(
                btn_frame,
                text="📖 View",
                width=65,
                height=26,
                font=ctk.CTkFont(size=11),
                command=lambda p=vf: self._on_view_chapter(p),
            )
            btn_view.pack(side="left", padx=2)
        else:
            btn_run_single = ctk.CTkButton(
                btn_frame,
                text="▶ Run",
                width=65,
                height=26,
                fg_color="#3A3D40",
                hover_color="#4E5256",
                font=ctk.CTkFont(size=11),
                command=lambda p=vf: self._on_run_single(p),
            )
            btn_run_single.pack(side="left", padx=2)

        self.item_widgets[str(vf.resolve())] = {
            "status_label": lbl_status,
            "snaps_label": lbl_snaps,
        }

    # ---------------- Search & Filter Handlers ----------------

    def _on_search_changed(self, event: Any = None) -> None:
        self.search_query = self.entry_search.get()
        self.refresh_queue_table()

    def _on_filter_changed(self, value: str) -> None:
        self.filter_status = value
        self.refresh_queue_table()

    # ---------------- Execution & Batch Logic ----------------

    def _on_start_batch(self) -> None:
        """Start batch processing of the entire video queue."""
        self.app.save_current_settings()
        if not self.video_items:
            self.scan_videos()
            if not self.video_items:
                self.append_log("No videos found to process.", level="warning")
                return

        out_str = self.entry_output.get().strip() or "./output"
        out_dir = Path(out_str).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        dry_run = bool(self.switch_dry_run.get())
        force = bool(self.switch_force.get())

        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.prog_bar.set(0)
        self.lbl_overall_prog.configure(text="Processing Batch...")

        self.app.start_worker(
            video_files=self.video_items,
            output_dir=out_dir,
            dry_run=dry_run,
            force=force,
        )

    def _on_run_single(self, video_path: Path) -> None:
        """Process an individual video file immediately."""
        self.app.save_current_settings()
        out_str = self.entry_output.get().strip() or "./output"
        out_dir = Path(out_str).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        dry_run = bool(self.switch_dry_run.get())

        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.prog_bar.set(0)
        self.lbl_overall_prog.configure(text=f"Processing {video_path.name}...")

        self.app.start_worker(
            video_files=[video_path],
            output_dir=out_dir,
            dry_run=dry_run,
            force=True,  # Force single file execution
        )

    def _on_stop_batch(self) -> None:
        """Signal worker to stop execution."""
        self.app.stop_worker()
        self.btn_stop.configure(state="disabled")
        self.lbl_step_status.configure(text="Stopping batch...")
        self.append_log("Stop requested. Waiting for worker to cleanly halt...", level="warning")

    def _on_reset_failed(self) -> None:
        """Reset failed items in state store to PENDING."""
        state = self._get_state_manager()
        n = state.reset_failed()
        self.append_log(f"Reset {n} failed entries to PENDING.", level="info")
        self.refresh_queue_table()

    def _on_view_chapter(self, video_path: Path) -> None:
        """Navigate to Chapter Viewer view and load the selected video's chapter."""
        out_dir = Path(self.entry_output.get() or "./output").resolve()
        md_file = out_dir / f"{video_path.stem}.md"
        self.app.show_chapter(md_file)

    # ---------------- UI Status & Event Updaters ----------------

    def on_batch_start(self, total: int, to_process: int, skipped: int) -> None:
        self.lbl_overall_prog.configure(text=f"Batch: 0/{to_process} files completed ({skipped} skipped)")
        self.prog_bar.set(0)

    def on_file_start(self, video_path: str, filename: str, index: int, total: int) -> None:
        self.lbl_overall_prog.configure(text=f"Overall: File {index} of {total} ({filename})")
        self.lbl_step_status.configure(text="Starting...")

        norm = str(Path(video_path).resolve())
        if norm in self.item_widgets:
            lbl = self.item_widgets[norm]["status_label"]
            lbl.configure(text="IN PROGRESS", fg_color="#1F6FEB", text_color="#FFFFFF")

    def on_file_status(self, video_path: str, status_msg: str) -> None:
        self.lbl_step_status.configure(text=status_msg)

    def on_file_completed(self, video_path: str, output_md_path: str, snap_count: int, duration: float) -> None:
        norm = str(Path(video_path).resolve())
        if norm in self.item_widgets:
            lbl = self.item_widgets[norm]["status_label"]
            lbl.configure(text="COMPLETED", fg_color="#238636", text_color="#FFFFFF")
            snaps_lbl = self.item_widgets[norm]["snaps_label"]
            snaps_lbl.configure(text=f"🖼 {snap_count} Figs", text_color="#58A6FF")

    def on_file_failed(self, video_path: str, error: str) -> None:
        norm = str(Path(video_path).resolve())
        if norm in self.item_widgets:
            lbl = self.item_widgets[norm]["status_label"]
            lbl.configure(text="FAILED", fg_color="#DA3633", text_color="#FFFFFF")

    def on_batch_finished(self, summary: Dict[str, Any]) -> None:
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.prog_bar.set(1.0)
        self.lbl_step_status.configure(text="")

        if summary.get("dry_run"):
            self.lbl_overall_prog.configure(text="Dry run audit completed successfully.")
        else:
            p = summary.get("processed", 0)
            f = summary.get("failed", 0)
            self.lbl_overall_prog.configure(text=f"Batch Finished: {p} processed, {f} failed.")

        self.refresh_queue_table()

    # ---------------- Log Drawer Methods ----------------

    def append_log(self, text: str, level: str = "info") -> None:
        """Append a message to the live log textbox."""
        import datetime
        now = datetime.datetime.now().strftime("%H:%M:%S")

        prefix = {
            "info": "[INFO]",
            "success": "[SUCCESS]",
            "warning": "[WARN]",
            "error": "[ERROR]",
        }.get(level, "[INFO]")

        line = f"[{now}] {prefix} {text}\n"

        self.txt_logs.configure(state="normal")
        self.txt_logs.insert("end", line)
        self.txt_logs.see("end")
        self.txt_logs.configure(state="disabled")

    def _on_clear_logs(self) -> None:
        self.txt_logs.configure(state="normal")
        self.txt_logs.delete("1.0", "end")
        self.txt_logs.configure(state="disabled")

    def _on_copy_logs(self) -> None:
        logs = self.txt_logs.get("1.0", "end")
        self.clipboard_clear()
        self.clipboard_append(logs)
        self.append_log("Logs copied to system clipboard.", level="info")
