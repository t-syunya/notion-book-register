import base64
import http.client
import json
import socket
import threading
import time
import unittest
from http import HTTPStatus

from notion_book_register import (
    Book,
    BookNotFoundError,
    CreatedNotionPage,
    IsbnNotDetectedError,
    RegisteredBook,
    VlmApiError,
)
from notion_book_register.api import (
    ApiConfig,
    ApiRequestError,
    BookRegistrationHttpServer,
    make_handler,
    parse_registration_request,
)

TEST_TOKEN = "test-token-0123456789-abcdef-xyz"


class FakeRegistrationService:
    def __init__(self, *, created=True, error=None) -> None:
        self.created = created
        self.error = error
        self.calls = []

    def register_image(
        self,
        image,
        *,
        mime_type,
        title=None,
        author=None,
        genre=None,
    ):
        self.calls.append((image, mime_type, title, author, genre))
        if self.error is not None:
            raise self.error
        return RegisteredBook(
            book=Book(isbn13="9784297135782", title="Python Testing"),
            page=CreatedNotionPage(
                page_id="page-id",
                url="https://www.notion.so/page-id",
                created=self.created,
            ),
        )


class ApiConfigTest(unittest.TestCase):
    def test_from_env_parses_server_configuration(self) -> None:
        config = ApiConfig.from_env(
            {
                "BOOK_REGISTER_API_TOKEN": f" {TEST_TOKEN} ",
                "BOOK_REGISTER_HOST": " 0.0.0.0 ",
                "BOOK_REGISTER_PORT": "8080",
                "BOOK_REGISTER_MAX_IMAGE_BYTES": "1234",
                "BOOK_REGISTER_REQUEST_TIMEOUT_SECONDS": "2.5",
                "BOOK_REGISTER_MAX_CONCURRENT_REQUESTS": "7",
            }
        )

        self.assertEqual(config.api_token, TEST_TOKEN)
        self.assertEqual(config.host, "0.0.0.0")
        self.assertEqual(config.port, 8080)
        self.assertEqual(config.max_image_bytes, 1234)
        self.assertEqual(config.request_timeout_seconds, 2.5)
        self.assertEqual(config.max_concurrent_requests, 7)

    def test_from_env_requires_token_and_valid_numeric_values(self) -> None:
        invalid_cases = (
            ({}, "API_TOKEN"),
            ({"BOOK_REGISTER_API_TOKEN": "short"}, "32-512"),
            ({"BOOK_REGISTER_API_TOKEN": "秘" * 32}, "ASCII"),
            ({"BOOK_REGISTER_API_TOKEN": TEST_TOKEN, "BOOK_REGISTER_PORT": "0"}, "port"),
            ({"BOOK_REGISTER_API_TOKEN": TEST_TOKEN, "BOOK_REGISTER_PORT": "65536"}, "PORT"),
            (
                {"BOOK_REGISTER_API_TOKEN": TEST_TOKEN, "BOOK_REGISTER_MAX_IMAGE_BYTES": "x"},
                "max image bytes",
            ),
            (
                {
                    "BOOK_REGISTER_API_TOKEN": TEST_TOKEN,
                    "BOOK_REGISTER_REQUEST_TIMEOUT_SECONDS": "inf",
                },
                "positive finite",
            ),
        )

        for environ, message in invalid_cases:
            with self.subTest(environ=environ):
                with self.assertRaisesRegex(ValueError, message):
                    ApiConfig.from_env(environ)


