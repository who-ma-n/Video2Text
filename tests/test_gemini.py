"""Unit tests for gemini.py ingestion, retries, and cleanup."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from google.genai.errors import APIError, ServerError
import httpx

from video2book.gemini import GeminiIngestor, is_transient_error


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="Gemini API key is required"):
        GeminiIngestor(api_key=None)


def test_is_transient_error():
    assert is_transient_error(ServerError(code=503, response_json={}, response=None)) is True
    assert is_transient_error(APIError(code=429, response_json={}, response=None)) is True
    assert is_transient_error(httpx.TransportError("connection dropped")) is True
    assert is_transient_error(TimeoutError("timed out")) is True
    assert is_transient_error(ValueError("invalid arguments")) is False


def test_upload_polling_and_guaranteed_cleanup(tmp_path: Path):
    dummy_video = tmp_path / "test_video.mp4"
    dummy_video.write_bytes(b"dummy video data")

    with patch("video2book.gemini.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        # Mock upload returning a File
        uploaded_file = MagicMock()
        uploaded_file.name = "files/test12345"
        mock_client.files.upload.return_value = uploaded_file

        # Mock get returning PROCESSING then ACTIVE
        state_processing = MagicMock()
        state_processing.name = "PROCESSING"
        file_processing = MagicMock()
        file_processing.state = state_processing

        state_active = MagicMock()
        state_active.name = "ACTIVE"
        file_active = MagicMock()
        file_active.state = state_active

        mock_client.files.get.side_effect = [file_processing, file_active]

        # Mock generation response
        gen_response = MagicMock()
        gen_response.text = "# Chapter 1\nGenerated textbook content"
        mock_client.models.generate_content.return_value = gen_response

        ingestor = GeminiIngestor(api_key="fake-key", poll_interval=0.01)
        result = ingestor.upload_and_generate(dummy_video)

        assert result == "# Chapter 1\nGenerated textbook content"
        mock_client.files.upload.assert_called_once()
        assert mock_client.files.get.call_count == 2
        # Verify remote file deletion cleanup was called
        mock_client.files.delete.assert_called_once_with(name="files/test12345")


def test_cleanup_called_even_on_generation_failure(tmp_path: Path):
    dummy_video = tmp_path / "failing_video.mp4"
    dummy_video.write_bytes(b"dummy video data")

    with patch("video2book.gemini.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        uploaded_file = MagicMock()
        uploaded_file.name = "files/fail_cleanup"
        mock_client.files.upload.return_value = uploaded_file

        state_active = MagicMock()
        state_active.name = "ACTIVE"
        file_active = MagicMock()
        file_active.state = state_active
        mock_client.files.get.return_value = file_active

        # Generation raises an unrecoverable error
        mock_client.models.generate_content.side_effect = RuntimeError("Generation exploded")

        ingestor = GeminiIngestor(api_key="fake-key", poll_interval=0.01, max_retries=1)

        with pytest.raises(RuntimeError, match="Generation exploded"):
            ingestor.upload_and_generate(dummy_video)

        # Cleanup MUST still be called in finally block
        mock_client.files.delete.assert_called_once_with(name="files/fail_cleanup")
