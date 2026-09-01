import unittest
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

from notion_book_register import (
    NdlApiError,
    NdlClient,
    book_from_ndl_record,
    book_from_sru_response,
    parse_sru_response,
)

SRU_RESPONSE = b"""<?xml version="1.0" encoding="UTF-8"?>
<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">
  <numberOfRecords>1</numberOfRecords>
  <records>
    <record>
      <recordData>
        <dcndl:BibResource xmlns:dcndl="http://ndl.go.jp/dcndl/terms/">
          <dcndl:titleTranscription>Python Testing</dcndl:titleTranscription>
        </dcndl:BibResource>
      </recordData>
    </record>
  </records>
</searchRetrieveResponse>
"""


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


class ErrorResponse:
    def __init__(self, error: OSError) -> None:
        self._error = error

    def __enter__(self) -> ErrorResponse:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def read(self) -> bytes:
        raise self._error


class CloseTrackingBody:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class NdlClientTest(unittest.TestCase):
    def test_search_by_isbn_builds_sru_request_and_parses_response(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return FakeResponse(SRU_RESPONSE)

        client = NdlClient(timeout=3.0, opener=opener)

        response = client.search_by_isbn("978-4-297-13578-2")

        self.assertTrue(response.found)
        self.assertEqual(response.number_of_records, 1)
        self.assertEqual(len(response.records_xml), 1)
        self.assertIn("Python Testing", response.records_xml[0])

        request, timeout = requests[0]
        self.assertEqual(timeout, 3.0)
        self.assertEqual(request.headers["User-agent"], "notion-book-register/0.1")

        parsed_url = urlparse(request.full_url)
        self.assertEqual(parsed_url.scheme, "https")
        self.assertEqual(parsed_url.netloc, "ndlsearch.ndl.go.jp")
        self.assertEqual(parsed_url.path, "/api/sru")

        params = parse_qs(parsed_url.query)
        self.assertEqual(params["operation"], ["searchRetrieve"])
        self.assertEqual(params["version"], ["1.2"])
        self.assertEqual(params["recordSchema"], ["dcndl"])
        self.assertEqual(params["recordPacking"], ["xml"])
        self.assertEqual(params["onlyBib"], ["true"])
        self.assertEqual(params["maximumRecords"], ["1"])
        self.assertEqual(params["query"], ['isbn="9784297135782"'])

    def test_search_by_isbn_rejects_invalid_maximum_records(self) -> None:
        client = NdlClient(opener=lambda request, timeout: FakeResponse(SRU_RESPONSE))

        with self.assertRaises(ValueError):
            client.search_by_isbn("9784297135782", maximum_records=0)

    def test_search_by_title_and_author_builds_combined_query(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse(SRU_RESPONSE)

        client = NdlClient(opener=opener)

        response = client.search_by_title_and_author(
            '  What  If?  "Testing"  ',
            "Author * \\ A",
            maximum_records=3,
        )

        self.assertTrue(response.found)
        params = parse_qs(urlparse(requests[0].full_url).query)
        self.assertEqual(params["maximumRecords"], ["3"])
        self.assertEqual(
            params["query"],
            ['title = "What If\\? \\"Testing\\"" AND creator = "Author \\* \\\\ A"'],
        )

    def test_search_by_title_and_author_rejects_empty_terms(self) -> None:
        client = NdlClient(opener=lambda request, timeout: FakeResponse(SRU_RESPONSE))

        for title, author, message in (("", "Author", "title"), ("Title", "  ", "author")):
            with self.subTest(title=title, author=author):
                with self.assertRaisesRegex(ValueError, message):
                    client.search_by_title_and_author(title, author)

    def test_search_by_isbn_with_fallback_skips_fallback_when_isbn_matches(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse(SRU_RESPONSE)

        client = NdlClient(opener=opener)

        response = client.search_by_isbn_with_fallback(
            "9784297135782",
            title="Python Testing",
            author="Author A",
        )

        self.assertTrue(response.found)
        self.assertEqual(len(requests), 1)

    def test_search_by_isbn_with_fallback_searches_title_and_author_after_no_match(self) -> None:
        requests = []
        empty_response = b"""<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">
  <numberOfRecords>0</numberOfRecords>
</searchRetrieveResponse>"""

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse(empty_response if len(requests) == 1 else SRU_RESPONSE)

        client = NdlClient(opener=opener)

        response = client.search_by_isbn_with_fallback(
            "9784297135782",
            title="Python Testing",
            author="Author A",
        )

        self.assertTrue(response.found)
        self.assertEqual(len(requests), 2)
        fallback_params = parse_qs(urlparse(requests[1].full_url).query)
        self.assertEqual(
            fallback_params["query"],
            ['title = "Python Testing" AND creator = "Author A"'],
        )

    def test_search_by_isbn_with_fallback_handles_ndl_no_record_diagnostic(self) -> None:
        requests = []
        no_record_response = b"""<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">
  <diagnostics>
    <diagnostic xmlns="http://www.loc.gov/zing/srw/diagnostic/">
      <uri>info:srw/diagnostic/1/1</uri>
      <message>Record does not exist</message>
    </diagnostic>
  </diagnostics>
</searchRetrieveResponse>"""

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse(no_record_response if len(requests) == 1 else SRU_RESPONSE)

        client = NdlClient(opener=opener)

        response = client.search_by_isbn_with_fallback(
            "9784297135782",
            title="1984",
            author="George Orwell",
        )

        self.assertTrue(response.found)
        self.assertEqual(len(requests), 2)
        fallback_params = parse_qs(urlparse(requests[1].full_url).query)
        self.assertEqual(
            fallback_params["query"],
            ['title = "1984" AND creator = "George Orwell"'],
        )

    def test_search_by_isbn_with_fallback_returns_empty_when_fallback_has_no_record(self) -> None:
        no_record_response = b"""<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">
  <diagnostics>
    <diagnostic xmlns="http://www.loc.gov/zing/srw/diagnostic/">
      <uri>info:srw/diagnostic/1/65</uri>
      <message>Record does not exist</message>
    </diagnostic>
  </diagnostics>
</searchRetrieveResponse>"""
        client = NdlClient(
            opener=lambda request, timeout: FakeResponse(no_record_response),
        )

        response = client.search_by_isbn_with_fallback(
            "9784297135782",
            title="Missing Book",
            author="Missing Author",
        )

        self.assertFalse(response.found)
        self.assertEqual(response.number_of_records, 0)

    def test_search_by_isbn_with_fallback_does_not_hide_invalid_isbn_response(self) -> None:
        requests = []
        inconsistent_response = b"""<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">
  <numberOfRecords>1</numberOfRecords>
  <records />
</searchRetrieveResponse>"""

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse(inconsistent_response)

        client = NdlClient(opener=opener)

        with self.assertRaisesRegex(NdlApiError, "missing record data"):
            client.search_by_isbn_with_fallback(
                "9784297135782",
                title="Python Testing",
                author="Author A",
            )

        self.assertEqual(len(requests), 1)

    def test_parse_sru_response_rejects_conflicting_no_record_diagnostic(self) -> None:
        conflicting_payloads = (
            b"""<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">
  <numberOfRecords>1</numberOfRecords>
  <diagnostics>
    <diagnostic xmlns="http://www.loc.gov/zing/srw/diagnostic/">
      <uri>info:srw/diagnostic/1/1</uri>
      <message>Record does not exist</message>
    </diagnostic>
  </diagnostics>
</searchRetrieveResponse>""",
            b"""<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">
  <diagnostics>
    <diagnostic xmlns="http://www.loc.gov/zing/srw/diagnostic/">
      <uri>info:srw/diagnostic/1/65</uri>
      <message>illegal query syntax</message>
    </diagnostic>
  </diagnostics>
</searchRetrieveResponse>""",
        )

        for payload in conflicting_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(NdlApiError):
                    parse_sru_response(payload)

    def test_parse_sru_response_rejects_record_data_when_count_is_zero(self) -> None:
        with self.assertRaisesRegex(NdlApiError, "reports no records"):
            parse_sru_response(
                b"""<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">
  <numberOfRecords>0</numberOfRecords>
  <records>
    <record><recordData><book /></recordData></record>
  </records>
</searchRetrieveResponse>"""
            )

    def test_search_by_isbn_wraps_http_error(self) -> None:
        def opener(request, timeout):
            raise HTTPError(request.full_url, 500, "Server Error", {}, None)

        client = NdlClient(opener=opener)

        with self.assertRaisesRegex(NdlApiError, "HTTP 500"):
            client.search_by_isbn("9784297135782")

    def test_search_by_isbn_closes_http_error_response(self) -> None:
        response_body = CloseTrackingBody()

        def opener(request, timeout):
            raise HTTPError(request.full_url, 500, "Server Error", {}, response_body)

        client = NdlClient(opener=opener)

        with self.assertRaisesRegex(NdlApiError, "HTTP 500"):
            client.search_by_isbn("9784297135782")

        self.assertTrue(response_body.closed)

    def test_search_by_isbn_wraps_url_error(self) -> None:
        def opener(request, timeout):
            raise URLError("timeout")

        client = NdlClient(opener=opener)

        with self.assertRaisesRegex(NdlApiError, "timeout"):
            client.search_by_isbn("9784297135782")

    def test_search_by_isbn_wraps_read_timeout(self) -> None:
        client = NdlClient(opener=lambda request, timeout: ErrorResponse(TimeoutError()))

        with self.assertRaisesRegex(NdlApiError, "Timed out"):
            client.search_by_isbn("9784297135782")

    def test_search_by_isbn_wraps_read_error(self) -> None:
        client = NdlClient(opener=lambda request, timeout: ErrorResponse(OSError("closed")))

        with self.assertRaisesRegex(NdlApiError, "closed"):
            client.search_by_isbn("9784297135782")

    def test_search_by_isbn_wraps_incomplete_read(self) -> None:
        client = NdlClient(opener=lambda request, timeout: ErrorResponse(IncompleteRead(b"")))

        with self.assertRaisesRegex(NdlApiError, "IncompleteRead"):
            client.search_by_isbn("9784297135782")

    def test_parse_sru_response_handles_empty_records(self) -> None:
        response = parse_sru_response(
            b"""<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">
  <numberOfRecords>0</numberOfRecords>
</searchRetrieveResponse>"""
        )

        self.assertFalse(response.found)
        self.assertEqual(response.number_of_records, 0)
        self.assertEqual(response.records_xml, ())

    def test_parse_sru_response_rejects_invalid_xml(self) -> None:
        with self.assertRaisesRegex(NdlApiError, "invalid XML"):
            parse_sru_response(b"<searchRetrieveResponse>")

    def test_parse_sru_response_rejects_unknown_xml_encoding(self) -> None:
        with self.assertRaisesRegex(NdlApiError, "invalid XML"):
            parse_sru_response(
                b"""<?xml version="1.0" encoding="madeup"?>
<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/" />"""
            )

    def test_parse_sru_response_rejects_dtd(self) -> None:
        with self.assertRaisesRegex(NdlApiError, "invalid XML"):
            parse_sru_response(
                b"""<!DOCTYPE searchRetrieveResponse [
  <!ELEMENT searchRetrieveResponse ANY>
]>
<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">
  <numberOfRecords>0</numberOfRecords>
</searchRetrieveResponse>"""
            )

    def test_parse_sru_response_rejects_entity_expansion(self) -> None:
        with self.assertRaisesRegex(NdlApiError, "invalid XML"):
            parse_sru_response(
                b"""<!DOCTYPE searchRetrieveResponse [
  <!ENTITY expanded "expanded">
]>
<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">
  <numberOfRecords>&expanded;</numberOfRecords>
</searchRetrieveResponse>"""
            )

    def test_parse_sru_response_requires_number_of_records(self) -> None:
        with self.assertRaisesRegex(NdlApiError, "missing numberOfRecords"):
            parse_sru_response(b"<searchRetrieveResponse />")

    def test_parse_sru_response_rejects_negative_number_of_records(self) -> None:
        with self.assertRaisesRegex(NdlApiError, "invalid numberOfRecords"):
            parse_sru_response(
                b"""<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">
  <numberOfRecords>-1</numberOfRecords>
</searchRetrieveResponse>"""
            )

    def test_parse_sru_response_rejects_diagnostics(self) -> None:
        with self.assertRaisesRegex(NdlApiError, "Unsupported schema"):
            parse_sru_response(
                b"""<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">
  <version>1.2</version>
  <numberOfRecords>0</numberOfRecords>
  <diagnostics>
    <diagnostic>
      <uri>info:srw/diagnostic/1/66</uri>
      <message>Unsupported schema</message>
      <details>dcndl</details>
    </diagnostic>
  </diagnostics>
</searchRetrieveResponse>"""
            )

    def test_parse_sru_response_rejects_diagnostic_namespace(self) -> None:
        with self.assertRaisesRegex(NdlApiError, "Unsupported schema"):
            parse_sru_response(
                b"""<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">
  <version>1.2</version>
  <numberOfRecords>0</numberOfRecords>
  <diagnostics>
    <diagnostic xmlns="http://www.loc.gov/zing/srw/diagnostic/">
      <uri>info:srw/diagnostic/1/66</uri>
      <message>Unsupported schema</message>
      <details>dcndl</details>
    </diagnostic>
  </diagnostics>
</searchRetrieveResponse>"""
            )

    def test_book_from_ndl_record_maps_dcndl_bibliographic_fields(self) -> None:
        book = book_from_ndl_record(
            """<dcndl:BibResource
  xmlns:dcndl="http://ndl.go.jp/dcndl/terms/"
  xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:dcterms="http://purl.org/dc/terms/"
  xmlns:foaf="http://xmlns.com/foaf/0.1/"
  xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
  rdf:about="https://ndlsearch.ndl.go.jp/books/R100000002-I000000000000">
  <dcterms:title>Python Testing</dcterms:title>
  <dcterms:creator>
    <foaf:Agent>
      <foaf:name>Author A</foaf:name>
      <dcndl:transcription>オーサー A</dcndl:transcription>
      <dcndl:role>著者</dcndl:role>
    </foaf:Agent>
  </dcterms:creator>
  <dcterms:creator>
    <rdf:Description>
      <rdf:value>Author B</rdf:value>
    </rdf:Description>
  </dcterms:creator>
  <dcterms:publisher>
    <foaf:Agent>
      <foaf:name>Publisher</foaf:name>
      <dcndl:transcription>パブリッシャー</dcndl:transcription>
      <dcndl:location>Tokyo</dcndl:location>
    </foaf:Agent>
  </dcterms:publisher>
  <dcterms:issued>2026</dcterms:issued>
  <dc:identifier xsi:type="dcndl:ISBN"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">978-4-297-13578-2</dc:identifier>
</dcndl:BibResource>"""
        )

        self.assertEqual(book.isbn13, "9784297135782")
        self.assertEqual(book.title, "Python Testing")
        self.assertEqual(book.authors, ("Author A", "Author B"))
        self.assertEqual(book.publisher, "Publisher")
        self.assertEqual(book.published_date, "2026")
        self.assertEqual(
            book.ndl_url,
            "https://ndlsearch.ndl.go.jp/books/R100000002-I000000000000",
        )

    def test_book_from_ndl_record_uses_title_transcription_and_fallback_isbn(self) -> None:
        book = book_from_ndl_record(
            """<dcndl:BibResource xmlns:dcndl="http://ndl.go.jp/dcndl/terms/">
  <dcndl:titleTranscription>Python Testing</dcndl:titleTranscription>
</dcndl:BibResource>""",
            isbn13="978-4-297-13578-2",
        )

        self.assertEqual(book.isbn13, "9784297135782")
        self.assertEqual(book.title, "Python Testing")

    def test_book_from_ndl_record_prefers_primary_fields_over_fallbacks(self) -> None:
        book = book_from_ndl_record(
            """<dcndl:BibResource
  xmlns:dcndl="http://ndl.go.jp/dcndl/terms/"
  xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:dcterms="http://purl.org/dc/terms/">
  <dcndl:titleTranscription>パイソン テスティング</dcndl:titleTranscription>
  <dcterms:title>Python Testing</dcterms:title>
  <dcterms:date>2026-08-18</dcterms:date>
  <dcterms:issued>2026</dcterms:issued>
  <dc:identifier>9784297135782</dc:identifier>
</dcndl:BibResource>"""
        )

        self.assertEqual(book.title, "Python Testing")
        self.assertEqual(book.published_date, "2026")

    def test_book_from_ndl_record_extracts_isbn_from_seealso_resource(self) -> None:
        book = book_from_ndl_record(
            """<dcndl:BibResource
  xmlns:dcndl="http://ndl.go.jp/dcndl/terms/"
  xmlns:dcterms="http://purl.org/dc/terms/"
  xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
  xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#">
  <dcterms:title>Python Testing</dcterms:title>
  <rdfs:seeAlso rdf:resource="http://iss.ndl.go.jp/isbn/9784297135782" />
</dcndl:BibResource>"""
        )

        self.assertEqual(book.isbn13, "9784297135782")

    def test_book_from_ndl_record_does_not_use_setisbn_resource(self) -> None:
        book = book_from_ndl_record(
            """<dcndl:BibResource
  xmlns:dcndl="http://ndl.go.jp/dcndl/terms/"
  xmlns:dcterms="http://purl.org/dc/terms/"
  xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
  xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#">
  <dcterms:title>Python Testing</dcterms:title>
  <rdfs:seeAlso rdf:resource="http://iss.ndl.go.jp/setisbn/9784000000000" />
</dcndl:BibResource>""",
            isbn13="978-4-297-13578-2",
        )

        self.assertEqual(book.isbn13, "9784297135782")

    def test_book_from_ndl_record_does_not_use_setisbn_identifier(self) -> None:
        book = book_from_ndl_record(
            """<dcndl:BibResource
  xmlns:dcndl="http://ndl.go.jp/dcndl/terms/"
  xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:dcterms="http://purl.org/dc/terms/"
  xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dcterms:title>Python Testing</dcterms:title>
  <dc:identifier xsi:type="dcndl:SetISBN">9784000000000</dc:identifier>
  <dc:identifier rdf:datatype="http://ndl.go.jp/dcndl/terms/ErrorISBN">
    9784000000000
  </dc:identifier>
</dcndl:BibResource>""",
            isbn13="978-4-297-13578-2",
        )

        self.assertEqual(book.isbn13, "9784297135782")

    def test_book_from_ndl_record_uses_identifier_typed_as_isbn(self) -> None:
        book = book_from_ndl_record(
            """<dcndl:BibResource
  xmlns:dcndl="http://ndl.go.jp/dcndl/terms/"
  xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:dcterms="http://purl.org/dc/terms/"
  xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <dcterms:title>Python Testing</dcterms:title>
  <dc:identifier rdf:datatype="http://ndl.go.jp/dcndl/terms/SetISBN">
    9784000000000
  </dc:identifier>
  <dc:identifier rdf:datatype="http://ndl.go.jp/dcndl/terms/ISBN">
    9784297135782
  </dc:identifier>
</dcndl:BibResource>"""
        )

        self.assertEqual(book.isbn13, "9784297135782")

    def test_book_from_sru_response_returns_none_when_not_found(self) -> None:
        response = parse_sru_response(
            b"""<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">
  <numberOfRecords>0</numberOfRecords>
</searchRetrieveResponse>"""
        )

        self.assertIsNone(book_from_sru_response(response, isbn13="9784297135782"))

    def test_book_from_sru_response_maps_first_record(self) -> None:
        response = parse_sru_response(
            b"""<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">
  <numberOfRecords>1</numberOfRecords>
  <records>
    <record>
      <recordData>
        <dcndl:BibResource xmlns:dcndl="http://ndl.go.jp/dcndl/terms/">
          <dcndl:titleTranscription>Python Testing</dcndl:titleTranscription>
        </dcndl:BibResource>
      </recordData>
    </record>
  </records>
</searchRetrieveResponse>"""
        )

        book = book_from_sru_response(response, isbn13="9784297135782")

        self.assertIsNotNone(book)
        self.assertEqual(book.title, "Python Testing")

    def test_book_from_ndl_record_requires_title(self) -> None:
        with self.assertRaisesRegex(NdlApiError, "missing title"):
            book_from_ndl_record(
                """<dcndl:BibResource xmlns:dcndl="http://ndl.go.jp/dcndl/terms/">
  <dc:identifier xmlns:dc="http://purl.org/dc/elements/1.1/">9784297135782</dc:identifier>
</dcndl:BibResource>"""
            )

    def test_book_from_ndl_record_requires_isbn(self) -> None:
        with self.assertRaisesRegex(NdlApiError, "missing ISBN-13"):
            book_from_ndl_record(
                """<dcndl:BibResource xmlns:dcndl="http://ndl.go.jp/dcndl/terms/">
  <dcndl:titleTranscription>Python Testing</dcndl:titleTranscription>
</dcndl:BibResource>"""
            )

    def test_book_from_ndl_record_rejects_invalid_xml(self) -> None:
        with self.assertRaisesRegex(NdlApiError, "invalid XML"):
            book_from_ndl_record("<dcndl:BibResource>")

    def test_book_from_ndl_record_rejects_dtd(self) -> None:
        with self.assertRaisesRegex(NdlApiError, "invalid XML"):
            book_from_ndl_record(
                """<!DOCTYPE BibResource [
  <!ELEMENT BibResource ANY>
]>
<dcndl:BibResource xmlns:dcndl="http://ndl.go.jp/dcndl/terms/">
  <dcndl:titleTranscription>Python Testing</dcndl:titleTranscription>
</dcndl:BibResource>""",
                isbn13="9784297135782",
            )

    def test_book_from_ndl_record_rejects_entity_expansion(self) -> None:
        with self.assertRaisesRegex(NdlApiError, "invalid XML"):
            book_from_ndl_record(
                """<!DOCTYPE BibResource [
  <!ENTITY title "Python Testing">
]>
<dcndl:BibResource xmlns:dcndl="http://ndl.go.jp/dcndl/terms/">
  <dcndl:titleTranscription>&title;</dcndl:titleTranscription>
</dcndl:BibResource>""",
                isbn13="9784297135782",
            )


if __name__ == "__main__":
    unittest.main()
