"""Background pipeline worker for video2book Desktop GUI."""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from video2book.extractor import check_ffmpeg_installed, process_markdown_snapshots
from video2book.openrouter import DEFAULT_MODEL, OpenRouterIngestor
from video2book.renderer import export_pdf_chapter, render_html_chapter
from video2book.state import StateManager

logger = logging.getLogger(__name__)


@dataclass
class WorkerMessage:
    """Message payload sent from worker thread to GUI thread."""
    kind: str
    data: Dict[str, Any]


class PipelineWorker(threading.Thread):
    """Background execution thread running the video2book pipeline."""

    def __init__(
        self,
        event_queue: queue.Queue,
        video_files: List[Path],
        output_dir: Path,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        poll_interval: float = 5.0,
        dry_run: bool = False,
        force: bool = False,
        reset_failed: bool = False,
        system_instruction: Optional[str] = None,
        user_prompt: Optional[str] = None,
        state_file: Optional[Path] = None,
        auto_generate_html: bool = True,
        auto_generate_pdf: bool = True,
        standalone_html: bool = False,
        translate_persian: bool = True,
    ):
        super().__init__(daemon=True)
        self.event_queue = event_queue
        self.video_files = [Path(p) for p in video_files]
        self.output_dir = Path(output_dir)
        self.api_key = api_key
        self.model = model
        self.poll_interval = poll_interval
        self.dry_run = dry_run
        self.force = force
        self.reset_failed = reset_failed
        self.system_instruction = system_instruction
        self.user_prompt = user_prompt
        self.state_file = state_file
        self.auto_generate_html = auto_generate_html
        self.auto_generate_pdf = auto_generate_pdf
        self.standalone_html = standalone_html
        self.translate_persian = translate_persian

        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """Signal the worker thread to stop execution."""
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def _emit(self, kind: str, **kwargs: Any) -> None:
        """Push a message to the UI event queue."""
        self.event_queue.put(WorkerMessage(kind=kind, data=kwargs))

    def _log(self, text: str, level: str = "info") -> None:
        """Push a log entry to the UI."""
        self._emit("LOG", text=text, level=level)

    def run(self) -> None:
        """Main background thread execution loop."""
        start_time = time.time()
        self._log("Initializing video2book pipeline worker...", level="info")

        # 1. System FFmpeg check
        ffmpeg_ok, ffmpeg_info = check_ffmpeg_installed()
        if not ffmpeg_ok:
            self._log("Error: FFmpeg is not found in system PATH.", level="error")
            self._emit("ERROR", message="System FFmpeg is missing. Install FFmpeg to continue.")
            self._emit("BATCH_FINISHED", success=False, summary={})
            return

        self._log(f"FFmpeg binary verified: {ffmpeg_info}", level="success")

        # 2. Output directories & state manager
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            images_dir = self.output_dir / "images"
            images_dir.mkdir(parents=True, exist_ok=True)

            actual_state_path = self.state_file or (self.output_dir / StateManager.DEFAULT_STATE_FILENAME)
            state = StateManager(actual_state_path)

            if self.reset_failed:
                n_reset = state.reset_failed()
                self._log(f"Reset {n_reset} previously failed files to PENDING state.", level="warning")

        except Exception as e:
            self._log(f"Failed to initialize output directory or state: {e}", level="error")
            self._emit("ERROR", message=f"Directory/State init error: {e}")
            self._emit("BATCH_FINISHED", success=False, summary={})
            return

        # 3. Dry Run Audit
        if self.dry_run:
            self._log("Performing Dry Run Audit...", level="info")
            audit_items: List[Dict[str, Any]] = []

            for idx, vf in enumerate(self.video_files, 1):
                if self.is_cancelled():
                    self._log("Dry run cancelled by user.", level="warning")
                    break

                state.register_video(vf)
                rec = state.get_record(vf)
                status_str = rec.status.value if rec else "PENDING"
                action_str = "Process" if state.should_process(vf, force=self.force) else "Skip (Already Completed)"
                size_mb = vf.stat().st_size / (1024 * 1024) if vf.exists() else 0.0

                audit_items.append({
                    "index": idx,
                    "filename": vf.name,
                    "path": str(vf),
                    "size_mb": round(size_mb, 2),
                    "status": status_str,
                    "action": action_str,
                })

            self._emit("DRY_RUN_FINISHED", items=audit_items)
            self._log(f"Dry run complete. Inspected {len(audit_items)} video files.", level="success")
            self._emit("BATCH_FINISHED", success=True, summary={"dry_run": True, "count": len(audit_items)})
            return

        # 4. Initialize OpenRouter Ingestor
        ingestor: Optional[OpenRouterIngestor] = None
        try:
            self._log(f"Authenticating with OpenRouter API (model: {self.model})...", level="info")
            ingestor = OpenRouterIngestor(
                api_key=self.api_key,
                model=self.model,
                poll_interval=self.poll_interval,
                system_instruction=self.system_instruction,
                user_prompt=self.user_prompt,
            )
            _, auth_info = ingestor.validate_connection()
            self._log(f"OpenRouter API connection validated for {self.model}: {auth_info}", level="success")
            if "LOW BALANCE" in auth_info:
                self._log(f"⚠️ Account balance notice: {auth_info}", level="warning")
        except Exception as e:
            err_msg = f"OpenRouter API authentication failed: {e}"
            self._log(err_msg, level="error")
            self._emit("ERROR", message=err_msg)
            self._emit("BATCH_FINISHED", success=False, summary={})
            return

        # 5. Determine tasks to process
        tasks_to_process: List[Path] = []
        skipped_count = 0

        for vf in self.video_files:
            state.register_video(vf)
            if state.should_process(vf, force=self.force):
                tasks_to_process.append(vf)
            else:
                skipped_count += 1

        total_files = len(self.video_files)
        self._log(
            f"Queue: {len(tasks_to_process)} to process, {skipped_count} skipped (already completed).",
            level="info",
        )
        self._emit(
            "BATCH_START",
            total=total_files,
            to_process=len(tasks_to_process),
            skipped=skipped_count,
        )

        completed_count = 0
        failed_count = 0
        total_snaps = 0

        for idx, video_path in enumerate(tasks_to_process, 1):
            if self.is_cancelled():
                self._log("Processing batch cancelled by user.", level="warning")
                break

            video_name = video_path.name
            target_md_path = self.output_dir / f"{video_path.stem}.md"
            file_start_time = time.time()

            self._log(f"[{idx}/{len(tasks_to_process)}] Starting: {video_name}", level="info")
            self._emit(
                "FILE_START",
                video_path=str(video_path),
                filename=video_name,
                index=idx,
                total=len(tasks_to_process),
            )

            state.mark_in_progress(video_path)

            def status_cb(msg: str) -> None:
                self._emit("FILE_STATUS", video_path=str(video_path), status=msg)
                self._log(f"  → {video_name}: {msg}", level="info")

            try:
                # Step A: Multimodal Upload & Generation
                raw_markdown = ingestor.upload_and_generate(
                    video_path=video_path,
                    status_callback=status_cb,
                )

                if self.is_cancelled():
                    self._log(f"Cancelled before saving {video_name}", level="warning")
                    break

                # Step B: Snapshot extraction & Markdown rewriting
                status_cb("Extracting snapshots with FFmpeg and rewriting Markdown...")
                processed_md, snap_count = process_markdown_snapshots(
                    markdown_content=raw_markdown,
                    video_path=video_path,
                    output_dir=self.output_dir,
                )

                # Step C: Write file
                with open(target_md_path, "w", encoding="utf-8") as f:
                    f.write(processed_md)

                # Step D: Compile interactive HTML textbook
                target_html_path: Optional[Path] = None
                if self.auto_generate_html:
                    status_cb("Compiling interactive HTML textbook...")
                    target_html_path = self.output_dir / f"{video_path.stem}.html"
                    render_html_chapter(
                        markdown_content=processed_md,
                        output_html_path=target_html_path,
                        video_stem=video_path.stem,
                        embed_base64_images=self.standalone_html,
                    )
                    self._log(f"  → Compiled web book: {target_html_path.name}", level="info")

                # Step E: Compile publication PDF book
                target_pdf_path: Optional[Path] = None
                if self.auto_generate_pdf and target_html_path and target_html_path.exists():
                    status_cb("Compiling publication PDF book via Edge/Chromium...")
                    target_pdf_path = self.output_dir / f"{video_path.stem}.pdf"
                    pdf_res = export_pdf_chapter(target_html_path, target_pdf_path)
                    if pdf_res:
                        self._log(f"  → Compiled PDF book: {target_pdf_path.name}", level="info")

                # Step F: Persian Translation (Keeping technical terms in English)
                target_fa_md_path: Optional[Path] = None
                target_fa_html_path: Optional[Path] = None
                target_fa_pdf_path: Optional[Path] = None

                if self.translate_persian and ingestor:
                    try:
                        status_cb("Translating chapter to Persian (preserving technical terms)...")
                        self._log(f"  → Translating {video_name} to academic Persian...", level="info")
                        persian_md = ingestor.translate_to_persian(
                            markdown_content=processed_md,
                            status_callback=status_cb,
                        )

                        target_fa_md_path = self.output_dir / f"{video_path.stem}_fa.md"
                        with open(target_fa_md_path, "w", encoding="utf-8") as f:
                            f.write(persian_md)
                        self._log(f"  → Saved Persian Markdown: {target_fa_md_path.name}", level="info")

                        # Step G: Compile Persian RTL Web Book
                        if self.auto_generate_html:
                            status_cb("Compiling Persian RTL web book...")
                            target_fa_html_path = self.output_dir / f"{video_path.stem}_fa.html"
                            render_html_chapter(
                                markdown_content=persian_md,
                                output_html_path=target_fa_html_path,
                                video_stem=video_path.stem,
                                embed_base64_images=self.standalone_html,
                                is_rtl=True,
                                lang="fa",
                            )
                            self._log(f"  → Compiled Persian web book: {target_fa_html_path.name}", level="info")

                        # Step H: Compile Persian RTL PDF Book
                        if self.auto_generate_pdf and target_fa_html_path and target_fa_html_path.exists():
                            status_cb("Compiling Persian PDF book via Edge/Chromium...")
                            target_fa_pdf_path = self.output_dir / f"{video_path.stem}_fa.pdf"
                            fa_pdf_res = export_pdf_chapter(target_fa_html_path, target_fa_pdf_path)
                            if fa_pdf_res:
                                self._log(f"  → Compiled Persian PDF book: {target_fa_pdf_path.name}", level="info")

                    except Exception as e:
                        self._log(f"Persian translation skipped or encountered error: {e}", level="warning")

                elapsed = time.time() - file_start_time
                state.mark_completed(
                    path=video_path,
                    output_md_path=target_md_path,
                    snapshots_count=snap_count,
                    duration_seconds=elapsed,
                )

                completed_count += 1
                total_snaps += snap_count

                self._log(
                    f"✓ Completed {video_name} ({snap_count} figures, {elapsed:.1f}s)",
                    level="success",
                )
                self._emit(
                    "FILE_COMPLETED",
                    video_path=str(video_path),
                    filename=video_name,
                    output_md_path=str(target_md_path),
                    output_html_path=str(target_html_path) if target_html_path else None,
                    output_pdf_path=str(target_pdf_path) if (target_pdf_path and target_pdf_path.exists()) else None,
                    output_fa_md_path=str(target_fa_md_path) if target_fa_md_path else None,
                    output_fa_html_path=str(target_fa_html_path) if target_fa_html_path else None,
                    output_fa_pdf_path=str(target_fa_pdf_path) if target_fa_pdf_path else None,
                    snapshots_count=snap_count,
                    duration=elapsed,
                )

            except Exception as e:
                err_msg = str(e)
                state.mark_failed(video_path, err_msg)
                failed_count += 1
                self._log(f"✗ Failed {video_name}: {err_msg}", level="error")
                self._emit(
                    "FILE_FAILED",
                    video_path=str(video_path),
                    filename=video_name,
                    error=err_msg,
                )

        # Batch summary
        total_duration = time.time() - start_time
        summary = {
            "total_files": total_files,
            "processed": completed_count,
            "failed": failed_count,
            "skipped": skipped_count,
            "total_snapshots": total_snaps,
            "duration": total_duration,
            "cancelled": self.is_cancelled(),
        }

        self._log(
            f"Batch completed: {completed_count} processed, {failed_count} failed, "
            f"{skipped_count} skipped, {total_snaps} figures extracted in {total_duration:.1f}s.",
            level="success" if failed_count == 0 else "warning",
        )

        self._emit("BATCH_FINISHED", success=(failed_count == 0), summary=summary)
