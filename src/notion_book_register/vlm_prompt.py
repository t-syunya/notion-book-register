"""Prompt helpers for extracting ISBN-13 values from book images."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from notion_book_register.isbn import InvalidIsbnError, normalize_isbn13

_ISBN13_PATTERN = re.compile(r"97[89](?:[\s-]?\d){10}")


class VlmPromptError(ValueError):
    """Raised when a VLM response does not match the ISBN extraction contract."""


@dataclass(frozen=True, slots=True)
class IsbnExtractionResult:
    """Validated ISBN extraction result returned by a VLM."""

    isbn13: str | None
    candidates: tuple[str, ...]
    confidence: str
    evidence: str


ISBN_EXTRACTION_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["isbn13", "candidates", "confidence", "evidence"],
    "properties": {
        "isbn13": {
            "type": ["string", "null"],
            "description": (
                "Most likely normalized ISBN-13, or null when no valid ISBN-13 is visible."
            ),
        },
        "candidates": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "All visible ISBN-13 candidates before validation, ordered by likelihood."
            ),
        },
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
        },
        "evidence": {
            "type": "string",
            "description": "Short note describing where the ISBN was read from.",
        },
    },
}

_SYSTEM_PROMPT = """\
You extract ISBN-13 values from book photos.
Return only a JSON object that matches the provided schema. Do not wrap the JSON
in Markdown or add explanatory text.

Rules:
- Read visible text, barcode labels, obi strips, back covers, and copyright pages.
- Prefer labels such as ISBN, ISBN-13, or JAN code when they appear near a 978 or 979 code.
- Ignore prices, publication dates, page counts, and non-book EAN-13 values.
- Normalize the selected ISBN-13 to 13 ASCII digits without hyphens or spaces.
- Use null for isbn13 when no valid ISBN-13 can be read confidently.
- Keep candidates in the order you would try them after OCR cleanup.
"""

_USER_PROMPT_TEMPLATE = """\
画像から書籍の ISBN-13 を抽出してください。
裏表紙、バーコード周辺、帯、奥付、カバーに印字された ISBN 表記を優先して確認してください。

返答は次の JSON Schema に一致する JSON オブジェクトだけにしてください。

{schema}

`isbn13` には最も確からしい有効な ISBN-13 を 13 桁の ASCII 数字で入れてください。
有効な ISBN-13 が見つからない場合は `isbn13` を null にし、`candidates` は空配列または
読み取れた候補だけにしてください。
"""


def build_isbn_extraction_messages() -> tuple[dict[str, str], ...]:
    """Build provider-neutral chat messages for ISBN-13 extraction from one image."""

    schema = json.dumps(ISBN_EXTRACTION_RESPONSE_SCHEMA, ensure_ascii=False, sort_keys=True)
    return (
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _USER_PROMPT_TEMPLATE.format(schema=schema)},
    )


def parse_isbn_extraction_response(payload: str) -> IsbnExtractionResult:
    """Parse and validate a JSON response produced by the ISBN extraction prompt."""

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise VlmPromptError("VLM response must be a JSON object.") from exc

    if not isinstance(data, dict):
        raise VlmPromptError("VLM response must be a JSON object.")

    _validate_response_keys(data)

    isbn13 = _normalize_optional_isbn13(data.get("isbn13"))
    candidates = _normalize_candidates(data.get("candidates"))
    confidence = _require_confidence(data.get("confidence"))
    evidence = _require_string(data.get("evidence"), "evidence")

    return IsbnExtractionResult(
        isbn13=isbn13,
        candidates=candidates,
        confidence=confidence,
        evidence=evidence.strip(),
    )


def _validate_response_keys(data: dict[str, object]) -> None:
    required = set(ISBN_EXTRACTION_RESPONSE_SCHEMA["required"])
    keys = set(data)
    missing = sorted(required - keys)
    if missing:
        raise VlmPromptError(f"missing required field: {missing[0]}.")

    unexpected = sorted(keys - required)
    if unexpected:
        raise VlmPromptError(f"unexpected field in VLM response: {unexpected[0]}.")


def _normalize_optional_isbn13(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise VlmPromptError("isbn13 must be a string or null.")
    normalized = _normalize_isbn_candidate(value)
    if normalized is None:
        raise VlmPromptError("invalid isbn13 in VLM response.")
    return normalized


def _normalize_candidates(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise VlmPromptError("candidates must be an array.")

    candidates = []
    seen = set()
    for candidate in value:
        if not isinstance(candidate, str):
            raise VlmPromptError("candidates must contain only strings.")
        normalized = _normalize_isbn_candidate(candidate)
        if normalized is None:
            continue
        if normalized not in seen:
            candidates.append(normalized)
            seen.add(normalized)
    return tuple(candidates)


def _normalize_isbn_candidate(value: str) -> str | None:
    normalized_whitespace = re.sub(r"\s+", " ", value)
    for candidate in (match.group(0) for match in _ISBN13_PATTERN.finditer(normalized_whitespace)):
        try:
            return normalize_isbn13(candidate)
        except InvalidIsbnError:
            continue

    try:
        return normalize_isbn13(normalized_whitespace)
    except InvalidIsbnError:
        return None


def _require_confidence(value: object) -> str:
    confidence = _require_string(value, "confidence").strip()
    if confidence not in {"high", "medium", "low"}:
        raise VlmPromptError("confidence must be high, medium, or low.")
    return confidence


def _require_string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise VlmPromptError(f"{field_name} must be a string.")
    return value
