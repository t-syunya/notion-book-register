import json
import unittest

from notion_book_register import (
    IsbnExtractionResult,
    VlmPromptError,
    build_isbn_extraction_messages,
    parse_isbn_extraction_response,
)
from notion_book_register.vlm_prompt import ISBN_EXTRACTION_RESPONSE_SCHEMA


class VlmPromptTest(unittest.TestCase):
    def test_build_isbn_extraction_messages_describes_json_contract(self) -> None:
        messages = build_isbn_extraction_messages()

        self.assertEqual([message["role"] for message in messages], ["system", "user"])
        joined = "\n".join(message["content"] for message in messages)
        self.assertIn("ISBN-13", joined)
        self.assertIn('"isbn13"', joined)
        self.assertIn("null", joined)
        self.assertIn("Markdown", joined)
        self.assertIn("バーコード", joined)

    def test_response_schema_requires_isbn13_and_candidates(self) -> None:
        self.assertEqual(ISBN_EXTRACTION_RESPONSE_SCHEMA["type"], "object")
        self.assertEqual(ISBN_EXTRACTION_RESPONSE_SCHEMA["additionalProperties"], False)
        self.assertEqual(
            ISBN_EXTRACTION_RESPONSE_SCHEMA["required"],
            ["isbn13", "candidates", "confidence", "evidence"],
        )

    def test_parse_isbn_extraction_response_normalizes_valid_result(self) -> None:
        result = parse_isbn_extraction_response(
            json.dumps(
                {
                    "isbn13": "ISBN 978-4-297-13578-2",
                    "candidates": [
                        "ISBN 978\t4\n297 13578 2",
                        "978-4-297-13578-2 税込 2,860円",
                        "4006381333931",
                    ],
                    "confidence": "high",
                    "evidence": "裏表紙のバーコード下に印字",
                }
            )
        )

        self.assertEqual(
            result,
            IsbnExtractionResult(
                isbn13="9784297135782",
                candidates=("9784297135782",),
                confidence="high",
                evidence="裏表紙のバーコード下に印字",
            ),
        )

    def test_parse_isbn_extraction_response_allows_null_when_not_found(self) -> None:
        result = parse_isbn_extraction_response(
            json.dumps(
                {
                    "isbn13": None,
                    "candidates": [],
                    "confidence": "low",
                    "evidence": "",
                }
            )
        )

        self.assertIsNone(result.isbn13)
        self.assertEqual(result.candidates, ())

    def test_parse_isbn_extraction_response_rejects_invalid_primary_isbn(self) -> None:
        for isbn13 in (
            "9784297135780",
            "97842971357820",
            "1239784297135782",
            "9784297135782-0",
            "9784297135782 0",
            "0 9784297135782",
        ):
            with self.subTest(isbn13=isbn13):
                with self.assertRaisesRegex(VlmPromptError, "invalid isbn13"):
                    parse_isbn_extraction_response(
                        json.dumps(
                            {
                                "isbn13": isbn13,
                                "candidates": [],
                                "confidence": "high",
                                "evidence": "text",
                            }
                        )
                    )

    def test_parse_isbn_extraction_response_ignores_embedded_isbn_candidates(self) -> None:
        result = parse_isbn_extraction_response(
            json.dumps(
                {
                    "isbn13": None,
                    "candidates": [
                        "97842971357820",
                        "1239784297135782",
                        "9784297135782-0",
                        "9784297135782 0",
                        "0 9784297135782",
                        "ISBN 9784297135782",
                    ],
                    "confidence": "medium",
                    "evidence": "OCR candidates",
                }
            )
        )

        self.assertEqual(
            result,
            IsbnExtractionResult(
                isbn13=None,
                candidates=("9784297135782",),
                confidence="medium",
                evidence="OCR candidates",
            ),
        )

    def test_parse_isbn_extraction_response_rejects_non_json_object(self) -> None:
        for payload in ("not json", "[]"):
            with self.subTest(payload=payload):
                with self.assertRaises(VlmPromptError):
                    parse_isbn_extraction_response(payload)

    def test_parse_isbn_extraction_response_rejects_invalid_field_types(self) -> None:
        invalid_cases = (
            (
                {
                    "isbn13": None,
                    "candidates": "9784297135782",
                    "confidence": "low",
                    "evidence": "",
                },
                "candidates must be an array",
            ),
            (
                {
                    "isbn13": None,
                    "candidates": [9784297135782],
                    "confidence": "low",
                    "evidence": "",
                },
                "candidates must contain only strings",
            ),
            (
                {
                    "isbn13": None,
                    "candidates": [],
                    "confidence": "certain",
                    "evidence": "",
                },
                "confidence must be high, medium, or low",
            ),
        )

        for payload, message in invalid_cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(VlmPromptError, message):
                    parse_isbn_extraction_response(json.dumps(payload))

    def test_parse_isbn_extraction_response_rejects_missing_or_extra_keys(self) -> None:
        with self.assertRaisesRegex(VlmPromptError, "missing required field"):
            parse_isbn_extraction_response(
                json.dumps(
                    {
                        "candidates": [],
                        "confidence": "low",
                        "evidence": "",
                    }
                )
            )

        with self.assertRaisesRegex(VlmPromptError, "unexpected field"):
            parse_isbn_extraction_response(
                json.dumps(
                    {
                        "isbn13": None,
                        "candidates": [],
                        "confidence": "low",
                        "evidence": "",
                        "raw_text": "ISBN 9784297135782",
                    }
                )
            )


if __name__ == "__main__":
    unittest.main()
