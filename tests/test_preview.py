import json
import unittest
from http import HTTPStatus
from unittest.mock import patch

import test_api as api_tests
from test_api import TEST_TOKEN
from test_registration import FakeNdl, FakeNotion, FakeVlm

from notion_book_register.registration import BookRegistrationService, InvalidPreviewError
from notion_book_register.vlm_prompt import IsbnExtractionResult


class PreviewTest(unittest.TestCase):
    def service(self, *, created=True):
        notion = FakeNotion(created=created)
        notion.data_source_id = "11111111111111111111111111111111"
        return BookRegistrationService(FakeVlm(), FakeNdl(), notion), notion

    def test_preview_never_writes_and_commit_consumes_once(self):
        service, notion = self.service(created=False)
        preview = service.preview_image(b"image", mime_type="image/png")
        self.assertEqual(notion.calls, [])
        self.assertEqual(preview["data_source_id"], notion.data_source_id)
        with self.assertRaises(InvalidPreviewError):
            service.commit_preview(preview["preview_token"], "9784101010014")
        result = service.commit_preview(preview["preview_token"], "9784297135782")
        self.assertFalse(result.page.created)
        with self.assertRaises(InvalidPreviewError):
            service.commit_preview(preview["preview_token"], "9784297135782")
        self.assertEqual(len(notion.calls), 1)

    def test_expiry_and_upstream_failure_do_not_allow_replay(self):
        service, notion = self.service()
        with patch("notion_book_register.registration.monotonic", return_value=10):
            preview = service.preview_image(b"image", mime_type="image/png")
        with patch("notion_book_register.registration.monotonic", return_value=311):
            with self.assertRaises(InvalidPreviewError):
                service.commit_preview(preview["preview_token"], "9784297135782")
        self.assertEqual(notion.calls, [])
        preview = service.preview_image(b"image", mime_type="image/png")
        with patch.object(notion, "create_book_page", side_effect=RuntimeError("unknown")) as write:
            with self.assertRaises(RuntimeError):
                service.commit_preview(preview["preview_token"], "9784297135782")
            with self.assertRaises(InvalidPreviewError):
                service.commit_preview(preview["preview_token"], "9784297135782")
            self.assertEqual(write.call_count, 1)

    def test_ambiguous_low_confidence_candidates_are_kept_for_selection(self):
        service, notion = self.service()
        original = service._ndl.search_by_isbn

        def lookup(isbn):
            response = original(isbn)
            from dataclasses import replace

            return replace(
                response,
                records_xml=tuple(
                    xml.replace("9784297135782", isbn) for xml in response.records_xml
                ),
            )

        with (
            patch.object(
                service._vlm,
                "extract_isbn13",
                return_value=IsbnExtractionResult(
                    None, ("9784297135782", "9784101010014"), "low", "two labels"
                ),
            ),
            patch.object(service._ndl, "search_by_isbn", side_effect=lookup),
        ):
            preview = service.preview_image(b"image", mime_type="image/png")
        self.assertEqual(len(preview["candidates"]), 2)
        self.assertEqual(preview["confidence"], "low")
        result = service.commit_preview(preview["preview_token"], "9784101010014")
        self.assertEqual(result.book.isbn13, "9784101010014")
        self.assertEqual(len(notion.calls), 1)

    def test_ndl_mismatched_isbn_is_not_offered(self):
        from notion_book_register.registration import BookNotFoundError

        service, notion = self.service()
        with patch.object(
            service._vlm,
            "extract_isbn13",
            return_value=IsbnExtractionResult("9784101010014", (), "high", "label"),
        ):
            with self.assertRaises(BookNotFoundError):
                service.preview_image(b"image", mime_type="image/png")
        self.assertEqual(notion.calls, [])

    def test_http_auth_preview_commit_and_validation(self):
        service, notion = self.service()
        request = api_tests.BookRegistrationEndpointTest()._request
        headers = {"Authorization": f"Bearer {TEST_TOKEN}", "Content-Type": "application/json"}
        status, _, _ = request(service, "POST", "/v2/books/preview", body=b"{}", headers={})
        self.assertEqual(status, HTTPStatus.UNAUTHORIZED)
        status, response, _ = request(
            service,
            "POST",
            "/v2/books/preview",
            body=b'{"image":"aW1hZ2U=","mime_type":"image/png"}',
            headers=headers,
        )
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(notion.calls, [])
        body = json.dumps(
            {"preview_token": response["result"]["preview_token"], "isbn13": "9784297135782"}
        ).encode()
        status, result, _ = request(service, "POST", "/v2/books/commit", body=body, headers=headers)
        self.assertEqual(status, HTTPStatus.CREATED)
        self.assertTrue(result["ok"])
        status, _, _ = request(service, "POST", "/v2/books/commit", body=body, headers=headers)
        self.assertEqual(status, HTTPStatus.CONFLICT)
        status, _, _ = request(service, "POST", "/v2/books/commit", body=b"[]", headers=headers)
        self.assertEqual(status, HTTPStatus.BAD_REQUEST)
