"""Statistics, Analytics, and State Checkpointing View for video2book."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict

import customtkinter as ctk

from video2book.state import StateManager, VideoStatus

if TYPE_CHECKING:
    from video2book.gui.app import Video2BookApp


class StatsView(ctk.CTkFrame):
    """View to inspect library analytics, completion metrics, and state records."""

    def __init__(self, master: Any, app: Video2BookApp):
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self.app = app

        self._build_ui()

    def _build_ui(self) -> None:
        """Construct the statistics dashboard layout."""
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # ---------------- Header ----------------
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.grid(row=0, column=0, sticky="ew", padx=15, pady=(15, 10))
        header_frame.grid_columnconfigure(0, weight=1)

        lbl_title = ctk.CTkLabel(
            header_frame,
            text="Pipeline Analytics & State Checkpoints",
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        lbl_title.grid(row=0, column=0, sticky="w")

        btn_refresh = ctk.CTkButton(
            header_frame,
            text="↻ Refresh",
            width=90,
            command=self.refresh_stats,
        )
        btn_refresh.grid(row=0, column=1, padx=5)

        btn_open_state = ctk.CTkButton(
            header_frame,
            text="📄 Open State File",
            width=130,
            fg_color="#3A3D40",
            hover_color="#4E5256",
            command=self._on_open_state_file,
        )
        btn_open_state.grid(row=0, column=2, padx=(5, 0))

        # ---------------- Metric Cards ----------------
        cards_frame = ctk.CTkFrame(self, fg_color="transparent")
        cards_frame.grid(row=1, column=0, sticky="ew", padx=15, pady=5)
        for i in range(4):
            cards_frame.grid_columnconfigure(i, weight=1)

        # Card 1: Completed Chapters
        self.card_completed = self._create_metric_card(
            cards_frame, 0, 0, "Chapters Completed", "0", "#2EA043"
        )
        # Card 2: Figures Extracted
        self.card_figures = self._create_metric_card(
            cards_frame, 0, 1, "Extracted Figures", "0", "#1F6FEB"
        )
        # Card 3: Failed Items
        self.card_failed = self._create_metric_card(
            cards_frame, 0, 2, "Failed Items", "0", "#DA3633"
        )
        # Card 4: Total Time
        self.card_time = self._create_metric_card(
            cards_frame, 0, 3, "Total Process Time", "0s", "#D29922"
        )

        # ---------------- State Checkpoint Table ----------------
        table_container = ctk.CTkFrame(self, corner_radius=10)
        table_container.grid(row=2, column=0, sticky="nsew", padx=15, pady=(10, 15))
        table_container.grid_columnconfigure(0, weight=1)
        table_container.grid_rowconfigure(1, weight=1)

        table_header = ctk.CTkFrame(table_container, fg_color="transparent")
        table_header.grid(row=0, column=0, sticky="ew", padx=15, pady=(12, 6))
        table_header.grid_columnconfigure(0, weight=1)

        lbl_tbl = ctk.CTkLabel(
            table_header,
            text="Registered State Checkpoints (.video2book_state.json)",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        lbl_tbl.grid(row=0, column=0, sticky="w")

        btn_reset_all_failed = ctk.CTkButton(
            table_header,
            text="🔄 Reset Failed to PENDING",
            width=180,
            fg_color="#3A3D40",
            hover_color="#4E5256",
            command=self._on_reset_failed,
        )
        btn_reset_all_failed.grid(row=0, column=1)

        self.scroll_table = ctk.CTkScrollableFrame(table_container, corner_radius=6)
        self.scroll_table.grid(row=1, column=0, sticky="nsew", padx=10, pady=(5, 10))
        self.scroll_table.grid_columnconfigure(0, weight=1)

    def _create_metric_card(
        self,
        parent: Any,
        row: int,
        col: int,
        title: str,
        value: str,
        accent_color: str,
    ) -> Dict[str, Any]:
        """Create a rounded metric card widget."""
        card = ctk.CTkFrame(parent, corner_radius=10, fg_color="#21262D")
        card.grid(row=row, column=col, sticky="nsew", padx=6, pady=5)
        card.grid_columnconfigure(0, weight=1)

        lbl_title = ctk.CTkLabel(
            card,
            text=title,
            font=ctk.CTkFont(size=12),
            text_color="#8B949E",
        )
        lbl_title.pack(anchor="w", padx=15, pady=(12, 2))

        lbl_val = ctk.CTkLabel(
            card,
            text=value,
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=accent_color,
        )
        lbl_val.pack(anchor="w", padx=15, pady=(0, 12))

        return {"label_val": lbl_val, "label_title": lbl_title}

    def refresh_stats(self) -> None:
        """Load state and recompute metrics."""
        out_dir = Path(self.app.config.output_dir or "./output").resolve()
        state_file = out_dir / StateManager.DEFAULT_STATE_FILENAME

        if not state_file.exists():
            self.card_completed["label_val"].configure(text="0")
            self.card_figures["label_val"].configure(text="0")
            self.card_failed["label_val"].configure(text="0")
            self.card_time["label_val"].configure(text="0s")
            self._render_empty_state("No state tracking file found in output directory.")
            return

        state = StateManager(state_file)
        records = list(state.records.values())

        completed = sum(1 for r in records if r.status == VideoStatus.COMPLETED)
        failed = sum(1 for r in records if r.status == VideoStatus.FAILED)
        total_figures = sum(r.snapshots_count for r in records)
        total_seconds = sum(r.duration_seconds or 0 for r in records)

        self.card_completed["label_val"].configure(text=str(completed))
        self.card_figures["label_val"].configure(text=str(total_figures))
        self.card_failed["label_val"].configure(text=str(failed))

        if total_seconds < 60:
            time_str = f"{total_seconds:.1f}s"
        elif total_seconds < 3600:
            time_str = f"{total_seconds/60:.1f}m"
        else:
            time_str = f"{total_seconds/3600:.1f}h"
        self.card_time["label_val"].configure(text=time_str)

        self._render_records_table(records)

    def _render_empty_state(self, message: str) -> None:
        for w in self.scroll_table.winfo_children():
            w.destroy()
        lbl = ctk.CTkLabel(
            self.scroll_table,
            text=message,
            font=ctk.CTkFont(size=12),
            text_color="#8B949E",
        )
        lbl.pack(pady=40)

    def _render_records_table(self, records: list) -> None:
        for w in self.scroll_table.winfo_children():
            w.destroy()

        if not records:
            self._render_empty_state("State file is empty.")
            return

        for idx, rec in enumerate(records, 1):
            p = Path(rec.file_path)
            card = ctk.CTkFrame(self.scroll_table, corner_radius=6, fg_color="#161B22")
            card.pack(fill="x", padx=4, pady=3)
            card.grid_columnconfigure(1, weight=1)

            lbl_idx = ctk.CTkLabel(
                card,
                text=f"#{idx}",
                font=ctk.CTkFont(size=11),
                text_color="#8B949E",
                width=35,
            )
            lbl_idx.grid(row=0, column=0, padx=8, pady=8)

            lbl_name = ctk.CTkLabel(
                card,
                text=p.name,
                font=ctk.CTkFont(size=12, weight="bold"),
                anchor="w",
            )
            lbl_name.grid(row=0, column=1, sticky="w", padx=5, pady=(8, 2))

            sub_info = f"Size: {rec.file_size_bytes/(1024*1024):.1f} MB  •  Sig: {rec.file_hash[:10]}..."
            if rec.duration_seconds:
                sub_info += f"  •  Duration: {rec.duration_seconds:.1f}s"
            if rec.error_message:
                sub_info += f"  •  Error: {rec.error_message}"

            lbl_sub = ctk.CTkLabel(
                card,
                text=sub_info,
                font=ctk.CTkFont(size=11),
                text_color="#8B949E" if not rec.error_message else "#DA3633",
                anchor="w",
            )
            lbl_sub.grid(row=1, column=1, sticky="w", padx=5, pady=(0, 8))

            badge_color = {
                VideoStatus.PENDING: ("#30363D", "#C9D1D9"),
                VideoStatus.IN_PROGRESS: ("#1F6FEB", "#FFFFFF"),
                VideoStatus.COMPLETED: ("#238636", "#FFFFFF"),
                VideoStatus.FAILED: ("#DA3633", "#FFFFFF"),
            }.get(rec.status, ("#30363D", "#C9D1D9"))

            lbl_status = ctk.CTkLabel(
                card,
                text=rec.status.value,
                fg_color=badge_color[0],
                text_color=badge_color[1],
                corner_radius=4,
                font=ctk.CTkFont(size=10, weight="bold"),
                width=85,
                height=22,
            )
            lbl_status.grid(row=0, column=2, rowspan=2, padx=10, pady=8)

            lbl_snaps = ctk.CTkLabel(
                card,
                text=f"🖼 {rec.snapshots_count}" if rec.snapshots_count > 0 else "-",
                font=ctk.CTkFont(size=11),
                text_color="#58A6FF" if rec.snapshots_count > 0 else "#8B949E",
                width=60,
            )
            lbl_snaps.grid(row=0, column=3, rowspan=2, padx=10, pady=8)

    def _on_reset_failed(self) -> None:
        out_dir = Path(self.app.config.output_dir or "./output").resolve()
        state_file = out_dir / StateManager.DEFAULT_STATE_FILENAME
        if state_file.exists():
            state = StateManager(state_file)
            n = state.reset_failed()
            self.app.queue_view.append_log(f"Reset {n} failed items in state.", level="info")
            self.refresh_stats()

    def _on_open_state_file(self) -> None:
        out_dir = Path(self.app.config.output_dir or "./output").resolve()
        state_file = out_dir / StateManager.DEFAULT_STATE_FILENAME
        if state_file.exists():
            try:
                if os.name == "nt":
                    os.startfile(str(state_file))
                else:
                    subprocess.run(["xdg-open", str(state_file)], check=False)
            except Exception as e:
                self.app.queue_view.append_log(f"Failed to open state file: {e}", level="error")
