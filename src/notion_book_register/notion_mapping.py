"""Mapping from internal models to the current Notion bookshelf schema."""

from __future__ import annotations

from notion_book_register.models import Book

BOOKSHELF_DATA_SOURCE_ID = "2bddc1bd-5d17-8199-8910-000b299eb538"
DEFAULT_READING_STATUS = "未読"


def book_to_notion_properties(
    book: Book,
    *,
    genre: str | None = None,
    status: str = DEFAULT_READING_STATUS,
) -> dict[str, str]:
    """Build Notion page properties for the user's current bookshelf database."""

    properties = {
        "作品名": book.title,
        "状態": status,
        "memo": _build_memo(book),
    }
    if genre:
        properties["ジャンル"] = genre
    return properties


def _build_memo(book: Book) -> str:
    lines = [
        f"ISBN: {book.isbn13}",
    ]
    if book.authors:
        lines.append(f"著者: {', '.join(book.authors)}")
    if book.publisher:
        lines.append(f"出版社: {book.publisher}")
    if book.published_date:
        lines.append(f"出版日: {book.published_date}")
    if book.ndl_url:
        lines.append(f"NDL: {book.ndl_url}")
    return "\n".join(lines)
