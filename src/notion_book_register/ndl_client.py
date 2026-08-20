"""Client for the National Diet Library Search SRU API."""

from __future__ import annotations

import re
from dataclasses import dataclass
from http.client import HTTPException
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from defusedxml import ElementTree as DefusedElementTree
from defusedxml.common import DefusedXmlException

from notion_book_register.isbn import normalize_isbn13
from notion_book_register.models import Book

NDL_SRU_API_URL = "https://ndlsearch.ndl.go.jp/api/sru"

_SRU_NAMESPACE = "http://www.loc.gov/zing/srw/"
_SRU_DIAGNOSTIC_NAMESPACE = "http://www.loc.gov/zing/srw/diagnostic/"
_NAMESPACES = {"sru": _SRU_NAMESPACE, "diag": _SRU_DIAGNOSTIC_NAMESPACE}
_ISBN13_PATTERN = re.compile(r"97[89](?:[\s-]?\d){10}")


class NdlApiError(RuntimeError):
    """Raised when the NDL API cannot be queried or parsed."""


@dataclass(frozen=True, slots=True)
class NdlSruResponse:
    """Parsed SRU search response."""

    number_of_records: int
    records_xml: tuple[str, ...]

    @property
    def found(self) -> bool:
        return self.number_of_records > 0 and bool(self.records_xml)


class _Response(Protocol):
    def __enter__(self) -> _Response: ...

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None: ...

    def read(self) -> bytes: ...


class _Opener(Protocol):
    def __call__(self, request: Request, timeout: float) -> _Response: ...


class NdlClient:
    """Small SRU client for ISBN based book lookup."""

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        opener: _Opener = urlopen,
    ) -> None:
        self._timeout = timeout
        self._opener = opener

    def search_by_isbn(self, isbn13: str, *, maximum_records: int = 1) -> NdlSruResponse:
        """Search NDL bibliographic records by ISBN-13."""

        normalized_isbn = normalize_isbn13(isbn13)
        if maximum_records < 1:
            raise ValueError("maximum_records must be greater than or equal to 1.")

        request = Request(
            self._build_search_url(normalized_isbn, maximum_records),
            headers={"User-Agent": "notion-book-register/0.1"},
        )
        try:
            with self._opener(request, timeout=self._timeout) as response:
                payload = response.read()
        except HTTPError as error:
            error.close()
            raise NdlApiError(f"NDL API returned HTTP {error.code}.") from error
        except URLError as error:
            raise NdlApiError(f"Failed to connect to NDL API: {error.reason}") from error
        except TimeoutError as error:
            raise NdlApiError("Timed out while reading from NDL API.") from error
        except OSError as error:
            raise NdlApiError(f"Failed to read from NDL API: {error}") from error
        except HTTPException as error:
            raise NdlApiError(f"Failed to read from NDL API: {error}") from error

        return parse_sru_response(payload)

    def _build_search_url(self, isbn13: str, maximum_records: int) -> str:
        query = urlencode(
            {
                "operation": "searchRetrieve",
                "version": "1.2",
                "recordSchema": "dcndl",
                "recordPacking": "xml",
                "onlyBib": "true",
                "maximumRecords": str(maximum_records),
                "query": f'isbn="{isbn13}"',
            }
        )
        return f"{NDL_SRU_API_URL}?{query}"


def parse_sru_response(payload: bytes) -> NdlSruResponse:
    """Parse the SRU response fields needed by the next normalization step."""

    root = _parse_xml(payload, "NDL API returned invalid XML.")

    _raise_for_diagnostics(root)

    number_of_records_text = root.findtext("sru:numberOfRecords", namespaces=_NAMESPACES)
    if number_of_records_text is None:
        raise NdlApiError("NDL API response is missing numberOfRecords.")

    try:
        number_of_records = int(number_of_records_text)
    except ValueError as error:
        raise NdlApiError("NDL API response has invalid numberOfRecords.") from error
    if number_of_records < 0:
        raise NdlApiError("NDL API response has invalid numberOfRecords.")

    records_xml = []
    for record_data in root.findall(".//sru:recordData", namespaces=_NAMESPACES):
        children = list(record_data)
        if not children:
            continue
        records_xml.append(ElementTree.tostring(children[0], encoding="unicode"))

    return NdlSruResponse(number_of_records=number_of_records, records_xml=tuple(records_xml))


def book_from_sru_response(response: NdlSruResponse, *, isbn13: str | None = None) -> Book | None:
    """Return the first NDL record normalized to a Book, or None when no record exists."""

    if not response.found:
        return None
    return book_from_ndl_record(response.records_xml[0], isbn13=isbn13)


def book_from_ndl_record(record_xml: str, *, isbn13: str | None = None) -> Book:
    """Normalize a DC-NDL bibliographic XML record to the internal Book model."""

    root = _parse_xml(record_xml, "NDL bibliographic record is invalid XML.")

    title = _first_text(root, ("title", "titleTranscription"))
    if title is None:
        raise NdlApiError("NDL bibliographic record is missing title.")

    record_isbn = _extract_record_isbn(root)
    fallback_isbn = normalize_isbn13(isbn13) if isbn13 is not None else None
    normalized_isbn = record_isbn or fallback_isbn
    if normalized_isbn is None:
        raise NdlApiError("NDL bibliographic record is missing ISBN-13.")

    return Book(
        isbn13=normalized_isbn,
        title=title,
        authors=tuple(_agent_texts(root, ("creator",))),
        publisher=_first_agent_text(root, ("publisher",)),
        published_date=_first_text(root, ("issued", "date")),
        ndl_url=_find_resource_url(root),
    )


