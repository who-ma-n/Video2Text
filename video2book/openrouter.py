"""OpenRouter Ingestion and Multimodal Chapter Generation.

Queries Google Gemini models through OpenRouter (https://openrouter.ai),
dynamically discovers available Gemini models from https://openrouter.ai/models,
and invokes multimodal chat completions with exponential backoff resiliency.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
import socket
import ssl
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import httpcore
except ImportError:
    httpcore = None

import httpx
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODELS_URL = f"{OPENROUTER_BASE_URL}/models"
OPENROUTER_CHAT_URL = f"{OPENROUTER_BASE_URL}/chat/completions"
OPENROUTER_AUTH_URL = f"{OPENROUTER_BASE_URL}/auth/key"

DEFAULT_MODEL = "google/gemini-2.5-flash"

# Standard browser User-Agent to avoid Cloudflare WAF bot-detection false-positives
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36 video2book/1.0"
)

# Hard payload limit for Google AI Studio is 20,000,000 bytes.
# Base64 expansion (1.33x) + JSON envelope requires raw video file <= 13.5 MB (~14,155,776 bytes).
SAFE_PAYLOAD_RAW_BYTES = int(13.5 * 1024 * 1024)

# Curated fallback models if network lookup fails
FALLBACK_GEMINI_MODELS: List[Dict[str, Any]] = [
    {"id": "google/gemini-2.5-flash", "name": "Google: Gemini 2.5 Flash", "video_capable": True},
    {"id": "google/gemini-2.5-pro", "name": "Google: Gemini 2.5 Pro", "video_capable": True},
    {"id": "google/gemini-3.8-flash", "name": "Google: Gemini 3.8 Flash", "video_capable": True},
    {"id": "google/gemini-3.7-flash", "name": "Google: Gemini 3.7 Flash", "video_capable": True},
    {"id": "google/gemini-3.5-flash", "name": "Google: Gemini 3.5 Flash", "video_capable": True},
    {"id": "google/gemini-3.5-flash-lite", "name": "Google: Gemini 3.5 Flash Lite", "video_capable": True},
    {"id": "google/gemini-2.0-flash-001", "name": "Google: Gemini 2.0 Flash", "video_capable": True},
]

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

PERSIAN_TRANSLATION_SYSTEM_PROMPT = """\
You are an expert computer science professor and senior software engineer translating an advanced technical textbook chapter from English to academic Persian (فارسی روان، شیوا، دقیق و دانشگاهی).

CRITICAL TRANSLATION MANDATES:
1. Technical Terms Preservation:
   - Keep all programming terms, computer science concepts, tools, frameworks, libraries, protocols, acronyms, and engineering jargon strictly in English (e.g., Virtual Environment, Interpreter, Bytecode, CPython, Language Server Protocol (LSP), Debug Adapter Protocol (DAP), Conda, Miniconda, Anaconda, Kernel, ZeroMQ, Socket, Site-Packages, Hard Link, Package Manager, SAT Solver, CLI, GUI, CI/CD, Transitive Dependencies, ABI, Thread, Process, Call Stack, Memory Allocation, Profiler).
   - If an idiomatic Persian explanation adds clarity, you may provide it alongside the English term (e.g., "محیط‌های ایزوله مجازی (Virtual Environments)" or simply "محیط مجازی Virtual Environment"), but NEVER replace or phonetically distort the standard English terminology.

