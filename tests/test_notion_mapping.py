import unittest

from notion_book_register import Book, book_to_notion_properties


class NotionMappingTest(unittest.TestCase):
    def test_book_to_notion_properties_maps_supported_bookshelf_fields(self) -> None:
        book = Book(
            isbn13="9784297135782",
            title="Python Testing",
            authors=("Author A", "Author B"),
            publisher="Publisher",
            published_date="2026",
            ndl_url="https://ndl.example/books/1",
        )

        properties = book_to_notion_properties(book, genre="技術書")

        self.assertEqual(properties["作品名"], "Python Testing")
        self.assertEqual(properties["状態"], "未読")
        self.assertEqual(properties["ジャンル"], "技術書")
        self.assertIn("ISBN: 9784297135782", properties["memo"])
        self.assertIn("著者: Author A, Author B", properties["memo"])
        self.assertIn("出版社: Publisher", properties["memo"])
        self.assertIn("出版日: 2026", properties["memo"])
        self.assertIn("NDL: https://ndl.example/books/1", properties["memo"])

    def test_book_to_notion_properties_omits_empty_optional_fields(self) -> None:
        book = Book(isbn13="9784297135782", title="Python Testing")

        properties = book_to_notion_properties(book)

        self.assertEqual(
            properties,
            {
                "作品名": "Python Testing",
                "状態": "未読",
                "memo": "ISBN: 9784297135782",
            },
        )


if __name__ == "__main__":
    unittest.main()
