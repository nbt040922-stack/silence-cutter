import unittest

from caption_engine.models import WordTimestamp


class CaptionModelTests(unittest.TestCase):
    def test_word_normalization(self):
        word = WordTimestamp("  hello\n world  ", -2, -4, 2.0)
        self.assertEqual(word.text, "hello world")
        self.assertEqual(word.start, 0.0)
        self.assertEqual(word.end, 0.0)
        self.assertIsNone(word.probability)


if __name__ == "__main__":
    unittest.main()
