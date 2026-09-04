import json
import math
import unittest
from http.client import HTTPException, IncompleteRead
from urllib.error import HTTPError, URLError

from notion_book_register import (
    GlmVlmClient,
    IsbnExtractionResult,
    OpenAiVlmClient,
    VlmApiError,
    VlmProvider,
)
from notion_book_register.vlm_client import VlmInputError, vlm_from_env


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
    def __init__(self, error: Exception) -> None:
        self._error = error

    def __enter__(self) -> ErrorResponse:
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


class GlmVlmClientTest(unittest.TestCase):
    def test_extract_isbn13_builds_glm_vision_request(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return FakeResponse(
                json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": json.dumps(
                                        {
                                            "isbn13": "9784297135782",
                                            "candidates": ["9784297135782"],
                                            "confidence": "high",
                                            "evidence": "barcode",
                                        }
                                    )
                                }
                            }
                        ]
                    }
                ).encode("utf-8")
            )

        result = GlmVlmClient(
            " token ", model=" glm-4.6v-flash ", timeout=3, opener=opener
        ).extract_isbn13(b"image bytes", mime_type="image/jpeg")

        request, timeout = requests[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(result.isbn13, "9784297135782")
        self.assertEqual(timeout, 3)
        self.assertEqual(request.full_url, "https://api.z.ai/api/paas/v4/chat/completions")
        self.assertEqual(request.headers["Authorization"], "Bearer token")
        self.assertEqual(body["model"], "glm-4.6v-flash")
        self.assertEqual(
            body["messages"][1]["content"][1]["image_url"]["url"],
            "data:image/jpeg;base64,aW1hZ2UgYnl0ZXM=",
        )
        self.assertEqual(body["thinking"], {"type": "disabled"})

    def test_vlm_from_env_defaults_to_glm_and_allows_openai(self) -> None:
        self.assertIsInstance(vlm_from_env(environ={"GLM_API_KEY": "token"}), GlmVlmClient)
        self.assertIsInstance(
            vlm_from_env(environ={"VLM_PROVIDER": "openai", "OPENAI_API_KEY": "token"}),
            OpenAiVlmClient,
        )
        with self.assertRaisesRegex(ValueError, "VLM_PROVIDER"):
            vlm_from_env(environ={"VLM_PROVIDER": "other"})

    def test_vlm_from_env_uses_provider_specific_models_without_a_common_override(self) -> None:
        glm = vlm_from_env(environ={"GLM_API_KEY": "token", "GLM_VLM_MODEL": "glm-custom"})
        openai = vlm_from_env(
            environ={
                "VLM_PROVIDER": "openai",
                "OPENAI_API_KEY": "token",
                "OPENAI_VLM_MODEL": "openai-custom",
            }
        )

        self.assertEqual(glm._config.model, "glm-custom")
        self.assertEqual(openai._config.model, "openai-custom")

    def test_common_vlm_model_override_takes_precedence_for_both_providers(self) -> None:
        glm = vlm_from_env(environ={"GLM_API_KEY": "token", "VLM_MODEL": "common-model"})
        openai = vlm_from_env(
            environ={
                "VLM_PROVIDER": "openai",
                "OPENAI_API_KEY": "token",
                "VLM_MODEL": "common-model",
                "OPENAI_VLM_MODEL": "openai-model",
            }
        )

        self.assertEqual(glm._config.model, "common-model")
        self.assertEqual(openai._config.model, "common-model")

    def test_glm_rejects_unsupported_or_oversized_images_before_request(self) -> None:
        client = GlmVlmClient("token", opener=lambda request, timeout: self.fail("must not call"))
        with self.assertRaisesRegex(VlmInputError, "image/jpeg") as unsupported:
            client.extract_isbn13(b"image", mime_type="image/webp")
        self.assertEqual(unsupported.exception.status_code, 415)
        with self.assertRaisesRegex(VlmInputError, "5 MiB") as oversized:
            client.extract_isbn13(b"x" * (5 * 1024 * 1024), mime_type="image/jpeg")
        self.assertEqual(oversized.exception.status_code, 413)

    def test_glm_rejects_png_larger_than_dimension_limit(self) -> None:
        image = (
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + (6001).to_bytes(4, "big") + (1).to_bytes(4, "big")
        )
        client = GlmVlmClient("token", opener=lambda request, timeout: self.fail("must not call"))

        with self.assertRaisesRegex(VlmInputError, "6000 pixels") as error:
            client.extract_isbn13(image, mime_type="image/png")

        self.assertEqual(error.exception.status_code, 413)

    def test_glm_rejects_jpeg_larger_than_dimension_limit(self) -> None:
        image = (
            b"\xff\xd8\xff\xc0\x00\x08\x08"
            + (6001).to_bytes(2, "big")
            + (1).to_bytes(2, "big")
            + b"\x01"
        )
        client = GlmVlmClient("token", opener=lambda request, timeout: self.fail("must not call"))

        with self.assertRaisesRegex(VlmInputError, "6000 pixels") as error:
            client.extract_isbn13(image, mime_type="image/jpeg")

        self.assertEqual(error.exception.status_code, 413)


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
        with self.assertRaisesRegex(ValueError, "image/png"):
            client.extract_isbn13(b"image", mime_type="application/pdf")
        with self.assertRaisesRegex(ValueError, "image/png"):
            client.extract_isbn13(b"image", mime_type="image/svg+xml")

    def test_extract_isbn13_wraps_invalid_extraction_output(self) -> None:
        client = OpenAiVlmClient(
            "token",
            opener=lambda request, timeout: FakeResponse(b'{"output_text": "not json"}'),
        )

        with self.assertRaisesRegex(VlmApiError, "invalid ISBN extraction output") as context:
            client.extract_isbn13(b"image", mime_type="image/jpeg")

        self.assertIsNotNone(context.exception.__cause__)

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
