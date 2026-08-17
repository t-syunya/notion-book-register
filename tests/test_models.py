import unittest

from notion_book_register import Book


class BookTest(unittest.TestCase):
    def test_book_normalizes_isbn_and_authors(self) -> None:
        book = Book(
            isbn13="978-4-297-13578-2",
            title="  Python Testing  ",
            authors=(" Author A ", "", "Author B"),
            publisher="Publisher",
        )

        self.assertEqual(book.isbn13, "9784297135782")
        self.assertEqual(book.title, "Python Testing")
        self.assertEqual(book.authors, ("Author A", "Author B"))

    def test_book_requires_title(self) -> None:
        with self.assertRaises(ValueError):
            Book(isbn13="9784297135782", title=" ")


if __name__ == "__main__":
    unittest.main()
