import unittest

from notion_book_register import InvalidIsbnError, normalize_isbn13, validate_isbn13


class IsbnTest(unittest.TestCase):
    def test_normalize_isbn13_accepts_digits(self) -> None:
        self.assertEqual(normalize_isbn13("9784297135782"), "9784297135782")

    def test_normalize_isbn13_ignores_common_separators(self) -> None:
        self.assertEqual(normalize_isbn13("978-4-297-13578-2"), "9784297135782")

    def test_normalize_isbn13_rejects_invalid_check_digit(self) -> None:
        with self.assertRaises(InvalidIsbnError):
            normalize_isbn13("9784297135780")

    def test_validate_isbn13_returns_false_for_non_isbn13(self) -> None:
        self.assertFalse(validate_isbn13("4297135784"))


if __name__ == "__main__":
    unittest.main()
