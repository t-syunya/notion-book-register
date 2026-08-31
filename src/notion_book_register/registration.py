"""Application service for registering a book from an image."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Protocol

from notion_book_register.models import Book
from notion_book_register.ndl_client import NdlClient, NdlSruResponse, book_from_sru_response
from notion_book_register.notion_client import CreatedNotionPage, NotionClient
from notion_book_register.vlm_client import OpenAiVlmClient, VlmProvider


class BookRegistrationError(RuntimeError):
    """Raised when an image cannot be turned into a registered book."""


class IsbnNotDetectedError(BookRegistrationError):
    """Raised when no validated ISBN-13 can be read from the image."""


class BookNotFoundError(BookRegistrationError):
    """Raised when NDL has no matching bibliographic record."""


@dataclass(frozen=True, slots=True)
class RegisteredBook:
    """Result of registering one image."""

    book: Book
    page: CreatedNotionPage


class _NdlBookProvider(Protocol):
    def search_by_isbn(self, isbn13: str, *, maximum_records: int = 1) -> NdlSruResponse: ...

    def search_by_isbn_with_fallback(
        self,
        isbn13: str,
        *,
        title: str,
        author: str,
        maximum_records: int = 1,
    ) -> NdlSruResponse: ...


class _NotionBookStore(Protocol):
    def create_book_page(
        self,
        book: Book,
        *,
        genre: str | None = None,
        status: str = "未読",
        prevent_duplicates: bool = True,
    ) -> CreatedNotionPage: ...


class BookRegistrationService:
    """Coordinate VLM extraction, NDL lookup, and Notion registration."""

    def __init__(
        self,
        vlm: VlmProvider,
        ndl: _NdlBookProvider,
        notion: _NotionBookStore,
    ) -> None:
        self._vlm = vlm
        self._ndl = ndl
        self._notion = notion
        self._notion_write_lock = Lock()

    @classmethod
    def from_env(cls) -> BookRegistrationService:
        """Build the production service from environment configuration."""

        return cls(
            OpenAiVlmClient.from_env(),
            NdlClient(),
            NotionClient.from_env(),
        )

    def register_image(
        self,
        image: bytes,
        *,
        mime_type: str,
        title: str | None = None,
        author: str | None = None,
        genre: str | None = None,
    ) -> RegisteredBook:
        """Extract, look up, and register the book shown in one image."""

        fallback_title = _normalize_optional_text(title)
        fallback_author = _normalize_optional_text(author)
        normalized_genre = _normalize_optional_text(genre)
        if (fallback_title is None) != (fallback_author is None):
            raise ValueError("title and author must be provided together.")

        extraction = self._vlm.extract_isbn13(image, mime_type=mime_type)
        if extraction.isbn13 is None:
            raise IsbnNotDetectedError("No valid ISBN-13 was detected in the image.")

        if fallback_title is not None and fallback_author is not None:
            response = self._ndl.search_by_isbn_with_fallback(
                extraction.isbn13,
                title=fallback_title,
                author=fallback_author,
            )
        else:
            response = self._ndl.search_by_isbn(extraction.isbn13)

        book = book_from_sru_response(response, isbn13=extraction.isbn13)
        if book is None:
            raise BookNotFoundError("No matching bibliographic record was found in NDL Search.")

        # Notion's duplicate check is a non-atomic query-then-create operation.
        # Serialize it within this service instance so concurrent HTTP requests do not race.
        with self._notion_write_lock:
            page = self._notion.create_book_page(book, genre=normalized_genre)
        return RegisteredBook(book=book, page=page)


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("optional text values must be strings.")
    normalized = " ".join(value.split())
    return normalized or None
