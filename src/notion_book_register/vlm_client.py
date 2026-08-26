"""VLM provider clients for extracting ISBN-13 values from book images."""

from __future__ import annotations

import base64
import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from http.client import HTTPException
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from notion_book_register.vlm_prompt import (
    ISBN_EXTRACTION_RESPONSE_SCHEMA,
    IsbnExtractionResult,
    VlmPromptError,
    build_isbn_extraction_messages,
    parse_isbn_extraction_response,
)

OPENAI_RESPONSES_API_URL = "https://api.openai.com/v1/responses"
DEFAULT_OPENAI_VLM_MODEL = "gpt-5-mini"
_IMAGE_DETAILS = {"auto", "low", "high"}
_SUPPORTED_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


class VlmApiError(RuntimeError):
    """Raised when a VLM provider cannot be queried or parsed."""


class VlmProvider(Protocol):
    """Provider abstraction for ISBN extraction from a single image."""

    def extract_isbn13(self, image: bytes, *, mime_type: str) -> IsbnExtractionResult:
        """Extract a validated ISBN-13 candidate from image bytes."""


@dataclass(frozen=True, slots=True)
class OpenAiVlmConfig:
    """Configuration for OpenAI Responses API based VLM extraction."""

    api_key: str
    model: str = DEFAULT_OPENAI_VLM_MODEL
    timeout: float = 30.0
    image_detail: str = "auto"


class _Response(Protocol):
    def __enter__(self) -> _Response: ...

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None: ...

    def read(self) -> bytes: ...


class _Opener(Protocol):
    def __call__(self, request: Request, timeout: float) -> _Response: ...


