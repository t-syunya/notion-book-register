"""Internal domain models."""

from __future__ import annotations

from dataclasses import dataclass, field

from notion_book_register.isbn import normalize_isbn13


@dataclass(frozen=True, slots=True)
class Book:
    """Normalized book information used across NDL and Notion boundaries."""

    isbn13: str
    title: str
    authors: tuple[str, ...] = field(default_factory=tuple)
    publisher: str | None = None
    published_date: str | None = None
    ndl_url: str | None = None

    def __post_init__(self) -> None:
        normalized_isbn = normalize_isbn13(self.isbn13)
        normalized_authors = tuple(author.strip() for author in self.authors if author.strip())
        object.__setattr__(self, "isbn13", normalized_isbn)
        object.__setattr__(self, "title", self.title.strip())
        object.__setattr__(self, "authors", normalized_authors)
        if not self.title:
            raise ValueError("Book title is required.")
