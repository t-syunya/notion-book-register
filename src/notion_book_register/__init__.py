"""Core utilities for notion-book-register."""

from notion_book_register.isbn import InvalidIsbnError, normalize_isbn13, validate_isbn13
from notion_book_register.models import Book
from notion_book_register.ndl_client import (
    NdlApiError,
    NdlClient,
    NdlSruResponse,
    book_from_ndl_record,
    book_from_sru_response,
    parse_sru_response,
)
from notion_book_register.notion_client import CreatedNotionPage, NotionApiError, NotionClient
from notion_book_register.notion_mapping import book_to_notion_properties
from notion_book_register.registration import (
    BookNotFoundError,
    BookRegistrationError,
    BookRegistrationService,
    IsbnNotDetectedError,
    RegisteredBook,
)
from notion_book_register.vlm_client import (
    OpenAiVlmClient,
    OpenAiVlmConfig,
    VlmApiError,
    VlmProvider,
)
from notion_book_register.vlm_prompt import (
    IsbnExtractionResult,
    VlmPromptError,
    build_isbn_extraction_messages,
    parse_isbn_extraction_response,
)

__all__ = [
    "Book",
    "BookNotFoundError",
    "BookRegistrationError",
    "BookRegistrationService",
    "CreatedNotionPage",
    "InvalidIsbnError",
    "IsbnExtractionResult",
    "IsbnNotDetectedError",
    "NdlApiError",
    "NdlClient",
    "NdlSruResponse",
    "NotionApiError",
    "NotionClient",
    "OpenAiVlmClient",
    "OpenAiVlmConfig",
    "RegisteredBook",
    "VlmApiError",
    "VlmProvider",
    "VlmPromptError",
    "book_from_ndl_record",
    "book_from_sru_response",
    "book_to_notion_properties",
    "build_isbn_extraction_messages",
    "normalize_isbn13",
    "parse_isbn_extraction_response",
    "parse_sru_response",
    "validate_isbn13",
]
