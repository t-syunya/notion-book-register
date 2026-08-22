"""Client for creating book pages in Notion."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from http.client import HTTPException
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from notion_book_register.models import Book
from notion_book_register.notion_mapping import (
    DEFAULT_READING_STATUS,
    book_to_notion_properties,
)

NOTION_API_URL = "https://api.notion.com/v1"
NOTION_VERSION = "2026-03-11"
NOTION_TEXT_CONTENT_LIMIT = 2000


class NotionApiError(RuntimeError):
    """Raised when the Notion API cannot be queried or parsed."""


@dataclass(frozen=True, slots=True)
class CreatedNotionPage:
    """Small page summary returned after a successful Notion create call."""

    page_id: str
    url: str | None = None


class _Response(Protocol):
    def __enter__(self) -> _Response: ...

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None: ...

    def read(self) -> bytes: ...


class _Opener(Protocol):
    def __call__(self, request: Request, timeout: float) -> _Response: ...


class NotionClient:
    """Small Notion client for registering normalized books."""

    def __init__(
        self,
        token: str,
        *,
        data_source_id: str,
        timeout: float = 10.0,
        opener: _Opener = urlopen,
    ) -> None:
        token = token.strip()
        data_source_id = data_source_id.strip()
        if not token:
            raise ValueError("Notion token is required.")
        if not data_source_id:
            raise ValueError("Notion data source ID is required.")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Notion timeout must be a positive finite value.")

        self._token = token
        self._data_source_id = data_source_id
        self._timeout = timeout
        self._opener = opener

    @classmethod
    def from_env(
        cls,
        *,
        environ: Mapping[str, str] = os.environ,
        data_source_id: str | None = None,
        timeout: float = 10.0,
        opener: _Opener = urlopen,
    ) -> NotionClient:
        """Build a client from NOTION_API_KEY or NOTION_TOKEN."""

        token = environ.get("NOTION_API_KEY") or environ.get("NOTION_TOKEN")
        if token is None:
            raise ValueError("NOTION_API_KEY or NOTION_TOKEN is required.")
        resolved_data_source_id = data_source_id or environ.get("NOTION_BOOKSHELF_DATA_SOURCE_ID")
        if resolved_data_source_id is None:
            raise ValueError("NOTION_BOOKSHELF_DATA_SOURCE_ID is required.")
        return cls(token, data_source_id=resolved_data_source_id, timeout=timeout, opener=opener)

    def create_book_page(
        self,
        book: Book,
        *,
        genre: str | None = None,
        status: str = DEFAULT_READING_STATUS,
    ) -> CreatedNotionPage:
        """Create a new book page under the configured Notion data source."""

        request = Request(
            f"{NOTION_API_URL}/pages",
            data=json.dumps(
                {
                    "parent": {
                        "type": "data_source_id",
                        "data_source_id": self._data_source_id,
                    },
                    "properties": _book_page_properties(book, genre=genre, status=status),
                },
                ensure_ascii=False,
            ).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Notion-Version": NOTION_VERSION,
                "User-Agent": "notion-book-register/0.1",
            },
            method="POST",
        )

        try:
            with self._opener(request, timeout=self._timeout) as response:
                payload = response.read()
        except HTTPError as error:
            try:
                error_payload = _read_http_error_payload(error)
            finally:
                _close_http_error(error)
            raise NotionApiError(_notion_http_error_message(error.code, error_payload)) from error
        except URLError as error:
            raise NotionApiError(f"Failed to connect to Notion API: {error.reason}") from error
        except TimeoutError as error:
            raise NotionApiError("Timed out while reading from Notion API.") from error
        except OSError as error:
            raise NotionApiError(f"Failed to read from Notion API: {error}") from error
        except HTTPException as error:
            raise NotionApiError(f"Failed to read from Notion API: {error}") from error

        return _parse_created_page(payload)


def _book_page_properties(
    book: Book,
    *,
    genre: str | None,
    status: str,
) -> dict[str, Any]:
    mapped = book_to_notion_properties(book, genre=genre, status=status)
    properties: dict[str, Any] = {
        "作品名": _title_property(mapped["作品名"]),
        "状態": _select_property(mapped["状態"]),
        "memo": _rich_text_property(mapped["memo"]),
    }
    if "ジャンル" in mapped:
        properties["ジャンル"] = _select_property(mapped["ジャンル"])
    return properties


def _title_property(value: str) -> dict[str, Any]:
    return {"title": [_text(chunk) for chunk in _split_text(value)]}


def _rich_text_property(value: str) -> dict[str, Any]:
    return {"rich_text": [_text(chunk) for chunk in _split_text(value)]}


def _select_property(value: str) -> dict[str, Any]:
    return {"select": {"name": value}}


def _text(value: str) -> dict[str, Any]:
    return {"type": "text", "text": {"content": value}}


def _split_text(value: str) -> list[str]:
    if not value:
        return []
    return [
        value[index : index + NOTION_TEXT_CONTENT_LIMIT]
        for index in range(0, len(value), NOTION_TEXT_CONTENT_LIMIT)
    ]


def _parse_created_page(payload: bytes) -> CreatedNotionPage:
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NotionApiError("Notion API returned invalid JSON.") from error
    if not isinstance(data, dict):
        raise NotionApiError("Notion API response must be a JSON object.")

    page_id = data.get("id")
    if not isinstance(page_id, str) or not page_id:
        raise NotionApiError("Notion API response is missing page id.")

    url = data.get("url")
    if url is not None and not isinstance(url, str):
        raise NotionApiError("Notion API response has invalid page url.")

    return CreatedNotionPage(page_id=page_id, url=url)


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


def _notion_http_error_message(status_code: int, payload: bytes) -> str:
    detail = _notion_error_detail(payload)
    if detail is None:
        return f"Notion API returned HTTP {status_code}."
    return f"Notion API returned HTTP {status_code}: {detail}"


def _notion_error_detail(payload: bytes) -> str | None:
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    message = data.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    return None
