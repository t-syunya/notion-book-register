import unittest
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

from notion_book_register import NdlApiError, NdlClient, parse_sru_response

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

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


class ErrorResponse:
    def __init__(self, error: OSError) -> None:
        self._error = error

    def __enter__(self) -> "ErrorResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def read(self) -> bytes:
        raise self._error


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

    def test_search_by_isbn_wraps_http_error(self) -> None:
        def opener(request, timeout):
            raise HTTPError(request.full_url, 500, "Server Error", {}, None)

        client = NdlClient(opener=opener)

        with self.assertRaisesRegex(NdlApiError, "HTTP 500"):
            client.search_by_isbn("9784297135782")

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

    def test_parse_sru_response_requires_number_of_records(self) -> None:
        with self.assertRaisesRegex(NdlApiError, "missing numberOfRecords"):
            parse_sru_response(b"<searchRetrieveResponse />")

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


if __name__ == "__main__":
    unittest.main()
