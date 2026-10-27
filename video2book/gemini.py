"""Gemini Multimodal Ingestion and Chapter Generation.

Uses the modern google-genai SDK (from google import genai) to upload
video files to the Gemini File API, poll until ACTIVE, invoke multimodal
generation with exponential backoff resiliency, and guarantee remote file cleanup.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Callable, Optional

from google import genai
from google.genai import types
from google.genai.errors import APIError, ServerError
import httpx
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.5-flash"

SYSTEM_INSTRUCTION = """\
You are a Principal Software Engineer, Computer Science Professor, and Senior Technical Book Author.
Your mission is to convert this video lecture into an authoritative, unabridged, graduate-level textbook chapter in Markdown format.

Follow these strict pedagogical and formatting guidelines:

1. Unabridged Technical Depth:
   - Provide exhaustive, rigorous explanations of every single concept, theorem, algorithm, and system mechanism discussed in the lecture.
   - Do NOT summarize, abbreviate, or skim over complex points. Treat this as a definitive textbook chapter.
   - Include theoretical foundations, historical context where relevant, design trade-offs, performance implications, and edge cases.

2. Code Blocks & Syntax:
   - Write out all code, CLI commands, configuration snippets, and pseudocode completely and verbatim.
   - Every code block MUST have an explicit language identifier (e.g., ```python, ```bash, ```rust, ```sql, ```yaml).
   - Add line-by-line pedagogical annotations and explanatory comments inside and following the code blocks.

3. Visual Markers & Figure Extraction:
   - Crucial: Whenever the video presents architectural diagrams, flowcharts, data structures, whiteboard drawings, UI screens, benchmark charts, or visual slides that convey non-trivial information, you MUST insert an explicit snapshot marker at that exact moment:
     `[SNAP: HH:MM:SS | Detailed Caption]`
     (Use HH:MM:SS or MM:SS corresponding to the exact video timestamp).
   - The caption must be descriptive, explaining exactly what the diagram depicts (e.g., `[SNAP: 00:14:22 | Distributed Consensus Raft Leader Election State Machine]`).
   - Do NOT omit visual markers. Every critical architectural layout or slide must have a marker so that our downstream pipeline can extract the exact frame.

4. Textbook Organization & Markdown Formatting:
   - Title the chapter appropriately with `# Chapter: <Title>`
   - Use a clear hierarchy (`## Section`, `### Subsection`, `#### Deep Dive`).
   - Include mathematical formulations using LaTeX notation ($E = mc^2$, or $$...$$ display math blocks).
   - Use Obsidian / GFM callout boxes where appropriate (`> [!NOTE]`, `> [!TIP]`, `> [!WARNING]`, `> [!IMPORTANT]`).
   - Conclude with a comprehensive "Review & Exercises / Discussion Questions" section.
"""

USER_PROMPT = """\
Please convert this entire video lecture into a comprehensive, unabridged technical textbook chapter in Markdown following the system instructions. Ensure all diagrams and key visual slides are marked with `[SNAP: HH:MM:SS | Detailed Caption]`.
"""


def is_transient_error(exception: BaseException) -> bool:
    """Predicate to determine if an error is transient and should trigger a retry."""
    if isinstance(exception, ServerError):
        return True

    if isinstance(exception, APIError):
        code = getattr(exception, "code", None)
        if code in (429, 500, 502, 503, 504):
            return True
        msg = str(exception).lower()
        if any(term in msg for term in ("429", "quota", "resource_exhausted", "unavailable", "rate limit")):
            return True

    if isinstance(exception, (httpx.TransportError, httpx.TimeoutException, ConnectionError, TimeoutError)):
        return True

    return False


def log_retry_attempt(retry_state: RetryCallState) -> None:
    """Log retry events with exponential backoff details."""
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    attempt = retry_state.attempt_number
    sleep_time = getattr(retry_state.next_action, "sleep", 0) if retry_state.next_action else 0
    logger.warning(
        f"[Retry attempt {attempt}] Transient error encountered: {exc}. "
        f"Backing off for {sleep_time:.2f}s before retrying..."
    )


class GeminiIngestor:
    """Manages video uploads, polling, chapter generation, retries, and cleanup."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        poll_interval: float = 5.0,
        max_retries: int = 6,
        system_instruction: Optional[str] = None,
        user_prompt: Optional[str] = None,
    ):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Gemini API key is required. Set the GEMINI_API_KEY environment variable "
                "or pass it via --api-key."
            )
        self.model = model
        self.poll_interval = poll_interval
        self.max_retries = max_retries
        self.system_instruction = system_instruction or SYSTEM_INSTRUCTION
        self.user_prompt = user_prompt or USER_PROMPT
        self.client = genai.Client(api_key=self.api_key)

    def validate_connection(self) -> bool:
        """Test the API key and client connectivity."""
        try:
            # Quick check by listing models or fetching model info
            _ = self.client.models.get(model=self.model)
            return True
        except Exception as e:
            logger.error(f"Failed to validate Gemini connection: {e}")
            raise

    def upload_and_generate(
        self,
        video_path: str | Path,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> str:
        """Upload video file, wait until ACTIVE, generate textbook chapter, and cleanup remote file.

        Guarantees deletion of the remote file via a `finally` block.
        """
        path = Path(video_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Video file not found: {path}")

        uploaded_file: Optional[types.File] = None

        try:
            # 1. Upload video
            if status_callback:
                status_callback(f"Uploading {path.name} to Gemini File API...")
            logger.info(f"Starting upload for {path} ({path.stat().st_size / (1024*1024):.1f} MB)")

            uploaded_file = self.client.files.upload(file=str(path))
            file_name = uploaded_file.name
            logger.info(f"Uploaded file resource name: {file_name}")

            # 2. Poll until ACTIVE
            if status_callback:
                status_callback(f"Waiting for remote video processing ({file_name})...")

            file_info = self._poll_file_active(file_name, status_callback)

            # 3. Generate Content with Resilient Exponential Backoff
            if status_callback:
                status_callback(f"Generating textbook chapter with {self.model}...")
            logger.info(f"Generating textbook chapter using model {self.model}")

            markdown_text = self._generate_with_retry(file_info)
            return markdown_text

        finally:
            # 4. Guaranteed Remote File Cleanup
            if uploaded_file and uploaded_file.name:
                self._cleanup_remote_file(uploaded_file.name, status_callback)

    def _poll_file_active(
        self,
        file_name: str,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> types.File:
        """Poll Gemini File API until the file is ACTIVE, raising if FAILED."""
        start_time = time.time()
        while True:
            file_info = self.client.files.get(name=file_name)
            state_raw = file_info.state
            state_name = getattr(state_raw, "name", str(state_raw))

            if state_name == "ACTIVE":
                logger.info(f"File {file_name} is ACTIVE and ready for generation.")
                return file_info

            if state_name == "FAILED":
                err = getattr(file_info, "error", "Unknown processing failure")
                raise RuntimeError(f"Gemini File API processing failed for {file_name}: {err}")

            elapsed = int(time.time() - start_time)
            msg = f"Processing video remotely... state={state_name} ({elapsed}s elapsed)"
            if status_callback:
                status_callback(msg)
            logger.debug(msg)

            time.sleep(self.poll_interval)

    def _generate_with_retry(self, file_info: types.File) -> str:
        """Execute model generation with exponential backoff on transient errors."""

        @retry(
            retry=retry_if_exception(is_transient_error),
            wait=wait_exponential_jitter(initial=4, max=60, jitter=2),
            stop=stop_after_attempt(self.max_retries),
            before_sleep=log_retry_attempt,
            reraise=True,
        )
        def _call_api() -> str:
            response = self.client.models.generate_content(
                model=self.model,
                contents=[file_info, self.user_prompt],
                config=types.GenerateContentConfig(
                    system_instruction=self.system_instruction,
                    temperature=0.2,
                ),
            )
            if not response.text:
                raise RuntimeError(
                    f"Gemini returned an empty response. Candidates: {getattr(response, 'candidates', None)}"
                )
            return response.text

        return _call_api()

    def _cleanup_remote_file(
        self,
        file_name: str,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Delete remote video file from Gemini cloud storage."""
        if status_callback:
            status_callback(f"Cleaning up remote file {file_name}...")
        try:
            self.client.files.delete(name=file_name)
            logger.info(f"Successfully deleted remote Gemini file: {file_name}")
        except Exception as e:
            logger.warning(f"Failed to delete remote Gemini file {file_name}: {e}")
