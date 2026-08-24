import json
import math
import unittest
from http.client import HTTPException, IncompleteRead
from urllib.error import HTTPError, URLError

from notion_book_register import (
    IsbnExtractionResult,
    OpenAiVlmClient,
    VlmApiError,
    VlmProvider,
)


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


class OpenAiVlmClientTest(unittest.TestCase):
    def test_openai_client_satisfies_vlm_provider_protocol(self) -> None:
        provider: VlmProvider = OpenAiVlmClient(
            "token",
            opener=lambda request, timeout: FakeResponse(
                json.dumps(
                    {
                        "output_text": json.dumps(
                            {
                                "isbn13": None,
                                "candidates": [],
                                "confidence": "low",
                                "evidence": "",
                            }
                        )
                    }
                ).encode("utf-8")
            ),
        )

        result = provider.extract_isbn13(b"image bytes", mime_type="image/jpeg")

        self.assertIsNone(result.isbn13)

    def test_extract_isbn13_builds_openai_request_and_parses_output_text(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return FakeResponse(
                json.dumps(
                    {
                        "output_text": json.dumps(
                            {
                                "isbn13": "978-4-297-13578-2",
                                "candidates": ["9784297135782"],
                                "confidence": "high",
                                "evidence": "barcode",
                            }
                        )
                    }
                ).encode("utf-8")
            )

        client = OpenAiVlmClient(
            " secret_token ",
            model=" gpt-test ",
            timeout=3.0,
            image_detail=" high ",
            opener=opener,
        )

        result = client.extract_isbn13(b"image bytes", mime_type=" IMAGE/JPEG ")

        self.assertEqual(
            result,
            IsbnExtractionResult(
                isbn13="9784297135782",
                candidates=("9784297135782",),
                confidence="high",
                evidence="barcode",
            ),
        )

        request, timeout = requests[0]
        self.assertEqual(timeout, 3.0)
        self.assertEqual(request.full_url, "https://api.openai.com/v1/responses")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.headers["Authorization"], "Bearer secret_token")
        self.assertEqual(request.headers["Content-type"], "application/json")
        self.assertEqual(request.headers["User-agent"], "notion-book-register/0.1")

        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["model"], "gpt-test")
        self.assertFalse(body["store"])
        self.assertEqual(body["text"]["format"]["type"], "json_schema")
        self.assertEqual(body["text"]["format"]["name"], "isbn_extraction")
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertEqual(body["input"][0]["role"], "system")
        self.assertEqual(body["input"][1]["role"], "user")
        user_content = body["input"][1]["content"]
        self.assertEqual(user_content[0]["type"], "input_text")
        self.assertEqual(user_content[1]["type"], "input_image")
        self.assertEqual(user_content[1]["detail"], "high")
        self.assertEqual(user_content[1]["image_url"], "data:image/jpeg;base64,aW1hZ2UgYnl0ZXM=")

    def test_extract_isbn13_parses_nested_response_output_text(self) -> None:
        def opener(request, timeout):
            return FakeResponse(
                json.dumps(
                    {
                        "output": [
                            {"type": "reasoning", "summary": []},
                            {
                                "type": "message",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": json.dumps(
                                            {
                                                "isbn13": None,
                                                "candidates": [],
                                                "confidence": "low",
                                                "evidence": "",
                                            }
                                        ),
                                    }
                                ],
                            },
                        ]
                    }
                ).encode("utf-8")
            )

        client = OpenAiVlmClient("secret_token", opener=opener)

        result = client.extract_isbn13(b"image bytes", mime_type="image/png")

        self.assertIsNone(result.isbn13)
        self.assertEqual(result.candidates, ())

    def test_from_env_uses_openai_environment_values(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return FakeResponse(
                json.dumps(
                    {
                        "output_text": json.dumps(
                            {
                                "isbn13": None,
                                "candidates": [],
                                "confidence": "low",
                                "evidence": "",
                            }
                        )
                    }
                ).encode("utf-8")
            )

        client = OpenAiVlmClient.from_env(
            environ={
                "OPENAI_API_KEY": "env-token",
                "OPENAI_VLM_MODEL": "env-model",
            },
            opener=opener,
        )

        client.extract_isbn13(b"image bytes", mime_type="image/webp")

        request, _timeout = requests[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.headers["Authorization"], "Bearer env-token")
        self.assertEqual(body["model"], "env-model")

    def test_from_env_requires_api_key(self) -> None:
        with self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
            OpenAiVlmClient.from_env(environ={})

    def test_constructor_rejects_invalid_configuration(self) -> None:
        invalid_cases = (
            {"api_key": " ", "error": "API key"},
            {"api_key": "token", "model": " ", "error": "model"},
            {"api_key": "token", "timeout": 0, "error": "timeout"},
            {"api_key": "token", "timeout": math.inf, "error": "timeout"},
            {"api_key": "token", "image_detail": "full", "error": "image_detail"},
        )

        for case in invalid_cases:
            kwargs = {key: value for key, value in case.items() if key != "error"}
            with self.subTest(error=case["error"]):
                with self.assertRaisesRegex(ValueError, str(case["error"])):
                    OpenAiVlmClient(**kwargs)

    def test_extract_isbn13_rejects_invalid_image_inputs(self) -> None:
        client = OpenAiVlmClient("token", opener=lambda request, timeout: FakeResponse(b"{}"))

        with self.assertRaisesRegex(ValueError, "image"):
            client.extract_isbn13(b"", mime_type="image/jpeg")
        with self.assertRaisesRegex(ValueError, "mime_type"):
            client.extract_isbn13(b"image", mime_type="")
        with self.assertRaisesRegex(ValueError, "image MIME"):
            client.extract_isbn13(b"image", mime_type="application/pdf")

    def test_extract_isbn13_rejects_missing_response_text(self) -> None:
        client = OpenAiVlmClient("token", opener=lambda request, timeout: FakeResponse(b"{}"))

        with self.assertRaisesRegex(VlmApiError, "missing output text"):
            client.extract_isbn13(b"image", mime_type="image/jpeg")

    def test_extract_isbn13_rejects_failed_response(self) -> None:
        client = OpenAiVlmClient(
            "token",
            opener=lambda request, timeout: FakeResponse(
                b'{"status": "failed", "error": {"message": "model failed"}}'
            ),
        )

        with self.assertRaisesRegex(VlmApiError, "model failed"):
            client.extract_isbn13(b"image", mime_type="image/jpeg")

    def test_extract_isbn13_rejects_incomplete_response(self) -> None:
        client = OpenAiVlmClient(
            "token",
            opener=lambda request, timeout: FakeResponse(
                b'{"status": "incomplete", "incomplete_details": {"reason": "max tokens"}}'
            ),
        )

        with self.assertRaisesRegex(VlmApiError, "max tokens"):
            client.extract_isbn13(b"image", mime_type="image/jpeg")

    def test_extract_isbn13_wraps_http_error_detail_and_closes_response(self) -> None:
        response_body = CloseTrackingBody(b'{"error": {"message": "model is not available"}}')

        def opener(request, timeout):
            raise HTTPError(request.full_url, 404, "Not Found", {}, response_body)

        client = OpenAiVlmClient("token", opener=opener)

        with self.assertRaisesRegex(VlmApiError, "model is not available"):
            client.extract_isbn13(b"image", mime_type="image/jpeg")

        self.assertTrue(response_body.closed)

    def test_extract_isbn13_wraps_connection_and_read_errors(self) -> None:
        errors = (
            (URLError("timeout"), "timeout"),
            (TimeoutError(), "Timed out"),
            (OSError("closed"), "closed"),
            (HTTPException("bad status"), "bad status"),
            (IncompleteRead(b""), "IncompleteRead"),
        )

        for error, message in errors:
            client = OpenAiVlmClient(
                "token",
                opener=lambda request, timeout, error=error: ErrorResponse(error),
            )
            with self.subTest(message=message):
                with self.assertRaisesRegex(VlmApiError, message):
                    client.extract_isbn13(b"image", mime_type="image/jpeg")


if __name__ == "__main__":
    unittest.main()
