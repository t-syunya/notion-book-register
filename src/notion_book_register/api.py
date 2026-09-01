"""Standard-library HTTP API for image-based book registration."""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import math
import os
import re
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
from threading import BoundedSemaphore, Timer
from typing import Any
from urllib.parse import urlsplit

from notion_book_register.ndl_client import NdlApiError
from notion_book_register.notion_client import NotionApiError
from notion_book_register.registration import (
    BookNotFoundError,
    BookRegistrationService,
    IsbnNotDetectedError,
    RegisteredBook,
)
from notion_book_register.vlm_client import VlmApiError

REGISTER_BOOK_PATH = "/v1/books"
HEALTH_PATH = "/healthz"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_MAX_IMAGE_BYTES = 10 * 1024 * 1024
DEFAULT_REQUEST_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_CONCURRENT_REQUESTS = 16
_SUPPORTED_IMAGE_MIME_TYPES = {"image/gif", "image/jpeg", "image/png", "image/webp"}
_BEARER_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9._~+/=-]{32,512}\Z")


@dataclass(frozen=True, slots=True)
class ApiConfig:
    """Validated HTTP server configuration."""

    api_token: str
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS
    max_concurrent_requests: int = DEFAULT_MAX_CONCURRENT_REQUESTS

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> ApiConfig:
        """Load API server configuration from environment variables."""

        api_token = environ.get("BOOK_REGISTER_API_TOKEN", "").strip()
        host = environ.get("BOOK_REGISTER_HOST", DEFAULT_HOST).strip()
        port = _parse_positive_int(environ.get("BOOK_REGISTER_PORT"), DEFAULT_PORT, "port")
        max_image_bytes = _parse_positive_int(
            environ.get("BOOK_REGISTER_MAX_IMAGE_BYTES"),
            DEFAULT_MAX_IMAGE_BYTES,
            "max image bytes",
        )
        request_timeout_seconds = _parse_positive_float(
            environ.get("BOOK_REGISTER_REQUEST_TIMEOUT_SECONDS"),
            DEFAULT_REQUEST_TIMEOUT_SECONDS,
            "request timeout seconds",
        )
        max_concurrent_requests = _parse_positive_int(
            environ.get("BOOK_REGISTER_MAX_CONCURRENT_REQUESTS"),
            DEFAULT_MAX_CONCURRENT_REQUESTS,
            "max concurrent requests",
        )
        _validate_api_token(api_token, label="BOOK_REGISTER_API_TOKEN")
        if not host:
            raise ValueError("BOOK_REGISTER_HOST must not be empty.")
        if port > 65535:
            raise ValueError("BOOK_REGISTER_PORT must be between 1 and 65535.")
        return cls(
            api_token=api_token,
            host=host,
            port=port,
            max_image_bytes=max_image_bytes,
            request_timeout_seconds=request_timeout_seconds,
            max_concurrent_requests=max_concurrent_requests,
        )


@dataclass(frozen=True, slots=True)
class RegistrationRequest:
    """Validated request data for one image registration."""

    image: bytes
    mime_type: str
    title: str | None = None
    author: str | None = None
    genre: str | None = None


class ApiRequestError(ValueError):
    """Raised for a client request that cannot be accepted."""

    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


class BookRegistrationHttpServer(ThreadingHTTPServer):
    """HTTP server with bounded concurrency and request read timeouts."""

    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler_class: type[BaseHTTPRequestHandler],
        *,
        request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        max_concurrent_requests: int = DEFAULT_MAX_CONCURRENT_REQUESTS,
    ) -> None:
        if not math.isfinite(request_timeout_seconds) or request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be a positive finite value.")
        if max_concurrent_requests < 1:
            raise ValueError("max_concurrent_requests must be greater than or equal to 1.")
        self.request_timeout_seconds = request_timeout_seconds
        self._request_slots = BoundedSemaphore(max_concurrent_requests)
        super().__init__(server_address, request_handler_class)

    def server_bind(self) -> None:
        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._request_slots.acquire(blocking=False):
            self._reject_busy_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()

    def _reject_busy_request(self, request: Any) -> None:
        body = b'{"error":"Server is busy."}'
        response = (
            b"HTTP/1.1 503 Service Unavailable\r\n"
            b"Content-Type: application/json; charset=utf-8\r\n"
            + f"Content-Length: {len(body)}\r\n".encode("ascii")
            + b"Cache-Control: no-store\r\nConnection: close\r\n\r\n"
            + body
        )
        try:
            request.sendall(response)
        except OSError:
            pass
        finally:
            self.shutdown_request(request)


