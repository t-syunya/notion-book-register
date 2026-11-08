"""Application service for registering a book from an image."""

from __future__ import annotations

from dataclasses import dataclass
from secrets import token_urlsafe
from threading import Lock
from time import monotonic
from typing import Protocol

from notion_book_register.models import Book
from notion_book_register.ndl_client import NdlClient, NdlSruResponse, book_from_sru_response
from notion_book_register.notion_client import CreatedNotionPage, NotionClient
from notion_book_register.vlm_client import VlmProvider, vlm_from_env


class BookRegistrationError(RuntimeError):
    """Raised when an image cannot be turned into a registered book."""


class IsbnNotDetectedError(BookRegistrationError):
    """Raised when no validated ISBN-13 can be read from the image."""


class InvalidPreviewError(BookRegistrationError):
    """Preview is expired, consumed, or does not contain the chosen ISBN."""


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
    @property
    def data_source_id(self) -> str: ...

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
        self._previews: dict[str, tuple[float, tuple[Book, ...], str | None]] = {}

    @classmethod
    def from_env(cls) -> BookRegistrationService:
        """Build the production service from environment configuration."""

        return cls(
            vlm_from_env(),
            NdlClient(),
            NotionClient.from_env(),
        )

    def preview_image(
        self,
        image: bytes,
        *,
        mime_type: str,
        title: str | None = None,
        author: str | None = None,
        genre: str | None = None,
    ) -> dict:
        """Recognize candidates without writing; keep immutable books for five minutes."""
        if title is not None or author is not None:
            raise ValueError("Preview requires exact ISBN lookup, without fallback.")
        extraction = self._vlm.extract_isbn13(image, mime_type=mime_type)
        isbns = tuple(
            dict.fromkeys(
                ((extraction.isbn13,) if extraction.isbn13 else ()) + extraction.candidates
            )
        )
        if not isbns:
            raise IsbnNotDetectedError("No valid ISBN-13 was detected in the image.")
        if len(isbns) > 5:
            raise IsbnNotDetectedError("Too many ISBN candidates; use a clearer image.")
        books = tuple(
            book
            for isbn in isbns
            if (book := book_from_sru_response(self._ndl.search_by_isbn(isbn), isbn13=isbn))
            is not None
            and book.isbn13 == isbn
        )
        if not books:
            raise BookNotFoundError("No matching bibliographic record was found in NDL Search.")
        token = token_urlsafe(32)
        with self._notion_write_lock:
            now = monotonic()
            self._previews = {k: v for k, v in self._previews.items() if v[0] > now}
            if len(self._previews) >= 128:
                raise InvalidPreviewError("Too many pending previews.")
            self._previews[token] = (now + 300, books, _normalize_optional_text(genre))
        return {
            "preview_token": token,
            "expires_in": 300,
            "data_source_id": self._notion.data_source_id,
            "confidence": extraction.confidence,
            "candidates": [
                {"isbn13": b.isbn13, "title": b.title, "authors": list(b.authors)} for b in books
            ],
        }

    def commit_preview(self, token: str, isbn13: str) -> RegisteredBook:
        """Consume before writing, including on ambiguous upstream failures."""
        with self._notion_write_lock:
            preview = self._previews.get(token)
            if preview is None or preview[0] <= monotonic():
                self._previews.pop(token, None)
                raise InvalidPreviewError("Preview is expired or already consumed.")
            book = next((b for b in preview[1] if b.isbn13 == isbn13), None)
            if book is None:
                raise InvalidPreviewError("ISBN is not in this preview.")
            del self._previews[token]
            page = self._notion.create_book_page(book, genre=preview[2])
            return RegisteredBook(book=book, page=page)

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
