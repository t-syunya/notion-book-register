"""ISBN normalization and validation."""

from __future__ import annotations


class InvalidIsbnError(ValueError):
    """Raised when a value cannot be normalized to a valid ISBN-13."""


def normalize_isbn13(value: str) -> str:
    """Return a validated 13-digit ISBN string.

    Separators such as spaces and hyphens are ignored. The function intentionally
    rejects ISBN-10 values because the current product flow stores ISBN-13.
    """

    digits = _extract_digits(value)
    if len(digits) != 13:
        raise InvalidIsbnError("ISBN-13 must contain exactly 13 digits.")
    if not _has_isbn13_prefix(digits):
        raise InvalidIsbnError("ISBN-13 must start with 978 or 979.")
    if not validate_isbn13(digits):
        raise InvalidIsbnError("ISBN-13 check digit is invalid.")
    return digits


def validate_isbn13(value: str) -> bool:
    """Return True when value is a valid ISBN-13."""

    try:
        digits = _extract_digits(value)
    except InvalidIsbnError, TypeError:
        return False

    if len(digits) != 13:
        return False
    if not _has_isbn13_prefix(digits):
        return False

    total = 0
    for index, char in enumerate(digits[:12]):
        weight = 1 if index % 2 == 0 else 3
        total += int(char) * weight

    expected = (10 - (total % 10)) % 10
    return expected == int(digits[-1])


def _extract_digits(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("ISBN value must be a string.")

    digits = []
    for char in value:
        if "0" <= char <= "9":
            digits.append(char)
        elif char in {" ", "-"}:
            continue
        else:
            raise InvalidIsbnError("ISBN-13 may contain only ASCII digits, spaces, and hyphens.")
    return "".join(digits)


def _has_isbn13_prefix(value: str) -> bool:
    return value.startswith(("978", "979"))
