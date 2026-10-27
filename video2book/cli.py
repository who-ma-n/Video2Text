"""Production-grade CLI pipeline for video2book."""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import List, Optional

import click
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from video2book.extractor import check_ffmpeg_installed, process_markdown_snapshots
from video2book.openrouter import (
    DEFAULT_MODEL,
    OpenRouterIngestor,
    fetch_openrouter_gemini_models,
)
from video2book.renderer import export_pdf_chapter, render_html_chapter
from video2book.state import StateManager, VideoStatus

if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console(legacy_windows=False)


def natural_sort_key(s: str | Path) -> list:
    """Sort strings with embedded numbers naturally (e.g. 1, 2, 10)."""
    text = str(s)
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", text)]


def setup_logging(verbose: bool) -> None:
    """Configure rich logging handler."""
    log_level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )


def discover_video_files(input_dir: Path, extensions: List[str]) -> List[Path]:
    """Recursively scan directory for video files matching extensions."""
    files: List[Path] = []
    ext_set = {f".{ext.lower().lstrip('.')}" for ext in extensions}

    for root, _, filenames in os.walk(input_dir):
        for f in filenames:
            p = Path(root) / f
            if p.suffix.lower() in ext_set:
                files.append(p)

    files.sort(key=natural_sort_key)
    return files


