"""Unit tests for OpenRouter ingestion, model fetching, and authentication."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import httpx
import pytest

from video2book.openrouter import (
    DEFAULT_MODEL,
    OpenRouterIngestor,
    encode_video_to_data_url,
    fetch_openrouter_gemini_models,
    is_openrouter_transient_error,
    validate_openrouter_key,
)


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OpenRouter API key is required"):
        OpenRouterIngestor(api_key=None)


def test_encode_video_to_data_url(tmp_path: Path):
    dummy_video = tmp_path / "lecture.mp4"
    dummy_video.write_bytes(b"fake video mp4 payload")

    data_url = encode_video_to_data_url(dummy_video)
    assert data_url.startswith("data:video/mp4;base64,")
    assert len(data_url) > len("data:video/mp4;base64,")


def test_validate_openrouter_key_success():
    with patch("httpx.Client.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {"label": "dev-key", "usage": 1.25, "limit": 50.0}
        }
        mock_get.return_value = mock_resp

        valid, msg = validate_openrouter_key("sk-or-v1-valid")
        assert valid is True
        assert "Verified" in msg
        assert "dev-key" in msg


def test_validate_openrouter_key_invalid():
    with patch("httpx.Client.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.json.return_value = {"error": {"message": "Invalid API key"}}
        mock_get.return_value = mock_resp

        valid, msg = validate_openrouter_key("sk-or-v1-bad")
        assert valid is False
        assert "Invalid API key" in msg


def test_fetch_openrouter_gemini_models():
    mock_payload = {
        "data": [
            {
                "id": "google/gemini-2.5-flash",
                "name": "Google: Gemini 2.5 Flash",
                "context_length": 1048576,
                "architecture": {"modality": "text+image+file+audio+video->text"},
            },
            {
                "id": "google/gemini-2.5-flash:batch",
                "name": "Gemini Batch",
                "architecture": {"modality": "text->text"},
            },
            {
                "id": "openai/gpt-4o",
                "name": "GPT-4o",
                "architecture": {"modality": "text+image->text"},
            },
        ]
    }

    with patch("httpx.Client.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_payload
        mock_get.return_value = mock_resp

        models = fetch_openrouter_gemini_models()
        assert len(models) == 1  # Only google/gemini-2.5-flash, skipping batch & non-gemini
        assert models[0]["id"] == "google/gemini-2.5-flash"
        assert models[0]["video_capable"] is True


def test_is_openrouter_transient_error():
    req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    resp_429 = httpx.Response(429, request=req, text="rate limit exceeded")
    err_429 = httpx.HTTPStatusError("rate limit", request=req, response=resp_429)
    assert is_openrouter_transient_error(err_429) is True

    resp_400 = httpx.Response(400, request=req, text="bad request")
    err_400 = httpx.HTTPStatusError("bad request", request=req, response=resp_400)
    assert is_openrouter_transient_error(err_400) is False

    assert is_openrouter_transient_error(httpx.TransportError("network down")) is True


def test_openrouter_ingestor_generate_success(tmp_path: Path):
    dummy_video = tmp_path / "lecture.mp4"
    dummy_video.write_bytes(b"video bytes")

    mock_chat_response = {
        "choices": [
            {
                "message": {
                    "content": "# Chapter 1: Introduction\n\n[SNAP: 00:01:30 | Architecture Diagram]"
                }
            }
        ]
    }

    with patch("httpx.Client.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_chat_response
        mock_post.return_value = mock_resp

        ingestor = OpenRouterIngestor(api_key="sk-or-test", model="google/gemini-2.5-flash")
        result = ingestor.upload_and_generate(dummy_video)

        assert "# Chapter 1: Introduction" in result
        assert "[SNAP: 00:01:30 | Architecture Diagram]" in result
        mock_post.assert_called_once()
