"""Unit tests for GUI pipeline worker thread."""

import queue
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from video2book.gui.worker import PipelineWorker


def test_worker_dry_run_success(tmp_path: Path):
    v1 = tmp_path / "lec1.mp4"
    v2 = tmp_path / "lec2.mp4"
    v1.write_bytes(b"data1")
    v2.write_bytes(b"data2")
    out = tmp_path / "output"

    event_q = queue.Queue()

    with patch("video2book.gui.worker.check_ffmpeg_installed", return_value=(True, "ffmpeg 7.0")):
        worker = PipelineWorker(
            event_queue=event_q,
            video_files=[v1, v2],
            output_dir=out,
            dry_run=True,
        )
        worker.start()
        worker.join(timeout=5)

    events = []
    while not event_q.empty():
        events.append(event_q.get())

    kinds = [e.kind for e in events]
    assert "DRY_RUN_FINISHED" in kinds
    assert "BATCH_FINISHED" in kinds

    dry_event = next(e for e in events if e.kind == "DRY_RUN_FINISHED")
    assert len(dry_event.data["items"]) == 2


def test_worker_ffmpeg_missing(tmp_path: Path):
    event_q = queue.Queue()
    with patch("video2book.gui.worker.check_ffmpeg_installed", return_value=(False, None)):
        worker = PipelineWorker(
            event_queue=event_q,
            video_files=[],
            output_dir=tmp_path / "output",
        )
        worker.start()
        worker.join(timeout=5)

    events = []
    while not event_q.empty():
        events.append(event_q.get())

    kinds = [e.kind for e in events]
    assert "ERROR" in kinds
    assert "BATCH_FINISHED" in kinds


def test_worker_cancellation(tmp_path: Path):
    v1 = tmp_path / "lec1.mp4"
    v1.write_bytes(b"data1")
    out = tmp_path / "output"

    event_q = queue.Queue()

    worker = PipelineWorker(
        event_queue=event_q,
        video_files=[v1],
        output_dir=out,
        dry_run=True,
    )
    worker.cancel()
    assert worker.is_cancelled()