2. Code Blocks & Identifiers:
   - Keep 100% of all code blocks (```python, ```bash, ```yaml, etc.) exactly in their original English form without translating variable names, comments, syntax, or commands.
   - Keep inline code identifiers (`sys.executable`, `globals()`, `test.py`, `export PATH=...`) untouched.

3. Figures, Images & Timestamps:
   - Preserve all Markdown image syntax `![Alt](images/...)` and relative file paths exactly as they are.
   - For figure captions: translate the descriptive text into Persian, but keep the timestamp marker format `(Timestamp: HH:MM:SS)` verbatim. Example:
     *Figure: دیاگرام معماری سیستم (Timestamp: 00:03:50)*

4. Mathematics & LaTeX:
   - Keep all LaTeX equations `$inline$` and `$$display$$` completely untouched.

5. Callout Boxes & Markdown Hierarchy:
   - Retain all callout markers verbatim: `> [!NOTE]`, `> [!WARNING]`, `> [!TIP]`, `> [!IMPORTANT]`, `> [!CAUTION]`. Translate only the inner explanation text.
   - Maintain all heading levels (#, ##, ###), bullet lists, and table formatting.

6. Tone & Quality:
   - Produce a fluent, natural, university-grade textbook chapter in Persian that an Iranian university student or professional software engineer will find crystal clear and engaging.
"""



def fetch_openrouter_gemini_models(timeout: float = 12.0) -> List[Dict[str, Any]]:
    """Fetch live Gemini models available on OpenRouter from https://openrouter.ai/api/v1/models.

    Filters for Google/Gemini models, sorts video-capable models first,
    and returns a structured list of model dicts.
    """
    try:
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
        }
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(OPENROUTER_MODELS_URL, headers=headers)
            resp.raise_for_status()
            data = resp.json().get("data", [])

        gemini_models: List[Dict[str, Any]] = []
        for m in data:
            mid = m.get("id", "")
            # Filter for Gemini/Google models, skip batch variants
            if ("gemini" in mid.lower() or "google/" in mid.lower()) and ":batch" not in mid:
                name = m.get("name", mid)
                arch = m.get("architecture", {})
                modality = arch.get("modality", "")
                is_video = "video" in modality.lower()
                context_len = m.get("context_length", 0)

                gemini_models.append({
                    "id": mid,
                    "name": name,
                    "video_capable": is_video,
                    "context_length": context_len,
                    "modality": modality,
                })

        # Sort: video-capable first, then alphabetical by id
        gemini_models.sort(key=lambda x: (not x["video_capable"], x["id"]))

        if gemini_models:
            return gemini_models

    except Exception as e:
        logger.warning(f"Failed to fetch models from OpenRouter: {e}. Falling back to default list.")

    return FALLBACK_GEMINI_MODELS


def validate_openrouter_key(api_key: str, timeout: float = 8.0) -> Tuple[bool, str]:
    """Test validity of an OpenRouter API key via https://openrouter.ai/api/v1/auth/key.

    Returns:
        (is_valid, message_or_label)
    """
    if not api_key:
        return False, "API key is empty."

    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "User-Agent": DEFAULT_USER_AGENT,
        }
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(OPENROUTER_AUTH_URL, headers=headers)
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                label = data.get("label", "Key Verified")
                limit = data.get("limit")
                usage = data.get("usage", 0)
                limit_remaining = data.get("limit_remaining")
                info = f"✓ Verified ({label})"
                if limit is not None:
                    info += f" [Usage: ${usage:.2f} / ${limit:.2f}]"
                    if limit_remaining is not None:
                        if limit_remaining < 1.00:
                            info += f" ⚠️ LOW BALANCE: ${limit_remaining:.2f} (<$1.00 required for video)"
                        else:
                            info += f" (Left: ${limit_remaining:.2f})"
                return True, info
            else:
                err_data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                err_msg = err_data.get("error", {}).get("message", f"HTTP {resp.status_code}")
                return False, f"✗ {err_msg}"
    except Exception as e:
        return False, f"✗ Connection failed: {e}"


def encode_video_to_data_url(path: str | Path) -> str:
    """Read a local video file and encode as a Base64 Data URL."""
    p = Path(path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"Video file not found: {p}")

    mime, _ = mimetypes.guess_type(str(p))
    if not mime or not mime.startswith("video/"):
        ext = p.suffix.lower()
        mime_map = {
            ".mp4": "video/mp4",
            ".webm": "video/webm",
            ".mkv": "video/x-matroska",
            ".mov": "video/quicktime",
            ".avi": "video/x-msvideo",
        }
        mime = mime_map.get(ext, "video/mp4")

    with open(p, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")

    return f"data:{mime};base64,{encoded}"


def is_openrouter_transient_error(exception: BaseException) -> bool:
    """Predicate to determine if an OpenRouter HTTP error is transient."""
    # 1. Transport, connection, and socket errors
    if isinstance(exception, (httpx.TransportError, httpx.TimeoutException, ConnectionError, TimeoutError, socket.error)):
        return True

    if httpcore and isinstance(exception, (httpcore.NetworkError, httpcore.RemoteProtocolError, httpcore.TimeoutException)):
        return True

    exc_str = str(exception).lower()
    if any(
        term in exc_str
        for term in (
            "getaddrinfo failed",
            "connection reset",
            "disconnected",
            "timed out",
            "remote protocol error",
            "handshake failed",
            "server disconnected without sending a response",
        )
    ):
        return True

    # 2. HTTP Status Code analysis
    if isinstance(exception, httpx.HTTPStatusError):
        code = exception.response.status_code
        if code in (429, 500, 502, 503, 504, 520, 521, 522, 524):
            return True

        msg = exception.response.text.lower()
        if any(term in msg for term in ("rate limit", "quota", "overloaded", "temporarily unavailable", "service unavailable")):
            return True

        # Treat transient Cloudflare WAF / Security Policy blips as retryable with backoff
        if code == 403 and any(
            term in msg for term in ("security policy", "access denied", "cloudflare", "cf-mitigated", "just a moment", "challenge")
        ):
            return True

    return False


def log_retry_attempt(retry_state: RetryCallState) -> None:
    """Log retry events with exponential backoff details."""
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    attempt = retry_state.attempt_number
    sleep_time = getattr(retry_state.next_action, "sleep", 0) if retry_state.next_action else 0
    logger.warning(
        f"[OpenRouter Retry attempt {attempt}] Transient error: {exc}. "
        f"Backing off for {sleep_time:.2f}s before retrying..."
    )


def get_video_duration(video_path: Path) -> float:
    """Extract duration in seconds using ffprobe."""
    import shutil
    import subprocess

    ffprobe_bin = shutil.which("ffprobe")
    if not ffprobe_bin:
        return 0.0
    try:
        res = subprocess.run(
            [
                ffprobe_bin,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=10.0,
        )
        return float(res.stdout.strip())
    except Exception:
        return 0.0


def optimize_video_for_llm(
    video_path: Path,
    max_size_bytes: int = SAFE_PAYLOAD_RAW_BYTES,
    status_callback: Optional[Callable[[str], None]] = None,
) -> Tuple[Path, bool]:
    """Optimize large video for multimodal LLM ingest to strictly respect provider payload limits.

    Google AI Studio enforces a hard 20,000,000 byte request limit. Accounting for Base64 (1.33x)
    and JSON envelope overhead, the raw MP4 file must NOT exceed ~13.5 MB.
    Uses multi-tier adaptive compression for long lectures or discussions.
    Returns (path_to_use, is_temporary_file).
    """
    if video_path.stat().st_size <= max_size_bytes:
        return video_path, False

    import shutil
    import subprocess
    import tempfile

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        logger.warning("FFmpeg not found; proceeding without optimization.")
        return video_path, False

    src_mb = video_path.stat().st_size / (1024 * 1024)
    duration = get_video_duration(video_path)
    dur_str = f", {int(duration // 60)}m{int(duration % 60)}s" if duration > 0 else ""

    if status_callback:
        status_callback(f"Compressing {src_mb:.1f}MB video{dur_str} for LLM ingest via FFmpeg...")
    logger.info(f"Compressing {video_path} ({src_mb:.1f} MB{dur_str}) to fit under {max_size_bytes / (1024 * 1024):.1f} MB...")

    # Pass 1: Standard proxy (480p, 1fps, crf 32, 48k audio)
    temp_fd, temp_out = tempfile.mkstemp(suffix=".mp4", prefix="llm_opt_")
    os.close(temp_fd)
    out_path = Path(temp_out)

    pass1_cmd = [
        ffmpeg_bin,
        "-y",
        "-i", str(video_path),
        "-vf", "scale=-2:480,fps=1",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "32",
        "-c:a", "aac",
        "-b:a", "48k",
        str(out_path),
    ]

    try:
        subprocess.run(pass1_cmd, capture_output=True, check=True)
        opt_size = out_path.stat().st_size
        opt_mb = opt_size / (1024 * 1024)

        if opt_size <= max_size_bytes:
            logger.info(f"Pass 1 optimized: {src_mb:.1f}MB -> {opt_mb:.1f}MB")
            if status_callback:
                status_callback(f"Compressed from {src_mb:.1f}MB down to {opt_mb:.1f}MB.")
            return out_path, True

        # Pass 2: Compact proxy for long videos (360p, 0.5fps, crf 36, 24k mono audio)
        logger.info(f"Pass 1 output ({opt_mb:.1f}MB) exceeds limit. Executing Pass 2 (360p, 0.5fps)...")
        if status_callback:
            status_callback(f"Adaptive compaction for long video ({opt_mb:.1f}MB -> target <=13.5MB)...")

        temp_fd2, temp_out2 = tempfile.mkstemp(suffix=".mp4", prefix="llm_opt_p2_")
        os.close(temp_fd2)
        out_path2 = Path(temp_out2)

        pass2_cmd = [
            ffmpeg_bin,
            "-y",
            "-i", str(video_path),
            "-vf", "scale=-2:360,fps=0.5",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "36",
            "-c:a", "aac",
            "-b:a", "24k",
            "-ac", "1",
            str(out_path2),
        ]
        subprocess.run(pass2_cmd, capture_output=True, check=True)
        opt2_size = out_path2.stat().st_size
        opt2_mb = opt2_size / (1024 * 1024)
        out_path.unlink(missing_ok=True)

        if opt2_size <= max_size_bytes:
            logger.info(f"Pass 2 optimized: {src_mb:.1f}MB -> {opt2_mb:.1f}MB")
            if status_callback:
                status_callback(f"Compressed from {src_mb:.1f}MB down to {opt2_mb:.1f}MB.")
            return out_path2, True

        # Pass 3: Strict bitrate-capped compression for extremely long discussions / Q&As
        logger.info(f"Pass 2 output ({opt2_mb:.1f}MB) still exceeds limit. Executing Pass 3 with target bitrate...")
        if status_callback:
            status_callback("Applying strict bitrate-capped compaction for maximum compression...")

        temp_fd3, temp_out3 = tempfile.mkstemp(suffix=".mp4", prefix="llm_opt_p3_")
        os.close(temp_fd3)
        out_path3 = Path(temp_out3)

        effective_dur = duration if duration > 30 else 3600.0  # fallback to 1h if unknown
        # Target raw 12.0 MB to leave a 1.5MB buffer under the 13.5MB ceiling
        target_total_kbps = max(20, int((12.0 * 1024 * 8) / effective_dur))
        audio_kbps = min(20, max(12, int(target_total_kbps * 0.35)))
        video_kbps = max(10, target_total_kbps - audio_kbps)

        pass3_cmd = [
            ffmpeg_bin,
            "-y",
            "-i", str(video_path),
            "-vf", "scale=-2:320,fps=0.25",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-b:v", f"{video_kbps}k",
            "-maxrate", f"{int(video_kbps * 1.3)}k",
            "-bufsize", f"{video_kbps * 2}k",
            "-c:a", "aac",
            "-b:a", f"{audio_kbps}k",
            "-ac", "1",
            str(out_path3),
        ]
        subprocess.run(pass3_cmd, capture_output=True, check=True)
        opt3_mb = out_path3.stat().st_size / (1024 * 1024)
        out_path2.unlink(missing_ok=True)

        logger.info(f"Pass 3 optimized: {src_mb:.1f}MB -> {opt3_mb:.1f}MB")
        if status_callback:
            status_callback(f"Compressed from {src_mb:.1f}MB down to {opt3_mb:.1f}MB.")
        return out_path3, True

    except Exception as e:
        logger.warning(f"FFmpeg optimization encountered error: {e}. Falling back to original.")
        if out_path.exists():
            out_path.unlink()
        return video_path, False


class OpenRouterIngestor:
    """Manages video chapter generation through OpenRouter Gemini models."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        poll_interval: float = 5.0,  # Maintained for interface parity
        max_retries: int = 7,
        system_instruction: Optional[str] = None,
        user_prompt: Optional[str] = None,
        timeout: float = 600.0,
    ):
        self.api_key = (
            api_key
            or os.environ.get("OPENROUTER_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
        )
        if not self.api_key:
            raise ValueError(
                "OpenRouter API key is required. Set the OPENROUTER_API_KEY environment variable "
                "or pass it via --api-key."
            )
        self.model = model
        self.poll_interval = poll_interval
        self.max_retries = max_retries
        self.system_instruction = system_instruction or SYSTEM_INSTRUCTION
        self.user_prompt = user_prompt or USER_PROMPT
        self.timeout = timeout

    def validate_connection(self) -> Tuple[bool, str]:
        """Test the OpenRouter API key and model validity."""
        valid, msg = validate_openrouter_key(self.api_key)
        if not valid:
            raise ValueError(f"OpenRouter connection check failed: {msg}")
        return valid, msg

    def upload_and_generate(
        self,
        video_path: str | Path,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> str:
        """Encode local video, send multimodal request to OpenRouter, and return Markdown text."""
        path = Path(video_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Video file not found: {path}")

        # 1. Check and optimize large files (>13.5MB) for API payload limits
        ingest_path, is_temp = optimize_video_for_llm(
            path,
            max_size_bytes=SAFE_PAYLOAD_RAW_BYTES,
            status_callback=status_callback,
        )

        try:
            file_size_mb = ingest_path.stat().st_size / (1024 * 1024)

            # 2. Base64 encoding
            if status_callback:
                status_callback(f"Encoding {ingest_path.name} ({file_size_mb:.1f} MB) to Base64...")
            logger.info(f"Encoding {ingest_path} ({file_size_mb:.1f} MB) for OpenRouter...")

            video_data_url = encode_video_to_data_url(ingest_path)

            # 3. Call OpenRouter API with Retry
            if status_callback:
                status_callback(f"Sending prompt to OpenRouter ({self.model})...")
            logger.info(f"Dispatching completion to OpenRouter with model {self.model}...")

            markdown_text = self._call_openrouter_with_retry(
                video_data_url=video_data_url,
                status_callback=status_callback,
            )
            return markdown_text

        finally:
            if is_temp and ingest_path.exists():
                try:
                    ingest_path.unlink()
                except Exception:
                    pass

    def _call_openrouter_with_retry(
        self,
        video_data_url: str,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> str:
        """Execute chat completion with exponential backoff on transient errors."""

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/video2book",
            "X-Title": "video2book",
            "User-Agent": DEFAULT_USER_AGENT,
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": self.system_instruction,
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "video_url",
                            "video_url": {
                                "url": video_data_url,
                            },
                        },
                        {
                            "type": "text",
                            "text": self.user_prompt,
                        },
                    ],
                },
            ],
            "temperature": 0.2,
        }

        @retry(
            retry=retry_if_exception(is_openrouter_transient_error),
            wait=wait_exponential_jitter(initial=5, max=90, jitter=3),
            stop=stop_after_attempt(self.max_retries),
            before_sleep=log_retry_attempt,
            reraise=True,
        )
        def _call_api() -> str:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(OPENROUTER_CHAT_URL, headers=headers, json=payload)
                if response.status_code != 200:
                    try:
                        err_json = response.json()
                        err_msg = err_json.get("error", {}).get("message", response.text)
                    except Exception:
                        err_msg = response.text
                    raise httpx.HTTPStatusError(
                        f"OpenRouter Error ({response.status_code}): {err_msg}",
                        request=response.request,
                        response=response,
                    )

                data = response.json()
                choices = data.get("choices", [])
                if not choices:
                    raise RuntimeError(f"OpenRouter returned no completion choices. Response: {data}")

                content = choices[0].get("message", {}).get("content")
                if not content:
                    raise RuntimeError(f"OpenRouter returned an empty message content: {data}")

                return content

        return _call_api()

    def translate_to_persian(
        self,
        markdown_content: str,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> str:
        """Translate technical English markdown into Persian while preserving technical words in English."""
        if not markdown_content or not markdown_content.strip():
            return ""

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/video2book",
            "X-Title": "video2book",
            "User-Agent": DEFAULT_USER_AGENT,
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": PERSIAN_TRANSLATION_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": (
                        "Translate the following English textbook chapter into academic Persian according to your system instructions. "
                        "Preserve all Markdown formatting, image links, and technical terms in English:\n\n"
                        f"{markdown_content}"
                    ),
                },
            ],
            "temperature": 0.2,
        }

        if status_callback:
            status_callback("Translating chapter to Persian (preserving technical terms in English)...")
        logger.info(f"Dispatching Persian translation to OpenRouter with model {self.model}...")

        @retry(
            retry=retry_if_exception(is_openrouter_transient_error),
            wait=wait_exponential_jitter(initial=5, max=90, jitter=3),
            stop=stop_after_attempt(self.max_retries),
            before_sleep=log_retry_attempt,
            reraise=True,
        )
        def _call_translate() -> str:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(OPENROUTER_CHAT_URL, headers=headers, json=payload)
                if response.status_code != 200:
                    try:
                        err_json = response.json()
                        err_msg = err_json.get("error", {}).get("message", response.text)
                    except Exception:
                        err_msg = response.text
                    raise httpx.HTTPStatusError(
                        f"OpenRouter Error ({response.status_code}): {err_msg}",
                        request=response.request,
                        response=response,
                    )

                data = response.json()
                choices = data.get("choices", [])
                if not choices:
                    raise RuntimeError(f"OpenRouter returned no completion choices. Response: {data}")

                content = choices[0].get("message", {}).get("content")
                if not content:
                    raise RuntimeError(f"OpenRouter returned empty message content: {data}")

                return content

        return _call_translate()