def parse_registration_request(payload: bytes, *, max_image_bytes: int) -> RegistrationRequest:
    """Parse and validate the JSON request body used by the HTTP endpoint."""

    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ApiRequestError(HTTPStatus.BAD_REQUEST, "Request body must be valid JSON.") from error
    if not isinstance(data, dict):
        raise ApiRequestError(HTTPStatus.BAD_REQUEST, "Request body must be a JSON object.")

    encoded_image = data.get("image")
    if not isinstance(encoded_image, str) or not encoded_image:
        raise ApiRequestError(HTTPStatus.BAD_REQUEST, "image must be a non-empty base64 string.")
    try:
        image = base64.b64decode(encoded_image, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ApiRequestError(HTTPStatus.BAD_REQUEST, "image must be valid base64.") from error
    if not image:
        raise ApiRequestError(HTTPStatus.BAD_REQUEST, "image must not be empty.")
    if len(image) > max_image_bytes:
        raise ApiRequestError(HTTPStatus.CONTENT_TOO_LARGE, "Decoded image is too large.")

    mime_type = _required_string(data, "mime_type").lower()
    if mime_type not in _SUPPORTED_IMAGE_MIME_TYPES:
        supported = ", ".join(sorted(_SUPPORTED_IMAGE_MIME_TYPES))
        raise ApiRequestError(
            HTTPStatus.BAD_REQUEST,
            f"mime_type must be one of: {supported}.",
        )
    title = _optional_string(data, "title")
    author = _optional_string(data, "author")
    genre = _optional_string(data, "genre")
    if (title is None) != (author is None):
        raise ApiRequestError(
            HTTPStatus.BAD_REQUEST,
            "title and author must be provided together.",
        )
    return RegistrationRequest(
        image=image,
        mime_type=mime_type,
        title=title,
        author=author,
        genre=genre,
    )


def make_handler(
    service: BookRegistrationService,
    *,
    api_token: str,
    max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
) -> type[BaseHTTPRequestHandler]:
    """Create a request handler bound to the supplied service and configuration."""

    _validate_api_token(api_token, label="api_token")
    if max_image_bytes < 1:
        raise ValueError("max_image_bytes must be greater than or equal to 1.")
    max_request_bytes = ((max_image_bytes + 2) // 3 * 4) + 64 * 1024

    class BookRegistrationHandler(BaseHTTPRequestHandler):
        server_version = "notion-book-register/0.1"
        protocol_version = "HTTP/1.1"

        def setup(self) -> None:
            super().setup()
            self._request_timeout = getattr(
                self.server,
                "request_timeout_seconds",
                DEFAULT_REQUEST_TIMEOUT_SECONDS,
            )
            self._deadline_timer: Timer | None = None
            self._deadline_expired = False
            self.connection.settimeout(self._request_timeout)
            self._start_read_deadline()

        def finish(self) -> None:
            self._cancel_read_deadline()
            try:
                super().finish()
            except BrokenPipeError, ConnectionResetError:
                return

        def handle(self) -> None:
            try:
                super().handle()
            except BrokenPipeError, ConnectionResetError:
                return

        def do_GET(self) -> None:
            self._cancel_read_deadline()
            if urlsplit(self.path).path != HEALTH_PATH:
                self._write_json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
                return
            self._write_json(HTTPStatus.OK, {"status": "ok"})

        def do_POST(self) -> None:
            self._cancel_read_deadline()
            if urlsplit(self.path).path != REGISTER_BOOK_PATH:
                self._write_json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
                return
            if not self._is_authorized(api_token):
                self._write_json(
                    HTTPStatus.UNAUTHORIZED,
                    {"error": "Authorization bearer token is invalid."},
                    extra_headers={"WWW-Authenticate": "Bearer"},
                )
                return

            try:
                self._start_read_deadline()
                try:
                    payload = self._read_json_body(max_request_bytes=max_request_bytes)
                finally:
                    self._cancel_read_deadline()
                request = parse_registration_request(payload, max_image_bytes=max_image_bytes)
                result = service.register_image(
                    request.image,
                    mime_type=request.mime_type,
                    title=request.title,
                    author=request.author,
                    genre=request.genre,
                )
            except ApiRequestError as error:
                self._write_json(error.status, {"error": str(error)})
                return
            except (IsbnNotDetectedError, BookNotFoundError) as error:
                self._write_json(HTTPStatus.UNPROCESSABLE_CONTENT, {"error": str(error)})
                return
            except VlmApiError, NdlApiError, NotionApiError:
                self._write_json(
                    HTTPStatus.BAD_GATEWAY,
                    {"error": "An upstream service failed."},
                )
                return
            except Exception:
                self.log_error("Unhandled error while registering a book.")
                self._write_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "An internal server error occurred."},
                )
                return

            self._write_json(
                HTTPStatus.CREATED if result.page.created else HTTPStatus.OK,
                _registration_payload(result),
            )

        def _read_json_body(self, *, max_request_bytes: int) -> bytes:
            content_type = self.headers.get("Content-Type", "").partition(";")[0].strip().lower()
            if content_type != "application/json":
                raise ApiRequestError(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    "Content-Type must be application/json.",
                )

            content_length_text = self.headers.get("Content-Length")
            if content_length_text is None:
                raise ApiRequestError(HTTPStatus.LENGTH_REQUIRED, "Content-Length is required.")
            try:
                content_length = int(content_length_text)
            except ValueError as error:
                raise ApiRequestError(
                    HTTPStatus.BAD_REQUEST,
                    "Content-Length must be an integer.",
                ) from error
            if content_length < 1:
                raise ApiRequestError(HTTPStatus.BAD_REQUEST, "Request body must not be empty.")
            if content_length > max_request_bytes:
                raise ApiRequestError(HTTPStatus.CONTENT_TOO_LARGE, "Request body is too large.")

            try:
                payload = self.rfile.read(content_length)
            except TimeoutError as error:
                raise ApiRequestError(
                    HTTPStatus.REQUEST_TIMEOUT,
                    "Timed out while reading the request body.",
                ) from error
            if self._deadline_expired:
                raise ApiRequestError(
                    HTTPStatus.REQUEST_TIMEOUT,
                    "Timed out while reading the request body.",
                )
            if len(payload) != content_length:
                raise ApiRequestError(HTTPStatus.BAD_REQUEST, "Request body is incomplete.")
            return payload

        def _is_authorized(self, expected_token: str) -> bool:
            authorization = self.headers.get("Authorization", "")
            scheme, separator, token = authorization.partition(" ")
            return bool(
                separator
                and scheme.casefold() == "bearer"
                and token
                and _BEARER_TOKEN_PATTERN.fullmatch(token) is not None
                and hmac.compare_digest(token, expected_token)
            )

        def _start_read_deadline(self) -> None:
            self._cancel_read_deadline()
            self._deadline_expired = False
            self._deadline_timer = Timer(self._request_timeout, self._expire_connection)
            self._deadline_timer.daemon = True
            self._deadline_timer.start()

        def _cancel_read_deadline(self) -> None:
            timer = getattr(self, "_deadline_timer", None)
            if timer is not None:
                timer.cancel()
                self._deadline_timer = None

        def _expire_connection(self) -> None:
            self._deadline_expired = True
            try:
                self.connection.shutdown(socket.SHUT_RD)
            except OSError:
                pass

        def _write_json(
            self,
            status: HTTPStatus,
            payload: dict[str, Any],
            *,
            extra_headers: Mapping[str, str] | None = None,
        ) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            try:
                self.close_connection = True
                self.send_response(status.value)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                for name, value in (extra_headers or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                return

    return BookRegistrationHandler


def serve(
    service: BookRegistrationService,
    *,
    config: ApiConfig,
) -> None:
    """Run the book registration API until interrupted."""

    handler = make_handler(
        service,
        api_token=config.api_token,
        max_image_bytes=config.max_image_bytes,
    )
    with BookRegistrationHttpServer(
        (config.host, config.port),
        handler,
        request_timeout_seconds=config.request_timeout_seconds,
        max_concurrent_requests=config.max_concurrent_requests,
    ) as server:
        server.serve_forever()


def main() -> None:
    """Run the HTTP API using environment-based production clients."""

    serve(BookRegistrationService.from_env(), config=ApiConfig.from_env())


def _registration_payload(result: RegisteredBook) -> dict[str, Any]:
    return {
        "isbn13": result.book.isbn13,
        "title": result.book.title,
        "page_id": result.page.page_id,
        "page_url": result.page.url,
        "created": result.page.created,
    }


def _required_string(data: dict[str, Any], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ApiRequestError(HTTPStatus.BAD_REQUEST, f"{field} must be a non-empty string.")
    return value.strip()


def _optional_string(data: dict[str, Any], field: str) -> str | None:
    value = data.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApiRequestError(HTTPStatus.BAD_REQUEST, f"{field} must be a string.")
    return value.strip() or None


def _parse_positive_int(value: str | None, default: int, label: str) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"{label} must be an integer.") from error
    if parsed < 1:
        raise ValueError(f"{label} must be greater than or equal to 1.")
    return parsed


def _parse_positive_float(value: str | None, default: float, label: str) -> float:
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError as error:
        raise ValueError(f"{label} must be a number.") from error
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"{label} must be a positive finite value.")
    return parsed


def _validate_api_token(value: str, *, label: str) -> None:
    if not value:
        raise ValueError(f"{label} is required.")
    if _BEARER_TOKEN_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} must be 32-512 ASCII bearer-token characters without spaces.")


if __name__ == "__main__":
    main()