class OpenAiVlmClient:
    """Small OpenAI Responses API client for ISBN extraction from images."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_OPENAI_VLM_MODEL,
        timeout: float = 30.0,
        image_detail: str = "auto",
        opener: _Opener = urlopen,
    ) -> None:
        config = _validate_config(
            OpenAiVlmConfig(
                api_key=api_key,
                model=model,
                timeout=timeout,
                image_detail=image_detail,
            )
        )
        self._config = config
        self._opener = opener

    @classmethod
    def from_env(
        cls,
        *,
        environ: Mapping[str, str] = os.environ,
        model: str | None = None,
        timeout: float = 30.0,
        image_detail: str = "auto",
        opener: _Opener = urlopen,
    ) -> OpenAiVlmClient:
        """Build a client from OPENAI_API_KEY and optional OPENAI_VLM_MODEL."""

        api_key = environ.get("OPENAI_API_KEY")
        if api_key is None:
            raise ValueError("OPENAI_API_KEY is required.")
        resolved_model = model or environ.get("OPENAI_VLM_MODEL") or DEFAULT_OPENAI_VLM_MODEL
        return cls(
            api_key,
            model=resolved_model,
            timeout=timeout,
            image_detail=image_detail,
            opener=opener,
        )

    def extract_isbn13(self, image: bytes, *, mime_type: str) -> IsbnExtractionResult:
        """Extract ISBN-13 from one image using the OpenAI Responses API."""

        if not image:
            raise ValueError("image must not be empty.")
        normalized_mime_type = _normalize_image_mime_type(mime_type)
        request = Request(
            OPENAI_RESPONSES_API_URL,
            data=json.dumps(
                _build_openai_response_body(
                    image,
                    mime_type=normalized_mime_type,
                    model=self._config.model,
                    image_detail=self._config.image_detail,
                ),
                ensure_ascii=False,
            ).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._config.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "notion-book-register/0.1",
            },
            method="POST",
        )

        payload = self._send_request(request)
        try:
            return parse_isbn_extraction_response(_extract_response_text(payload))
        except VlmPromptError as error:
            raise VlmApiError(
                f"OpenAI API returned invalid ISBN extraction output: {error}"
            ) from error

    def _send_request(self, request: Request) -> bytes:
        try:
            with self._opener(request, timeout=self._config.timeout) as response:
                return response.read()
        except HTTPError as error:
            try:
                error_payload = _read_http_error_payload(error)
            finally:
                _close_http_error(error)
            raise VlmApiError(_openai_http_error_message(error.code, error_payload)) from error
        except URLError as error:
            raise VlmApiError(f"Failed to connect to OpenAI API: {error.reason}") from error
        except TimeoutError as error:
            raise VlmApiError("Timed out while reading from OpenAI API.") from error
        except OSError as error:
            raise VlmApiError(f"Failed to read from OpenAI API: {error}") from error
        except HTTPException as error:
            raise VlmApiError(f"Failed to read from OpenAI API: {error}") from error


def _validate_config(config: OpenAiVlmConfig) -> OpenAiVlmConfig:
    api_key = config.api_key.strip()
    model = config.model.strip()
    image_detail = config.image_detail.strip()
    if not api_key:
        raise ValueError("OpenAI API key is required.")
    if not model:
        raise ValueError("OpenAI model is required.")
    if not math.isfinite(config.timeout) or config.timeout <= 0:
        raise ValueError("OpenAI timeout must be a positive finite value.")
    if image_detail not in _IMAGE_DETAILS:
        raise ValueError("image_detail must be auto, low, or high.")
    return OpenAiVlmConfig(
        api_key=api_key,
        model=model,
        timeout=config.timeout,
        image_detail=image_detail,
    )


def _build_openai_response_body(
    image: bytes,
    *,
    mime_type: str,
    model: str,
    image_detail: str,
) -> dict[str, Any]:
    messages = build_isbn_extraction_messages()
    input_messages = []
    for message in messages:
        input_messages.append(
            {
                "role": message["role"],
                "content": [
                    {
                        "type": "input_text",
                        "text": message["content"],
                    }
                ],
            }
        )
    input_messages[-1]["content"].append(
        {
            "type": "input_image",
            "image_url": _image_data_url(image, mime_type=mime_type),
            "detail": image_detail,
        }
    )

    return {
        "model": model,
        "input": input_messages,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "isbn_extraction",
                "schema": ISBN_EXTRACTION_RESPONSE_SCHEMA,
                "strict": True,
            }
        },
        "store": False,
    }


def _image_data_url(image: bytes, *, mime_type: str) -> str:
    encoded_image = base64.b64encode(image).decode("ascii")
    return f"data:{mime_type};base64,{encoded_image}"


def _normalize_image_mime_type(value: str) -> str:
    mime_type = value.strip().lower()
    if not mime_type:
        raise ValueError("mime_type is required.")
    if mime_type not in _SUPPORTED_IMAGE_MIME_TYPES:
        supported = ", ".join(sorted(_SUPPORTED_IMAGE_MIME_TYPES))
        raise ValueError(f"mime_type must be one of: {supported}.")
    return mime_type


def _extract_response_text(payload: bytes) -> str:
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VlmApiError("OpenAI API returned invalid JSON.") from error
    if not isinstance(data, dict):
        raise VlmApiError("OpenAI API response must be a JSON object.")

    _raise_for_response_error(data)

    output_text = data.get("output_text")
    if isinstance(output_text, str):
        return output_text

    for item in _iter_output_items(data):
        for content in _iter_content_items(item):
            text = content.get("text")
            if content.get("type") == "output_text" and isinstance(text, str):
                return text

    raise VlmApiError("OpenAI API response is missing output text.")


def _raise_for_response_error(data: dict[str, Any]) -> None:
    status = data.get("status")
    if status not in {"failed", "incomplete"}:
        return

    detail = _response_error_detail(data)
    if detail is None:
        raise VlmApiError(f"OpenAI API response {status}.")
    raise VlmApiError(f"OpenAI API response {status}: {detail}")


def _response_error_detail(data: dict[str, Any]) -> str | None:
    for field_name in ("error", "incomplete_details"):
        value = data.get(field_name)
        if not isinstance(value, dict):
            continue
        message = value.get("message") or value.get("reason")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return None


def _iter_output_items(data: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    output = data.get("output")
    if not isinstance(output, list):
        return ()
    return tuple(item for item in output if isinstance(item, dict))


def _iter_content_items(item: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    content = item.get("content")
    if not isinstance(content, list):
        return ()
    return tuple(content_item for content_item in content if isinstance(content_item, dict))


def _read_http_error_payload(error: HTTPError) -> bytes:
    try:
        return error.read()
    except (AttributeError, HTTPException, OSError):
        return b""


def _close_http_error(error: HTTPError) -> None:
    try:
        error.close()
    except (AttributeError, HTTPException, OSError):
        return


def _openai_http_error_message(status_code: int, payload: bytes) -> str:
    detail = _openai_error_detail(payload)
    if detail is None:
        return f"OpenAI API returned HTTP {status_code}."
    return f"OpenAI API returned HTTP {status_code}: {detail}"


def _openai_error_detail(payload: bytes) -> str | None:
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    error = data.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return None