class RegistrationRequestTest(unittest.TestCase):
    def test_parse_registration_request_decodes_image_and_optional_fields(self) -> None:
        request = parse_registration_request(
            json.dumps(
                {
                    "image": base64.b64encode(b"image bytes").decode("ascii"),
                    "mime_type": " image/jpeg ",
                    "title": " Python Testing ",
                    "author": " Author A ",
                    "genre": " 技術書 ",
                }
            ).encode("utf-8"),
            max_image_bytes=100,
        )

        self.assertEqual(request.image, b"image bytes")
        self.assertEqual(request.mime_type, "image/jpeg")
        self.assertEqual(request.title, "Python Testing")
        self.assertEqual(request.author, "Author A")
        self.assertEqual(request.genre, "技術書")

    def test_parse_registration_request_rejects_invalid_payloads(self) -> None:
        valid_image = base64.b64encode(b"image").decode("ascii")
        small_image = base64.b64encode(b"x").decode("ascii")
        invalid_payloads = (
            (b"not json", HTTPStatus.BAD_REQUEST),
            (b"[]", HTTPStatus.BAD_REQUEST),
            (
                json.dumps({"image": "!", "mime_type": "image/jpeg"}).encode(),
                HTTPStatus.BAD_REQUEST,
            ),
            (
                json.dumps(
                    {"image": small_image, "mime_type": "image/jpeg", "title": "Title"}
                ).encode(),
                HTTPStatus.BAD_REQUEST,
            ),
            (
                json.dumps({"image": valid_image, "mime_type": "image/jpeg"}).encode(),
                HTTPStatus.CONTENT_TOO_LARGE,
            ),
            (
                json.dumps({"image": small_image, "mime_type": "application/pdf"}).encode(),
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            ),
        )

        for payload, expected_status in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ApiRequestError) as context:
                    parse_registration_request(payload, max_image_bytes=4)
                self.assertEqual(context.exception.status, expected_status)


