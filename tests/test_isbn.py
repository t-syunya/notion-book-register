import unittest

from notion_book_register import InvalidIsbnError, normalize_isbn13, validate_isbn13


class IsbnTest(unittest.TestCase):
    def test_normalize_isbn13_accepts_digits(self) -> None:
        self.assertEqual(normalize_isbn13("9784297135782"), "9784297135782")

    def test_normalize_isbn13_accepts_979_prefix(self) -> None:
        self.assertEqual(normalize_isbn13("9791090636071"), "9791090636071")

    def test_normalize_isbn13_ignores_common_separators(self) -> None:
        self.assertEqual(normalize_isbn13("978-4-297-13578-2"), "9784297135782")

    def test_normalize_isbn13_ignores_spaces(self) -> None:
        self.assertEqual(normalize_isbn13("978 4 297 13578 2"), "9784297135782")

    def test_normalize_isbn13_rejects_invalid_check_digit(self) -> None:
        with self.assertRaises(InvalidIsbnError):
            normalize_isbn13("9784297135780")

    def test_normalize_isbn13_rejects_non_isbn_ean13_prefix(self) -> None:
        with self.assertRaises(InvalidIsbnError):
            normalize_isbn13("4006381333931")

    def test_normalize_isbn13_rejects_non_separator_characters(self) -> None:
        for value in (
            "x9784297135782",
            "\uff19\uff17\uff18\uff14\uff12\uff19\uff17\uff11\uff13\uff15\uff17\uff18\uff12",
            "978429713578\u00b2",
        ):
            with self.subTest(value=value):
                with self.assertRaises(InvalidIsbnError):
                    normalize_isbn13(value)

    def test_validate_isbn13_returns_false_for_non_isbn13(self) -> None:
        self.assertFalse(validate_isbn13("4297135784"))

    def test_validate_isbn13_returns_false_for_non_isbn_ean13_prefix(self) -> None:
        self.assertFalse(validate_isbn13("4006381333931"))

    def test_validate_isbn13_returns_false_for_non_ascii_digits(self) -> None:
        self.assertFalse(
            validate_isbn13(
                "\uff19\uff17\uff18\uff14\uff12\uff19\uff17\uff11\uff13\uff15\uff17\uff18\uff12"
            )
        )

    def test_validate_isbn13_returns_false_for_invalid_characters(self) -> None:
        for value in (
            "x9784297135782",
            "978\t4297135782",
            None,
        ):
            with self.subTest(value=value):
                self.assertFalse(validate_isbn13(value))


if __name__ == "__main__":
    unittest.main()