def _parse_xml(payload: bytes | str, error_message: str) -> ElementTree.Element:
    try:
        return DefusedElementTree.fromstring(payload, forbid_dtd=True)
    except (ElementTree.ParseError, DefusedXmlException, LookupError) as error:
        raise NdlApiError(error_message) from error


def _raise_for_diagnostics(root: ElementTree.Element) -> None:
    diagnostics = [
        *root.findall(".//sru:diagnostic", namespaces=_NAMESPACES),
        *root.findall(".//diag:diagnostic", namespaces=_NAMESPACES),
    ]
    if not diagnostics:
        return

    messages = []
    for diagnostic in diagnostics:
        details = [
            text.strip()
            for text in (
                _find_child_text(diagnostic, "message"),
                _find_child_text(diagnostic, "details"),
            )
            if text and text.strip()
        ]
        if details:
            messages.append(": ".join(details))

    message = "; ".join(messages) if messages else "unknown diagnostic"
    raise NdlApiError(f"NDL API returned SRU diagnostics: {message}")


def _find_child_text(element: ElementTree.Element, local_name: str) -> str | None:
    for child in element:
        if child.tag.rsplit("}", maxsplit=1)[-1] == local_name:
            return child.text
    return None


def _extract_record_isbn(root: ElementTree.Element) -> str | None:
    resource_isbn = _extract_resource_isbn(root)
    if resource_isbn is not None:
        return resource_isbn

    for element in root.iter():
        if _local_name(element.tag) != "identifier":
            continue

        text = _compact_text(element)
        if not text:
            continue

        identifier_type = _identifier_type(element)
        if identifier_type is not None and identifier_type != "ISBN":
            continue

        candidates = [text]
        isbn_match = _ISBN13_PATTERN.search(text)
        if isbn_match:
            candidates.insert(0, isbn_match.group(0))

        text_lower = text.casefold()
        looks_like_isbn = (
            identifier_type == "ISBN"
            or text.startswith(("978", "979"))
            or (
                "isbn" in text_lower
                and "setisbn" not in text_lower
                and "errorisbn" not in text_lower
            )
        )
        if not looks_like_isbn:
            continue

        for candidate in candidates:
            normalized_isbn = _normalize_isbn_candidate(candidate)
            if normalized_isbn is not None:
                return normalized_isbn
    return None


def _identifier_type(element: ElementTree.Element) -> str | None:
    for name, value in element.attrib.items():
        if _local_name(name) not in {"datatype", "type"}:
            continue
        return re.split(r"[/#:]", value.strip())[-1]
    return None


def _extract_resource_isbn(root: ElementTree.Element) -> str | None:
    for element in root.iter():
        if _local_name(element.tag) != "seeAlso":
            continue
        for name, value in element.attrib.items():
            if _local_name(name) != "resource":
                continue
            normalized_isbn = _normalize_isbn_resource(value)
            if normalized_isbn is not None:
                return normalized_isbn
    return None


def _normalize_isbn_resource(value: str) -> str | None:
    path_segments = [segment for segment in urlparse(value).path.split("/") if segment]
    for index, segment in enumerate(path_segments[:-1]):
        if segment != "isbn":
            continue
        return _normalize_isbn_candidate(path_segments[index + 1])
    return None


def _normalize_isbn_candidate(value: str) -> str | None:
    for candidate in (match.group(0) for match in _ISBN13_PATTERN.finditer(value)):
        try:
            return normalize_isbn13(candidate)
        except ValueError:
            continue

    try:
        return normalize_isbn13(value)
    except ValueError:
        return None


def _first_text(root: ElementTree.Element, local_names: tuple[str, ...]) -> str | None:
    for local_name in local_names:
        for text in _texts(root, (local_name,)):
            return text
    return None


def _first_agent_text(root: ElementTree.Element, local_names: tuple[str, ...]) -> str | None:
    for text in _agent_texts(root, local_names):
        return text
    return None


def _texts(root: ElementTree.Element, local_names: tuple[str, ...]) -> list[str]:
    values = []
    seen = set()
    for element in root.iter():
        if _local_name(element.tag) not in local_names:
            continue
        text = _compact_text(element)
        if not text or text in seen:
            continue
        values.append(text)
        seen.add(text)
    return values


def _agent_texts(root: ElementTree.Element, local_names: tuple[str, ...]) -> list[str]:
    values = []
    seen = set()
    for element in root.iter():
        if _local_name(element.tag) not in local_names:
            continue
        text = _agent_text(element)
        if not text or text in seen:
            continue
        values.append(text)
        seen.add(text)
    return values


def _agent_text(element: ElementTree.Element) -> str:
    for preferred_name in ("name", "value"):
        for child in element.iter():
            if child is element or _local_name(child.tag) != preferred_name:
                continue
            text = _compact_text(child)
            if text:
                return text
    return _compact_text(element)


def _compact_text(element: ElementTree.Element) -> str:
    return " ".join(text.strip() for text in element.itertext() if text.strip())


def _find_resource_url(root: ElementTree.Element) -> str | None:
    for element in root.iter():
        for name, value in element.attrib.items():
            if _local_name(name) == "about" and value.strip():
                return value.strip()
    return None


def _local_name(name: str) -> str:
    return name.rsplit("}", maxsplit=1)[-1]