class BookRegistrationEndpointTest(unittest.TestCase):
    def test_v1_post_keeps_legacy_success_response(self) -> None:
        status, response, _headers = self._request(
            FakeRegistrationService(),
            "POST",
            "/v1/books",
            body=json.dumps(
                {
                    "image": base64.b64encode(b"image bytes").decode("ascii"),
                    "mime_type": "image/jpeg",
                }
            ).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {TEST_TOKEN}",
                "Content-Type": "application/json",
            },
        )

        self.assertEqual(status, HTTPStatus.CREATED)
        self.assertEqual(response["isbn13"], "9784297135782")
        self.assertEqual(response["page_id"], "page-id")
        self.assertTrue(response["created"])
        self.assertNotIn("ok", response)

    def test_v1_post_keeps_legacy_error_response(self) -> None:
        status, response, headers = self._request(
            FakeRegistrationService(),
            "POST",
            "/v1/books",
            body=b"{}",
            headers={"Content-Type": "application/json"},
        )

        self.assertEqual(status, HTTPStatus.UNAUTHORIZED)
        self.assertEqual(response, {"error": "Authorization bearer token is invalid."})
        self.assertEqual(headers["WWW-Authenticate"], "Bearer")

    def test_post_registers_image_and_returns_created_page(self) -> None:
        service = FakeRegistrationService()
        status, response, headers = self._request(
            service,
            "POST",
            "/v2/books",
            body=json.dumps(
                {
                    "image": base64.b64encode(b"image bytes").decode("ascii"),
                    "mime_type": "image/jpeg",
                }
            ).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {TEST_TOKEN}",
                "Content-Type": "application/json",
            },
        )

        self.assertEqual(status, HTTPStatus.CREATED)
        self.assertTrue(response["ok"])
        self.assertIn("登録しました", response["message"])
        self.assertEqual(response["result"]["isbn13"], "9784297135782")
        self.assertEqual(response["result"]["page_id"], "page-id")
        self.assertTrue(response["result"]["created"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["Connection"], "close")
        self.assertEqual(service.calls[0][:2], (b"image bytes", "image/jpeg"))

    def test_post_requires_valid_bearer_token_without_calling_service(self) -> None:
        service = FakeRegistrationService()

        status, response, headers = self._request(
            service,
            "POST",
            "/v2/books",
            body=b"{}",
            headers={"Content-Type": "application/json"},
        )

        self.assertEqual(status, HTTPStatus.UNAUTHORIZED)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "unauthorized")
        self.assertFalse(response["error"]["retryable"])
        self.assertEqual(headers["WWW-Authenticate"], "Bearer")
        self.assertEqual(service.calls, [])

    def test_post_rejects_malformed_bearer_tokens_without_calling_service(self) -> None:
        invalid_tokens = ("é" * 32, "a" * 513, "a" * 31 + " ")

        for token in invalid_tokens:
            service = FakeRegistrationService()
            with self.subTest(token_length=len(token)):
                status, _response, _headers = self._request(
                    service,
                    "POST",
                    "/v2/books",
                    body=b"{}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                )

                self.assertEqual(status, HTTPStatus.UNAUTHORIZED)
                self.assertEqual(service.calls, [])

    def test_post_hides_unexpected_service_error_behind_json_500(self) -> None:
        service = FakeRegistrationService(error=ValueError("internal secret detail"))

        status, response, _headers = self._request(
            service,
            "POST",
            "/v2/books",
            body=json.dumps(
                {
                    "image": base64.b64encode(b"image").decode("ascii"),
                    "mime_type": "image/jpeg",
                }
            ).encode(),
            headers={
                "Authorization": f"Bearer {TEST_TOKEN}",
                "Content-Type": "application/json",
            },
        )

        self.assertEqual(status, HTTPStatus.INTERNAL_SERVER_ERROR)
        self.assertEqual(response["error"]["code"], "internal_error")
        self.assertNotIn("secret", response["message"])

    def test_post_returns_shortcut_friendly_duplicate_result(self) -> None:
        status, response, _headers = self._request(
            FakeRegistrationService(created=False),
            "POST",
            "/v2/books",
            body=json.dumps(
                {
                    "image": base64.b64encode(b"image").decode("ascii"),
                    "mime_type": "image/jpeg",
                }
            ).encode(),
            headers={
                "Authorization": f"Bearer {TEST_TOKEN}",
                "Content-Type": "application/json",
            },
        )

        self.assertEqual(status, HTTPStatus.OK)
        self.assertTrue(response["ok"])
        self.assertFalse(response["result"]["created"])
        self.assertIn("すでに", response["message"])

    def test_post_returns_stable_error_codes_for_registration_failures(self) -> None:
        cases = (
            (
                IsbnNotDetectedError("detail"),
                HTTPStatus.UNPROCESSABLE_CONTENT,
                "isbn_not_detected",
                False,
            ),
            (
                BookNotFoundError("detail"),
                HTTPStatus.UNPROCESSABLE_CONTENT,
                "book_not_found",
                False,
            ),
            (
                VlmApiError("secret upstream detail"),
                HTTPStatus.BAD_GATEWAY,
                "upstream_error",
                False,
            ),
        )

        for error, expected_status, expected_code, expected_retryable in cases:
            with self.subTest(expected_code=expected_code):
                status, response, _headers = self._request(
                    FakeRegistrationService(error=error),
                    "POST",
                    "/v2/books",
                    body=json.dumps(
                        {
                            "image": base64.b64encode(b"image").decode("ascii"),
                            "mime_type": "image/jpeg",
                        }
                    ).encode(),
                    headers={
                        "Authorization": f"Bearer {TEST_TOKEN}",
                        "Content-Type": "application/json",
                    },
                )

                self.assertEqual(status, expected_status)
                self.assertFalse(response["ok"])
                self.assertEqual(response["error"]["code"], expected_code)
                self.assertEqual(response["error"]["retryable"], expected_retryable)
                self.assertNotIn("secret", response["message"])
                self.assertNotIn("再実行してください", response["message"])

    def test_v1_keeps_bad_request_status_for_unsupported_image_mime_type(self) -> None:
        status, response, _headers = self._request(
            FakeRegistrationService(),
            "POST",
            "/v1/books",
            body=json.dumps(
                {
                    "image": base64.b64encode(b"image").decode("ascii"),
                    "mime_type": "application/pdf",
                }
            ).encode(),
            headers={
                "Authorization": f"Bearer {TEST_TOKEN}",
                "Content-Type": "application/json",
            },
        )

        self.assertEqual(status, HTTPStatus.BAD_REQUEST)
        self.assertIsInstance(response["error"], str)

    def test_v2_rejects_unsupported_methods_with_json_error(self) -> None:
        for method in ("GET", "PUT", "PATCH", "DELETE", "OPTIONS"):
            with self.subTest(method=method):
                status, response, headers = self._request(
                    FakeRegistrationService(),
                    method,
                    "/v2/books",
                )

                self.assertEqual(status, HTTPStatus.METHOD_NOT_ALLOWED)
                self.assertFalse(response["ok"])
                self.assertEqual(response["error"]["code"], "method_not_allowed")
                self.assertFalse(response["error"]["retryable"])
                self.assertEqual(headers["Allow"], "POST")

    def test_method_handling_matches_health_and_unknown_routes(self) -> None:
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            with self.subTest(method=method):
                status, response, headers = self._request(
                    FakeRegistrationService(), method, "/healthz"
                )

                self.assertEqual(status, HTTPStatus.METHOD_NOT_ALLOWED)
                self.assertEqual(response["error"]["code"], "method_not_allowed")
                self.assertEqual(headers["Allow"], "GET, HEAD")

        status, response, headers = self._request(FakeRegistrationService(), "PUT", "/unknown")

        self.assertEqual(status, HTTPStatus.NOT_FOUND)
        self.assertEqual(response["error"]["code"], "not_found")
        self.assertNotIn("Allow", headers)

    def test_partial_body_times_out_and_server_remains_available(self) -> None:
        handler = make_handler(
            FakeRegistrationService(),
            api_token=TEST_TOKEN,
            max_image_bytes=100,
        )
        handler.log_message = lambda self, format, *args: None
        server = BookRegistrationHttpServer(
            ("127.0.0.1", 0),
            handler,
            request_timeout_seconds=0.1,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = socket.create_connection(server.server_address, timeout=1)
        try:
            client.sendall(
                (
                    "POST /v1/books HTTP/1.1\r\n"
                    f"Host: {server.server_address[0]}\r\n"
                    f"Authorization: Bearer {TEST_TOKEN}\r\n"
                    "Content-Type: application/json\r\n"
                    "Content-Length: 100\r\n\r\n{"
                ).encode("ascii")
            )
            response = client.recv(4096)
            self.assertIn(b"408 Request Timeout", response)

            connection = http.client.HTTPConnection(*server.server_address, timeout=1)
            try:
                connection.request("GET", "/healthz")
                self.assertEqual(connection.getresponse().status, HTTPStatus.OK)
            finally:
                connection.close()
        finally:
            client.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_post_accepts_expect_100_continue(self) -> None:
        service = FakeRegistrationService()
        handler = make_handler(service, api_token=TEST_TOKEN, max_image_bytes=100)
        handler.log_message = lambda self, format, *args: None
        server = BookRegistrationHttpServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = socket.create_connection(server.server_address, timeout=1)
        body = json.dumps(
            {
                "image": base64.b64encode(b"image bytes").decode("ascii"),
                "mime_type": "image/jpeg",
            }
        ).encode("utf-8")
        try:
            client.sendall(
                (
                    "POST /v1/books HTTP/1.1\r\n"
                    f"Host: {server.server_address[0]}\r\n"
                    f"Authorization: Bearer {TEST_TOKEN}\r\n"
                    "Content-Type: application/json\r\n"
                    "Expect: 100-continue\r\n"
                    f"Content-Length: {len(body)}\r\n\r\n"
                ).encode("ascii")
            )
            self.assertTrue(client.recv(4096).startswith(b"HTTP/1.1 100 Continue\r\n\r\n"))

            client.sendall(body)
            response = client.recv(4096)
            self.assertIn(b"201 Created", response)
            self.assertIn(b"Connection: close", response)
            self.assertEqual(service.calls[0][:2], (b"image bytes", "image/jpeg"))
        finally:
            client.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_server_rejects_requests_over_concurrency_limit(self) -> None:
        handler = make_handler(
            FakeRegistrationService(),
            api_token=TEST_TOKEN,
            max_image_bytes=100,
        )
        handler.log_message = lambda self, format, *args: None
        request_started = threading.Event()
        original_setup = handler.setup

        def setup(request_handler):
            original_setup(request_handler)
            request_started.set()

        handler.setup = setup
        server = BookRegistrationHttpServer(
            ("127.0.0.1", 0),
            handler,
            request_timeout_seconds=1,
            max_concurrent_requests=1,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        slow_client = socket.create_connection(server.server_address, timeout=1)
        try:
            slow_client.sendall(b"GET /healthz HTTP/1.1\r\n")
            self.assertTrue(request_started.wait(timeout=1))
            for path in ("/v1/books", "/v2/books"):
                connection = http.client.HTTPConnection(*server.server_address, timeout=1)
                try:
                    connection.request(
                        "POST",
                        path,
                        body=b"{}",
                        headers={"Content-Type": "application/json"},
                    )
                    response = connection.getresponse()
                    body = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(response.status, HTTPStatus.SERVICE_UNAVAILABLE)
                    self.assertEqual(
                        response.headers["Content-Type"], "application/json; charset=utf-8"
                    )
                    self.assertEqual(body, {"error": "Server is busy."})
                finally:
                    connection.close()
        finally:
            slow_client.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_header_deadline_cannot_be_extended_by_slow_drip(self) -> None:
        handler = make_handler(
            FakeRegistrationService(),
            api_token=TEST_TOKEN,
            max_image_bytes=100,
        )
        handler.log_message = lambda self, format, *args: None
        server = BookRegistrationHttpServer(
            ("127.0.0.1", 0),
            handler,
            request_timeout_seconds=0.1,
            max_concurrent_requests=1,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        slow_client = socket.create_connection(server.server_address, timeout=1)
        slow_client.settimeout(1)
        try:
            disconnected = False
            for byte in b"GET /healthz HTTP/1.1\r\n":
                try:
                    slow_client.sendall(bytes((byte,)))
                except OSError:
                    disconnected = True
                    break
                time.sleep(0.03)

            if not disconnected:
                disconnected = slow_client.recv(1) == b""
            self.assertTrue(disconnected)

            connection = http.client.HTTPConnection(*server.server_address, timeout=1)
            try:
                connection.request("GET", "/healthz")
                self.assertEqual(connection.getresponse().status, HTTPStatus.OK)
            finally:
                connection.close()
        finally:
            slow_client.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_health_endpoint_does_not_require_authentication(self) -> None:
        status, response, headers = self._request(
            FakeRegistrationService(),
            "GET",
            "/healthz",
        )

        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(response, {"status": "ok"})
        self.assertEqual(headers["Connection"], "close")

    def test_head_responses_do_not_include_a_body(self) -> None:
        for path, expected_status in (
            ("/healthz", HTTPStatus.OK),
            ("/v1/books", HTTPStatus.METHOD_NOT_ALLOWED),
            ("/v2/books", HTTPStatus.METHOD_NOT_ALLOWED),
            ("/unknown", HTTPStatus.NOT_FOUND),
        ):
            with self.subTest(path=path):
                handler = make_handler(
                    FakeRegistrationService(), api_token=TEST_TOKEN, max_image_bytes=100
                )
                handler.log_message = lambda self, format, *args: None
                server = BookRegistrationHttpServer(("127.0.0.1", 0), handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                client = socket.create_connection(server.server_address, timeout=1)
                try:
                    request = f"HEAD {path} HTTP/1.1\r\nHost: {server.server_address[0]}\r\n\r\n"
                    client.sendall(request.encode("ascii"))
                    response = b""
                    while chunk := client.recv(4096):
                        response += chunk

                    self.assertIn(f" {expected_status.value} ".encode("ascii"), response)
                    self.assertIn(b"Connection: close", response)
                    self.assertTrue(response.endswith(b"\r\n\r\n"))
                finally:
                    client.close()
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)

    def _request(self, service, method, path, *, body=None, headers=None):
        handler = make_handler(service, api_token=TEST_TOKEN, max_image_bytes=100)
        handler.log_message = lambda self, format, *args: None
        server = BookRegistrationHttpServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection(*server.server_address, timeout=2)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            return response.status, payload, response.headers
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
