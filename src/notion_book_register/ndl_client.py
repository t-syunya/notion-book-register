"""Client for the National Diet Library Search SRU API."""

from __future__ import annotations

from dataclasses import dataclass
from http.client import HTTPException
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from notion_book_register.isbn import normalize_isbn13

NDL_SRU_API_URL = "https://ndlsearch.ndl.go.jp/api/sru"

_SRU_NAMESPACE = "http://www.loc.gov/zing/srw/"
_SRU_DIAGNOSTIC_NAMESPACE = "http://www.loc.gov/zing/srw/diagnostic/"
_NAMESPACES = {"sru": _SRU_NAMESPACE, "diag": _SRU_DIAGNOSTIC_NAMESPACE}


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
        base_url: str = NDL_SRU_API_URL,
        timeout: float = 10.0,
        opener: _Opener = urlopen,
    ) -> None:
        self._base_url = base_url
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
        return f"{self._base_url}?{query}"


def parse_sru_response(payload: bytes) -> NdlSruResponse:
    """Parse the SRU response fields needed by the next normalization step."""

    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as error:
        raise NdlApiError("NDL API returned invalid XML.") from error

    _raise_for_diagnostics(root)

    number_of_records_text = root.findtext("sru:numberOfRecords", namespaces=_NAMESPACES)
    if number_of_records_text is None:
        raise NdlApiError("NDL API response is missing numberOfRecords.")

    try:
        number_of_records = int(number_of_records_text)
    except ValueError as error:
        raise NdlApiError("NDL API response has invalid numberOfRecords.") from error

    records_xml = []
    for record_data in root.findall(".//sru:recordData", namespaces=_NAMESPACES):
        children = list(record_data)
        if not children:
            continue
        records_xml.append(ElementTree.tostring(children[0], encoding="unicode"))

    return NdlSruResponse(number_of_records=number_of_records, records_xml=tuple(records_xml))


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
