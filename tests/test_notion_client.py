import json
import math
import unittest
from http.client import HTTPException, IncompleteRead
from urllib.error import HTTPError, URLError

from notion_book_register import Book, NotionApiError, NotionClient

TEST_DATA_SOURCE_ID = "00000000-0000-0000-0000-000000000000"


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
    def __init__(self, error: Exception) -> None:
        self._error = error

    def __enter__(self) -> "ErrorResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def read(self) -> bytes:
        raise self._error


class CloseTrackingBody:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.closed = False

    def read(self) -> bytes:
        return self._payload

    def close(self) -> None:
        self.closed = True


class ReadErrorBody:
    def __init__(self, error: Exception) -> None:
        self._error = error
        self.closed = False

    def read(self) -> bytes:
        raise self._error

    def close(self) -> None:
        self.closed = True


class CloseErrorBody:
    def __init__(self, payload: bytes, error: Exception) -> None:
        self._payload = payload
        self._error = error

    def read(self) -> bytes:
        return self._payload

    def close(self) -> None:
        raise self._error


class NotionClientTest(unittest.TestCase):
    def test_create_book_page_builds_notion_request_and_parses_response(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return FakeResponse(
                b"""{
  "object": "page",
  "id": "3c90c3cc-0d44-4b50-8888-8dd25736052a",
  "url": "https://www.notion.so/page"
}"""
            )

        client = NotionClient(
            "secret_token",
            data_source_id=TEST_DATA_SOURCE_ID,
            timeout=3.0,
            opener=opener,
        )

        page = client.create_book_page(
            Book(
                isbn13="978-4-297-13578-2",
                title="Python Testing",
                authors=("Author A",),
                publisher="Publisher",
                published_date="2026",
                ndl_url="https://ndl.example/books/1",
            ),
            genre="技術書",
            prevent_duplicates=False,
        )

        self.assertEqual(page.page_id, "3c90c3cc-0d44-4b50-8888-8dd25736052a")
        self.assertEqual(page.url, "https://www.notion.so/page")

        request, timeout = requests[0]
        self.assertEqual(timeout, 3.0)
        self.assertEqual(request.full_url, "https://api.notion.com/v1/pages")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.headers["Authorization"], "Bearer secret_token")
        self.assertEqual(request.headers["Content-type"], "application/json")
        self.assertEqual(request.headers["Notion-version"], "2026-03-11")
        self.assertEqual(request.headers["User-agent"], "notion-book-register/0.1")

        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(
            body["parent"],
            {
                "type": "data_source_id",
                "data_source_id": TEST_DATA_SOURCE_ID,
            },
        )
        self.assertEqual(
            body["properties"]["作品名"],
            {"title": [{"type": "text", "text": {"content": "Python Testing"}}]},
        )
        self.assertEqual(body["properties"]["状態"], {"select": {"name": "未読"}})
        self.assertEqual(body["properties"]["ジャンル"], {"select": {"name": "技術書"}})
        self.assertIn(
            "ISBN: 9784297135782",
            body["properties"]["memo"]["rich_text"][0]["text"]["content"],
        )

    def test_create_book_page_omits_genre_when_not_provided(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse(b'{"id": "page-id", "url": null}')

        client = NotionClient("secret_token", data_source_id=TEST_DATA_SOURCE_ID, opener=opener)

        client.create_book_page(
            Book(isbn13="9784297135782", title="Python Testing"),
            prevent_duplicates=False,
        )

        body = json.loads(requests[0].data.decode("utf-8"))
        self.assertNotIn("ジャンル", body["properties"])

    def test_create_book_page_splits_long_text_properties(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse(b'{"id": "page-id", "url": null}')

        client = NotionClient("secret_token", data_source_id=TEST_DATA_SOURCE_ID, opener=opener)
        long_title = "T" * 2500
        long_ndl_url = "https://ndl.example/" + ("a" * 5000)

        client.create_book_page(
            Book(
                isbn13="9784297135782",
                title=long_title,
                ndl_url=long_ndl_url,
            ),
            prevent_duplicates=False,
        )

        body = json.loads(requests[0].data.decode("utf-8"))
        title_chunks = body["properties"]["作品名"]["title"]
        memo_chunks = body["properties"]["memo"]["rich_text"]

        self.assertEqual("".join(chunk["text"]["content"] for chunk in title_chunks), long_title)
        self.assertGreater(len(title_chunks), 1)
        self.assertGreater(len(memo_chunks), 1)
        for chunk in (*title_chunks, *memo_chunks):
            self.assertLessEqual(len(chunk["text"]["content"]), 2000)

    def test_find_book_page_by_isbn_queries_memo_property(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return FakeResponse(
                b"""{
  "object": "list",
  "results": [
    {
      "object": "page",
      "id": "existing-page-id",
      "url": "https://www.notion.so/existing"
    }
  ]
}"""
            )

        client = NotionClient(
            "secret_token",
            data_source_id=TEST_DATA_SOURCE_ID,
            timeout=3.0,
            opener=opener,
        )

        page = client.find_book_page_by_isbn("978-4-297-13578-2")

        self.assertIsNotNone(page)
        assert page is not None
        self.assertEqual(page.page_id, "existing-page-id")
        self.assertEqual(page.url, "https://www.notion.so/existing")
        self.assertFalse(page.created)

        request, timeout = requests[0]
        self.assertEqual(timeout, 3.0)
        self.assertEqual(
            request.full_url,
            f"https://api.notion.com/v1/data_sources/{TEST_DATA_SOURCE_ID}/query",
        )
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.headers["Authorization"], "Bearer secret_token")
        self.assertEqual(request.headers["Content-type"], "application/json")
        self.assertEqual(request.headers["Notion-version"], "2026-03-11")

        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(
            body,
            {
                "filter": {
                    "property": "memo",
                    "rich_text": {
                        "contains": "ISBN: 9784297135782",
                    },
                },
                "page_size": 1,
            },
        )

    def test_find_book_page_by_isbn_returns_none_when_not_found(self) -> None:
        client = NotionClient(
            "secret_token",
            data_source_id=TEST_DATA_SOURCE_ID,
            opener=lambda request, timeout: FakeResponse(b'{"object": "list", "results": []}'),
        )

        self.assertIsNone(client.find_book_page_by_isbn("9784297135782"))

    def test_find_book_page_by_isbn_ignores_non_page_result(self) -> None:
        client = NotionClient(
            "secret_token",
            data_source_id=TEST_DATA_SOURCE_ID,
            opener=lambda request, timeout: FakeResponse(
                b"""{
  "object": "list",
  "results": [
    {
      "object": "data_source",
      "id": "nested-data-source-id",
      "url": "https://www.notion.so/nested"
    }
  ]
}"""
            ),
        )

        self.assertIsNone(client.find_book_page_by_isbn("9784297135782"))

    def test_create_book_page_skips_create_when_isbn_already_exists(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse(
                b"""{
  "object": "list",
  "results": [
    {
      "object": "page",
      "id": "existing-page-id",
      "url": "https://www.notion.so/existing"
    }
  ]
}"""
            )

        client = NotionClient("secret_token", data_source_id=TEST_DATA_SOURCE_ID, opener=opener)

        page = client.create_book_page(Book(isbn13="9784297135782", title="Python Testing"))

        self.assertEqual(page.page_id, "existing-page-id")
        self.assertEqual(page.url, "https://www.notion.so/existing")
        self.assertFalse(page.created)
        self.assertEqual(len(requests), 1)
        self.assertIn("/data_sources/", requests[0].full_url)

    def test_create_book_page_creates_when_isbn_does_not_exist(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append(request)
            if "/data_sources/" in request.full_url:
                return FakeResponse(b'{"object": "list", "results": []}')
            return FakeResponse(b'{"id": "created-page-id", "url": null}')

        client = NotionClient("secret_token", data_source_id=TEST_DATA_SOURCE_ID, opener=opener)

        page = client.create_book_page(Book(isbn13="9784297135782", title="Python Testing"))

        self.assertEqual(page.page_id, "created-page-id")
        self.assertTrue(page.created)
        self.assertEqual(len(requests), 2)
        self.assertIn("/data_sources/", requests[0].full_url)
        self.assertEqual(requests[1].full_url, "https://api.notion.com/v1/pages")

    def test_find_book_page_by_isbn_rejects_missing_results(self) -> None:
        client = NotionClient(
            "secret_token",
            data_source_id=TEST_DATA_SOURCE_ID,
            opener=lambda request, timeout: FakeResponse(b"{}"),
        )

        with self.assertRaisesRegex(NotionApiError, "missing results"):
            client.find_book_page_by_isbn("9784297135782")

    def test_create_book_page_rejects_empty_token(self) -> None:
        with self.assertRaises(ValueError):
            NotionClient(" ", data_source_id=TEST_DATA_SOURCE_ID)

    def test_create_book_page_rejects_empty_data_source_id(self) -> None:
        with self.assertRaises(ValueError):
            NotionClient("secret_token", data_source_id=" ")

    def test_create_book_page_rejects_invalid_timeout(self) -> None:
        for timeout in (0.0, -1.0, math.nan, math.inf):
            with self.subTest(timeout=timeout):
                with self.assertRaisesRegex(ValueError, "positive finite"):
                    NotionClient(
                        "secret_token",
                        data_source_id=TEST_DATA_SOURCE_ID,
                        timeout=timeout,
                    )

    def test_from_env_uses_notion_api_key(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return FakeResponse(b'{"id": "page-id", "url": null}')

        client = NotionClient.from_env(
            environ={
                "NOTION_API_KEY": "secret_from_env",
                "NOTION_BOOKSHELF_DATA_SOURCE_ID": TEST_DATA_SOURCE_ID,
            },
            timeout=3.0,
            opener=opener,
        )

        client.create_book_page(
            Book(isbn13="9784297135782", title="Python Testing"),
            prevent_duplicates=False,
        )

        request, timeout = requests[0]
        self.assertEqual(timeout, 3.0)
        self.assertEqual(request.headers["Authorization"], "Bearer secret_from_env")

    def test_from_env_falls_back_to_notion_token(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse(b'{"id": "page-id", "url": null}')

        client = NotionClient.from_env(
            environ={
                "NOTION_TOKEN": "secret_token",
                "NOTION_BOOKSHELF_DATA_SOURCE_ID": TEST_DATA_SOURCE_ID,
            },
            opener=opener,
        )

        client.create_book_page(
            Book(isbn13="9784297135782", title="Python Testing"),
            prevent_duplicates=False,
        )

        self.assertEqual(requests[0].headers["Authorization"], "Bearer secret_token")

    def test_from_env_uses_bookshelf_data_source_id(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse(b'{"id": "page-id", "url": null}')

        client = NotionClient.from_env(
            environ={
                "NOTION_API_KEY": "secret_token",
                "NOTION_BOOKSHELF_DATA_SOURCE_ID": "custom-data-source-id",
            },
            opener=opener,
        )

        client.create_book_page(
            Book(isbn13="9784297135782", title="Python Testing"),
            prevent_duplicates=False,
        )

        body = json.loads(requests[0].data.decode("utf-8"))
        self.assertEqual(body["parent"]["data_source_id"], "custom-data-source-id")

    def test_from_env_requires_token(self) -> None:
        with self.assertRaisesRegex(ValueError, "NOTION_API_KEY or NOTION_TOKEN"):
            NotionClient.from_env(environ={})

    def test_from_env_requires_data_source_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "NOTION_BOOKSHELF_DATA_SOURCE_ID"):
            NotionClient.from_env(environ={"NOTION_API_KEY": "secret_token"})

    def test_create_book_page_wraps_http_error_detail(self) -> None:
        response_body = CloseTrackingBody(b'{"message": "Invalid request"}')

        def opener(request, timeout):
            raise HTTPError(request.full_url, 400, "Bad Request", {}, response_body)

        client = NotionClient("secret_token", data_source_id=TEST_DATA_SOURCE_ID, opener=opener)

        with self.assertRaisesRegex(NotionApiError, "HTTP 400: Invalid request"):
            client.create_book_page(
                Book(isbn13="9784297135782", title="Python Testing"),
                prevent_duplicates=False,
            )

        self.assertTrue(response_body.closed)

    def test_create_book_page_wraps_http_error_with_non_object_body(self) -> None:
        def opener(request, timeout):
            raise HTTPError(request.full_url, 400, "Bad Request", {}, CloseTrackingBody(b"[]"))

        client = NotionClient("secret_token", data_source_id=TEST_DATA_SOURCE_ID, opener=opener)

        with self.assertRaisesRegex(NotionApiError, r"HTTP 400\."):
            client.create_book_page(
                Book(isbn13="9784297135782", title="Python Testing"),
                prevent_duplicates=False,
            )

    def test_create_book_page_wraps_http_error_when_body_read_fails(self) -> None:
        response_body = ReadErrorBody(IncompleteRead(b""))

        def opener(request, timeout):
            raise HTTPError(request.full_url, 500, "Server Error", {}, response_body)

        client = NotionClient("secret_token", data_source_id=TEST_DATA_SOURCE_ID, opener=opener)

        with self.assertRaisesRegex(NotionApiError, r"HTTP 500\."):
            client.create_book_page(
                Book(isbn13="9784297135782", title="Python Testing"),
                prevent_duplicates=False,
            )

        self.assertTrue(response_body.closed)

    def test_create_book_page_wraps_http_error_without_body(self) -> None:
        def opener(request, timeout):
            raise HTTPError(request.full_url, 502, "Bad Gateway", {}, None)

        client = NotionClient("secret_token", data_source_id=TEST_DATA_SOURCE_ID, opener=opener)

        with self.assertRaisesRegex(NotionApiError, r"HTTP 502\."):
            client.create_book_page(
                Book(isbn13="9784297135782", title="Python Testing"),
                prevent_duplicates=False,
            )

    def test_create_book_page_wraps_http_error_when_close_fails(self) -> None:
        for close_error in (OSError("close failed"), HTTPException("close failed")):
            with self.subTest(close_error=type(close_error).__name__):

                def opener(request, timeout, error=close_error):
                    raise HTTPError(
                        request.full_url,
                        503,
                        "Service Unavailable",
                        {},
                        CloseErrorBody(b'{"message": "temporarily unavailable"}', error),
                    )

                client = NotionClient(
                    "secret_token",
                    data_source_id=TEST_DATA_SOURCE_ID,
                    opener=opener,
                )

                with self.assertRaisesRegex(
                    NotionApiError,
                    "HTTP 503: temporarily unavailable",
                ):
                    client.create_book_page(
                        Book(isbn13="9784297135782", title="Python Testing"),
                        prevent_duplicates=False,
                    )

    def test_create_book_page_wraps_url_error(self) -> None:
        def opener(request, timeout):
            raise URLError("timeout")

        client = NotionClient("secret_token", data_source_id=TEST_DATA_SOURCE_ID, opener=opener)

        with self.assertRaisesRegex(NotionApiError, "timeout"):
            client.create_book_page(
                Book(isbn13="9784297135782", title="Python Testing"),
                prevent_duplicates=False,
            )

    def test_create_book_page_wraps_read_timeout(self) -> None:
        client = NotionClient(
            "secret_token",
            data_source_id=TEST_DATA_SOURCE_ID,
            opener=lambda request, timeout: ErrorResponse(TimeoutError()),
        )

        with self.assertRaisesRegex(NotionApiError, "Timed out"):
            client.create_book_page(
                Book(isbn13="9784297135782", title="Python Testing"),
                prevent_duplicates=False,
            )

    def test_create_book_page_wraps_incomplete_read(self) -> None:
        client = NotionClient(
            "secret_token",
            data_source_id=TEST_DATA_SOURCE_ID,
            opener=lambda request, timeout: ErrorResponse(IncompleteRead(b"")),
        )

        with self.assertRaisesRegex(NotionApiError, "IncompleteRead"):
            client.create_book_page(
                Book(isbn13="9784297135782", title="Python Testing"),
                prevent_duplicates=False,
            )

    def test_create_book_page_rejects_invalid_json_response(self) -> None:
        client = NotionClient(
            "secret_token",
            data_source_id=TEST_DATA_SOURCE_ID,
            opener=lambda request, timeout: FakeResponse(b"{"),
        )

        with self.assertRaisesRegex(NotionApiError, "invalid JSON"):
            client.create_book_page(
                Book(isbn13="9784297135782", title="Python Testing"),
                prevent_duplicates=False,
            )

    def test_create_book_page_rejects_non_object_json_response(self) -> None:
        client = NotionClient(
            "secret_token",
            data_source_id=TEST_DATA_SOURCE_ID,
            opener=lambda request, timeout: FakeResponse(b"[]"),
        )

        with self.assertRaisesRegex(NotionApiError, "JSON object"):
            client.create_book_page(
                Book(isbn13="9784297135782", title="Python Testing"),
                prevent_duplicates=False,
            )

    def test_create_book_page_requires_response_page_id(self) -> None:
        client = NotionClient(
            "secret_token",
            data_source_id=TEST_DATA_SOURCE_ID,
            opener=lambda request, timeout: FakeResponse(b"{}"),
        )

        with self.assertRaisesRegex(NotionApiError, "missing page id"):
            client.create_book_page(
                Book(isbn13="9784297135782", title="Python Testing"),
                prevent_duplicates=False,
            )


if __name__ == "__main__":
    unittest.main()