@click.command(
    name="video2book",
    help="Convert large video lecture libraries into comprehensive technical textbook chapters in Markdown.",
)
@click.option(
    "--gui",
    is_flag=True,
    default=False,
    help="Launch the video2book Desktop Studio GUI.",
)
@click.option(
    "--input-dir",
    "-i",
    required=False,
    default=None,
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    help="Path to the directory containing video files (recursive scan).",
)
@click.option(
    "--output-dir",
    "-o",
    default=Path("./output"),
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    help="Target directory for Markdown chapters and extracted images. [default: ./output]",
)
@click.option(
    "--model",
    "-m",
    default=DEFAULT_MODEL,
    show_default=True,
    help="Gemini model identifier from OpenRouter (e.g. google/gemini-2.5-flash).",
)
@click.option(
    "--list-models",
    is_flag=True,
    default=False,
    help="Fetch and list available Gemini models directly from https://openrouter.ai/models.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Validate files and API credentials without uploading or extracting.",
)
@click.option(
    "--force",
    "-f",
    is_flag=True,
    default=False,
    help="Force reprocessing of files even if marked COMPLETED in state.",
)
@click.option(
    "--api-key",
    envvar="OPENROUTER_API_KEY",
    help="OpenRouter API Key. Can also be set via OPENROUTER_API_KEY environment variable.",
)
@click.option(
    "--state-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Custom path to the state tracking JSON file. [default: <output-dir>/.video2book_state.json]",
)
@click.option(
    "--reset-failed",
    is_flag=True,
    default=False,
    help="Reset all FAILED entries to PENDING before executing.",
)
@click.option(
    "--poll-interval",
    default=5.0,
    show_default=True,
    type=float,
    help="Seconds to wait between polling remote file processing state.",
)
@click.option(
    "--extensions",
    default="mp4,mkv,mov,webm,avi",
    show_default=True,
    help="Comma-separated video extensions to scan.",
)
@click.option(
    "--pdf/--no-pdf",
    default=True,
    show_default=True,
    help="Automatically compile publication-ready PDF book via headless Edge/Chromium.",
)
@click.option(
    "--standalone-html/--no-standalone-html",
    default=False,
    show_default=True,
    help="Embed figures directly as Base64 in HTML for single-file sharing.",
)
@click.option(
    "--persian/--no-persian",
    default=True,
    show_default=True,
    help="Automatically translate chapter to academic Persian (preserving technical terms in English) and compile Persian web book & PDF.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Enable verbose debug logging.",
)
def main(
    gui: bool,
    input_dir: Optional[Path],
    output_dir: Path,
    model: str,
    list_models: bool,
    dry_run: bool,
    force: bool,
    api_key: Optional[str],
    state_file: Optional[Path],
    reset_failed: bool,
    poll_interval: float,
    extensions: str,
    pdf: bool,
    standalone_html: bool,
    persian: bool,
    verbose: bool,
) -> int:
    """Execute the video2book pipeline."""
    if list_models:
        console.print("[cyan]Querying available Gemini models from https://openrouter.ai/models...[/cyan]")
        models = fetch_openrouter_gemini_models()
        table = Table(title="Available Gemini Models on OpenRouter (https://openrouter.ai/models)")
        table.add_column("Model ID", style="bold cyan")
        table.add_column("Display Name", style="bold")
        table.add_column("Video Support", justify="center")
        table.add_column("Context Window", justify="right")

        for m in models:
            vid_str = "[bold green]Yes[/bold green]" if m.get("video_capable") else "[dim]Text/Image[/dim]"
            ctx = m.get("context_length")
            ctx_str = f"{ctx:,}" if ctx else "-"
            table.add_row(m["id"], m["name"], vid_str, ctx_str)

        console.print(table)
        return 0

    if gui:
        from video2book.gui import launch_gui
        return launch_gui()

    if not input_dir:
        console.print(
            Panel(
                "[bold red]Error: Missing required option '--input-dir' / '-i'.[/bold red]\n\n"
                "To launch the interactive desktop interface:\n"
                "  • [cyan]video2book --gui[/cyan]\n"
                "  • [cyan]video2book-gui[/cyan]\n\n"
                "For CLI help:\n"
                "  • [cyan]video2book --help[/cyan]\n",
                title="Missing Input Directory",
                border_style="red",
            )
        )
        sys.exit(2)

    setup_logging(verbose)

    # 1. Startup ffmpeg verification
    ffmpeg_ok, ffmpeg_info = check_ffmpeg_installed()
    if not ffmpeg_ok:
        console.print(
            Panel(
                "[bold red]Error: System ffmpeg is required but not found in PATH.[/bold red]\n\n"
                "Please install ffmpeg before running video2book:\n"
                "  • Windows: [cyan]winget install Gyan.FFmpeg[/cyan] or [cyan]choco install ffmpeg[/cyan]\n"
                "  • macOS:   [cyan]brew install ffmpeg[/cyan]\n"
                "  • Linux:   [cyan]sudo apt update && sudo apt install ffmpeg[/cyan]\n",
                title="Missing Dependency",
                border_style="red",
            )
        )
        sys.exit(1)

    # 2. Setup directories and state tracking
    output_dir.mkdir(parents=True, exist_ok=True)
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    actual_state_path = state_file or (output_dir / StateManager.DEFAULT_STATE_FILENAME)
    state = StateManager(actual_state_path)

    if reset_failed:
        num_reset = state.reset_failed()
        console.print(f"[yellow]Reset {num_reset} previously failed entries to PENDING.[/yellow]")

    # 3. Discover video files
    ext_list = [e.strip() for e in extensions.split(",") if e.strip()]
    video_files = discover_video_files(input_dir, ext_list)

    if not video_files:
        console.print(f"[yellow]No video files matching ({extensions}) found in {input_dir}[/yellow]")
        return 0

    # 4. Display startup banner
    total_size_bytes = sum(p.stat().st_size for p in video_files if p.exists())
    total_size_gb = total_size_bytes / (1024**3)

    summary_table = Table(title="video2book Pipeline Configuration", show_header=False)
    summary_table.add_row("Provider", "OpenRouter (https://openrouter.ai)")
    summary_table.add_row("Input Directory", str(input_dir.resolve()))
    summary_table.add_row("Output Directory", str(output_dir.resolve()))
    summary_table.add_row("Model", model)
    summary_table.add_row("Video Files Found", f"{len(video_files)} files ({total_size_gb:.2f} GB)")
    summary_table.add_row("State File", str(actual_state_path.resolve()))
    summary_table.add_row("ffmpeg Binary", ffmpeg_info or "Available")
    summary_table.add_row("Dry Run Mode", "[bold yellow]ENABLED[/bold yellow]" if dry_run else "Disabled")
    summary_table.add_row("Force Reprocess", "[bold magenta]ENABLED[/bold magenta]" if force else "Disabled")
    console.print(Panel(summary_table, border_style="cyan"))

    # 5. Initialize or validate OpenRouter client
    ingestor: Optional[OpenRouterIngestor] = None
    if not dry_run:
        actual_key = api_key or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not actual_key:
            console.print(
                Panel(
                    "[bold red]Error: OPENROUTER_API_KEY environment variable is not set.[/bold red]\n\n"
                    "Provide your key via:\n"
                    "  • Environment: [cyan]export OPENROUTER_API_KEY='your_api_key'[/cyan] (or Windows Powershell: [cyan]$env:OPENROUTER_API_KEY='your_key'[/cyan])\n"
                    "  • CLI flag:    [cyan]--api-key your_api_key[/cyan]\n\n"
                    "Get an API key at: [cyan]https://openrouter.ai/keys[/cyan]\n"
                    "Browse models at:  [cyan]https://openrouter.ai/models[/cyan]\n",
                    title="Missing OpenRouter API Key",
                    border_style="red",
                )
            )
            sys.exit(1)
        try:
            ingestor = OpenRouterIngestor(
                api_key=actual_key,
                model=model,
                poll_interval=poll_interval,
            )
            _, auth_info = ingestor.validate_connection()
            console.print(f"[green][OK] OpenRouter API connection verified for {model}: {auth_info}[/green]")
            if "LOW BALANCE" in auth_info:
                console.print(f"[bold yellow][WARNING] {auth_info}[/bold yellow]")
        except Exception as e:
            console.print(f"[bold red]Failed to initialize OpenRouter client: {e}[/bold red]")
            sys.exit(1)

    # 6. Filter files to process
    tasks_to_process: List[Path] = []
    skipped_count = 0

    for vf in video_files:
        # Pre-register in state
        state.register_video(vf)
        if state.should_process(vf, force=force):
            tasks_to_process.append(vf)
        else:
            skipped_count += 1

    console.print(
        f"Processing queue: [bold green]{len(tasks_to_process)} to process[/bold green], "
        f"[bold blue]{skipped_count} already completed (skipped)[/bold blue]."
    )

    if dry_run:
        console.print("\n[bold yellow]--- DRY RUN AUDIT ---[/bold yellow]")
        dry_table = Table(show_header=True, header_style="bold magenta")
        dry_table.add_column("#", justify="right")
        dry_table.add_column("Video Filename")
        dry_table.add_column("Size (MB)", justify="right")
        dry_table.add_column("Current State")
        dry_table.add_column("Action")

        for idx, vf in enumerate(video_files, 1):
            rec = state.get_record(vf)
            status_str = rec.status.value if rec else "PENDING"
            action_str = "Process" if state.should_process(vf, force=force) else "Skip (Already Completed)"
            size_mb = vf.stat().st_size / (1024 * 1024)
            dry_table.add_row(str(idx), vf.name, f"{size_mb:.1f}", status_str, action_str)

        console.print(dry_table)
        console.print("[green]Dry run complete. No files were modified or uploaded.[/green]")
        return 0

    if not tasks_to_process:
        console.print("[green]All video files have already been successfully processed![/green]")
        return 0

    # 7. Main Execution Loop with Rich Progress
    completed_this_run = 0
    failed_this_run = 0
    total_snapshots_extracted = 0
    start_run_time = time.time()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        overall_task = progress.add_task(
            "[cyan]Total Progress",
            total=len(video_files),
            completed=skipped_count,
        )
        step_task = progress.add_task("[yellow]Current Status", total=None)

        for video_path in tasks_to_process:
            video_name = video_path.name
            target_md_path = output_dir / f"{video_path.stem}.md"

            progress.update(
                step_task,
                description=f"[yellow]Processing {video_name}...",
            )

            file_start_time = time.time()
            state.mark_in_progress(video_path)

            def status_cb(msg: str) -> None:
                progress.update(step_task, description=f"[yellow]{video_name}: {msg}")

            try:
                assert ingestor is not None
                # A. Multimodal Gemini Chapter Generation
                raw_markdown = ingestor.upload_and_generate(
                    video_path=video_path,
                    status_callback=status_cb,
                )

                # B. Extract snapshots and rewrite Markdown image links
                status_cb("Extracting snapshots and rewriting Markdown...")
                processed_md, snap_count = process_markdown_snapshots(
                    markdown_content=raw_markdown,
                    video_path=video_path,
                    output_dir=output_dir,
                )

                # C. Save output Markdown chapter
                with open(target_md_path, "w", encoding="utf-8") as f:
                    f.write(processed_md)

                # D. Compile interactive HTML textbook
                status_cb("Compiling interactive HTML textbook...")
                target_html_path = output_dir / f"{video_path.stem}.html"
                render_html_chapter(
                    markdown_content=processed_md,
                    output_html_path=target_html_path,
                    video_stem=video_path.stem,
                    embed_base64_images=standalone_html,
                )

                # E. Compile publication PDF book
                target_pdf_path = None
                if pdf:
                    status_cb("Compiling publication PDF book via Edge/Chromium...")
                    target_pdf_path = output_dir / f"{video_path.stem}.pdf"
                    export_pdf_chapter(target_html_path, target_pdf_path)

                # F. Persian Technical Translation (Keeping technical terms in English)
                fa_note = ""
                if persian:
                    try:
                        status_cb("Translating chapter to Persian (preserving technical terms)...")
                        persian_md = ingestor.translate_to_persian(
                            markdown_content=processed_md,
                            status_callback=status_cb,
                        )

                        target_fa_md_path = output_dir / f"{video_path.stem}_fa.md"
                        with open(target_fa_md_path, "w", encoding="utf-8") as f:
                            f.write(persian_md)

                        # G. Compile Persian RTL Web Book
                        status_cb("Compiling Persian RTL web book...")
                        target_fa_html_path = output_dir / f"{video_path.stem}_fa.html"
                        render_html_chapter(
                            markdown_content=persian_md,
                            output_html_path=target_fa_html_path,
                            video_stem=video_path.stem,
                            embed_base64_images=standalone_html,
                            is_rtl=True,
                            lang="fa",
                        )

                        # H. Compile Persian RTL PDF Book
                        target_fa_pdf_path = None
                        if pdf:
                            status_cb("Compiling Persian PDF book via Edge/Chromium...")
                            target_fa_pdf_path = output_dir / f"{video_path.stem}_fa.pdf"
                            export_pdf_chapter(target_fa_html_path, target_fa_pdf_path)

                        fa_note = f" + Persian: [underline]{target_fa_md_path.name}[/underline], [underline]{target_fa_html_path.name}[/underline]"
                        if target_fa_pdf_path and target_fa_pdf_path.exists():
                            fa_note += f", [underline]{target_fa_pdf_path.name}[/underline]"
                    except Exception as e:
                        logger.warning(f"Persian translation encountered an issue: {e}")
                        fa_note = f" (Persian translation skipped: {e})"

                elapsed = time.time() - file_start_time
                state.mark_completed(
                    path=video_path,
                    output_md_path=target_md_path,
                    snapshots_count=snap_count,
                    duration_seconds=elapsed,
                )

                completed_this_run += 1
                total_snapshots_extracted += snap_count
                progress.advance(overall_task, 1)

                pdf_note = f", [underline]{target_pdf_path.name}[/underline]" if (target_pdf_path and target_pdf_path.exists()) else ""
                console.log(
                    f"[bold green][OK] Completed[/bold green] {video_name} "
                    f"-> [underline]{target_md_path.name}[/underline], "
                    f"[underline]{target_html_path.name}[/underline]{pdf_note}"
                    f"{fa_note} "
                    f"({snap_count} snapshots, {elapsed:.1f}s)"
                )

            except Exception as e:
                err_msg = str(e)
                state.mark_failed(video_path, err_msg)
                failed_this_run += 1
                progress.advance(overall_task, 1)
                console.log(f"[bold red][FAIL] Failed[/bold red] {video_name}: {err_msg}")
                logging.exception(f"Error processing {video_name}")

        progress.update(step_task, description="[green]Done!")

    # 8. Final Run Summary Table
    total_run_duration = time.time() - start_run_time
    summary_table = Table(title="Batch Run Summary", border_style="green")
    summary_table.add_column("Metric", style="bold")
    summary_table.add_column("Value")

    summary_table.add_row("Total Files in Library", str(len(video_files)))
    summary_table.add_row("Processed in this run", str(completed_this_run))
    summary_table.add_row("Skipped (already completed)", str(skipped_count))
    summary_table.add_row("Failed in this run", str(failed_this_run))
    summary_table.add_row("Total Figures Extracted", str(total_snapshots_extracted))
    summary_table.add_row("Total Run Duration", f"{total_run_duration:.1f}s ({total_run_duration/60:.1f} min)")
    summary_table.add_row("Output Directory", str(output_dir.resolve()))

    console.print(Panel(summary_table, border_style="green"))

    return 0 if failed_this_run == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
