import json
import unittest
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError

from notion_book_register import Book, NotionApiError, NotionClient


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


class CloseTrackingBody:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.closed = False

    def read(self) -> bytes:
        return self._payload

    def close(self) -> None:
        self.closed = True


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
            data_source_id="2bddc1bd-5d17-8199-8910-000b299eb538",
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
                "data_source_id": "2bddc1bd-5d17-8199-8910-000b299eb538",
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

        client = NotionClient("secret_token", opener=opener)

        client.create_book_page(Book(isbn13="9784297135782", title="Python Testing"))

        body = json.loads(requests[0].data.decode("utf-8"))
        self.assertNotIn("ジャンル", body["properties"])

    def test_create_book_page_rejects_empty_token(self) -> None:
        with self.assertRaises(ValueError):
            NotionClient(" ")

    def test_create_book_page_rejects_empty_data_source_id(self) -> None:
        with self.assertRaises(ValueError):
            NotionClient("secret_token", data_source_id=" ")

    def test_from_env_uses_notion_api_key(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return FakeResponse(b'{"id": "page-id", "url": null}')

        client = NotionClient.from_env(
            environ={"NOTION_API_KEY": "secret_from_env"},
            timeout=3.0,
            opener=opener,
        )

        client.create_book_page(Book(isbn13="9784297135782", title="Python Testing"))

        request, timeout = requests[0]
        self.assertEqual(timeout, 3.0)
        self.assertEqual(request.headers["Authorization"], "Bearer secret_from_env")

    def test_from_env_falls_back_to_notion_token(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse(b'{"id": "page-id", "url": null}')

        client = NotionClient.from_env(environ={"NOTION_TOKEN": "secret_token"}, opener=opener)

        client.create_book_page(Book(isbn13="9784297135782", title="Python Testing"))

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

        client.create_book_page(Book(isbn13="9784297135782", title="Python Testing"))

        body = json.loads(requests[0].data.decode("utf-8"))
        self.assertEqual(body["parent"]["data_source_id"], "custom-data-source-id")

    def test_from_env_requires_token(self) -> None:
        with self.assertRaisesRegex(ValueError, "NOTION_API_KEY or NOTION_TOKEN"):
            NotionClient.from_env(environ={})

    def test_create_book_page_wraps_http_error_detail(self) -> None:
        response_body = CloseTrackingBody(b'{"message": "Invalid request"}')

        def opener(request, timeout):
            raise HTTPError(request.full_url, 400, "Bad Request", {}, response_body)

        client = NotionClient("secret_token", opener=opener)

        with self.assertRaisesRegex(NotionApiError, "HTTP 400: Invalid request"):
            client.create_book_page(Book(isbn13="9784297135782", title="Python Testing"))

        self.assertTrue(response_body.closed)

    def test_create_book_page_wraps_url_error(self) -> None:
        def opener(request, timeout):
            raise URLError("timeout")

        client = NotionClient("secret_token", opener=opener)

        with self.assertRaisesRegex(NotionApiError, "timeout"):
            client.create_book_page(Book(isbn13="9784297135782", title="Python Testing"))

    def test_create_book_page_wraps_read_timeout(self) -> None:
        client = NotionClient(
            "secret_token",
            opener=lambda request, timeout: ErrorResponse(TimeoutError()),
        )

        with self.assertRaisesRegex(NotionApiError, "Timed out"):
            client.create_book_page(Book(isbn13="9784297135782", title="Python Testing"))

    def test_create_book_page_wraps_incomplete_read(self) -> None:
        client = NotionClient(
            "secret_token",
            opener=lambda request, timeout: ErrorResponse(IncompleteRead(b"")),
        )

        with self.assertRaisesRegex(NotionApiError, "IncompleteRead"):
            client.create_book_page(Book(isbn13="9784297135782", title="Python Testing"))

    def test_create_book_page_rejects_invalid_json_response(self) -> None:
        client = NotionClient("secret_token", opener=lambda request, timeout: FakeResponse(b"{"))

        with self.assertRaisesRegex(NotionApiError, "invalid JSON"):
            client.create_book_page(Book(isbn13="9784297135782", title="Python Testing"))

    def test_create_book_page_requires_response_page_id(self) -> None:
        client = NotionClient("secret_token", opener=lambda request, timeout: FakeResponse(b"{}"))

        with self.assertRaisesRegex(NotionApiError, "missing page id"):
            client.create_book_page(Book(isbn13="9784297135782", title="Python Testing"))


if __name__ == "__main__":
    unittest.main()
