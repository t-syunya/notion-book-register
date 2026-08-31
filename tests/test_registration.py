import threading
import unittest

from notion_book_register import (
    BookNotFoundError,
    BookRegistrationService,
    CreatedNotionPage,
    IsbnExtractionResult,
    IsbnNotDetectedError,
    NdlSruResponse,
)

BOOK_RECORD = """<dcndl:BibResource
  xmlns:dcndl="http://ndl.go.jp/dcndl/terms/"
  xmlns:dcterms="http://purl.org/dc/terms/"
  xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
  rdf:about="https://ndlsearch.ndl.go.jp/books/test">
  <dcterms:title>Python Testing</dcterms:title>
  <dcterms:identifier rdf:datatype="http://ndl.go.jp/dcndl/terms/ISBN">
    9784297135782
  </dcterms:identifier>
</dcndl:BibResource>"""


class FakeVlm:
    def __init__(self, isbn13="9784297135782") -> None:
        self.isbn13 = isbn13
        self.calls = []

    def extract_isbn13(self, image, *, mime_type):
        self.calls.append((image, mime_type))
        return IsbnExtractionResult(
            isbn13=self.isbn13,
            candidates=() if self.isbn13 is None else (self.isbn13,),
            confidence="high" if self.isbn13 else "low",
            evidence="barcode" if self.isbn13 else "",
        )


class FakeNdl:
    def __init__(self, response=None) -> None:
        self.response = response or NdlSruResponse(1, (BOOK_RECORD,))
        self.calls = []

    def search_by_isbn(self, isbn13, *, maximum_records=1):
        self.calls.append(("isbn", isbn13, maximum_records))
        return self.response

    def search_by_isbn_with_fallback(
        self,
        isbn13,
        *,
        title,
        author,
        maximum_records=1,
    ):
        self.calls.append(("fallback", isbn13, title, author, maximum_records))
        return self.response


class FakeNotion:
    def __init__(self, *, created=True) -> None:
        self.created = created
        self.calls = []

    def create_book_page(
        self,
        book,
        *,
        genre=None,
        status="未読",
        prevent_duplicates=True,
    ):
        self.calls.append((book, genre, status, prevent_duplicates))
        return CreatedNotionPage(
            page_id="page-id",
            url="https://www.notion.so/page-id",
            created=self.created,
        )


class ConcurrencyTrackingNotion(FakeNotion):
    def __init__(self) -> None:
        super().__init__()
        self._state_lock = threading.Lock()
        self.active_calls = 0
        self.max_active_calls = 0
        self.first_call_started = threading.Event()
        self.release_first_call = threading.Event()

    def create_book_page(self, book, **kwargs):
        with self._state_lock:
            self.active_calls += 1
            self.max_active_calls = max(self.max_active_calls, self.active_calls)
        try:
            if len(self.calls) == 0:
                self.first_call_started.set()
                self.release_first_call.wait(timeout=1)
            return super().create_book_page(book, **kwargs)
        finally:
            with self._state_lock:
                self.active_calls -= 1


class AcquisitionTrackingLock:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._attempts = 0
        self.second_attempt_started = threading.Event()

    def __enter__(self):
        with self._state_lock:
            self._attempts += 1
            if self._attempts == 2:
                self.second_attempt_started.set()
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._lock.release()


class BookRegistrationServiceTest(unittest.TestCase):
    def test_register_image_runs_complete_registration_pipeline(self) -> None:
        vlm = FakeVlm()
        ndl = FakeNdl()
        notion = FakeNotion()
        service = BookRegistrationService(vlm, ndl, notion)

        result = service.register_image(
            b"image bytes",
            mime_type="image/jpeg",
            genre=" 技術書 ",
        )

        self.assertEqual(vlm.calls, [(b"image bytes", "image/jpeg")])
        self.assertEqual(ndl.calls, [("isbn", "9784297135782", 1)])
        self.assertEqual(result.book.title, "Python Testing")
        self.assertEqual(result.page.page_id, "page-id")
        self.assertEqual(notion.calls[0][1], "技術書")

    def test_register_image_uses_title_author_fallback_when_both_are_present(self) -> None:
        ndl = FakeNdl()
        service = BookRegistrationService(FakeVlm(), ndl, FakeNotion())

        service.register_image(
            b"image bytes",
            mime_type="image/png",
            title="  Python   Testing ",
            author=" Author A ",
        )

        self.assertEqual(
            ndl.calls,
            [("fallback", "9784297135782", "Python Testing", "Author A", 1)],
        )

    def test_register_image_requires_title_and_author_together_before_vlm_call(self) -> None:
        vlm = FakeVlm()
        service = BookRegistrationService(vlm, FakeNdl(), FakeNotion())

        with self.assertRaisesRegex(ValueError, "provided together"):
            service.register_image(
                b"image bytes",
                mime_type="image/jpeg",
                title="Python Testing",
            )

        self.assertEqual(vlm.calls, [])

    def test_register_image_raises_when_vlm_does_not_detect_isbn(self) -> None:
        ndl = FakeNdl()
        notion = FakeNotion()
        service = BookRegistrationService(FakeVlm(isbn13=None), ndl, notion)

        with self.assertRaises(IsbnNotDetectedError):
            service.register_image(b"image bytes", mime_type="image/jpeg")

        self.assertEqual(ndl.calls, [])
        self.assertEqual(notion.calls, [])

    def test_register_image_raises_when_ndl_has_no_matching_book(self) -> None:
        ndl = FakeNdl(NdlSruResponse(0, ()))
        notion = FakeNotion()
        service = BookRegistrationService(FakeVlm(), ndl, notion)

        with self.assertRaises(BookNotFoundError):
            service.register_image(b"image bytes", mime_type="image/jpeg")

        self.assertEqual(notion.calls, [])

    def test_register_image_validates_genre_before_external_calls(self) -> None:
        vlm = FakeVlm()
        ndl = FakeNdl()
        notion = FakeNotion()
        service = BookRegistrationService(vlm, ndl, notion)

        with self.assertRaises(TypeError):
            service.register_image(
                b"image bytes",
                mime_type="image/jpeg",
                genre=123,
            )

        self.assertEqual(vlm.calls, [])
        self.assertEqual(ndl.calls, [])
        self.assertEqual(notion.calls, [])

    def test_register_image_serializes_notion_duplicate_check_and_create(self) -> None:
        notion = ConcurrencyTrackingNotion()
        service = BookRegistrationService(FakeVlm(), FakeNdl(), notion)
        tracking_lock = AcquisitionTrackingLock()
        service._notion_write_lock = tracking_lock
        barrier = threading.Barrier(3)
        errors = []

        def register() -> None:
            try:
                barrier.wait()
                service.register_image(b"image bytes", mime_type="image/jpeg")
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=register) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        self.assertTrue(notion.first_call_started.wait(timeout=1))
        self.assertTrue(tracking_lock.second_attempt_started.wait(timeout=1))
        notion.release_first_call.set()
        for thread in threads:
            thread.join(timeout=1)

        self.assertEqual(errors, [])
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(notion.max_active_calls, 1)
        self.assertEqual(len(notion.calls), 2)


if __name__ == "__main__":
    unittest.main()
