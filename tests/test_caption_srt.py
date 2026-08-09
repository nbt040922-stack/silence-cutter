import unittest

from caption_engine.models import CaptionSegment, WordTimestamp
from caption_engine.srt import captions_to_srt, format_timestamp, milliseconds


class CaptionSrtTests(unittest.TestCase):
    def test_millisecond_conversion(self):
        self.assertEqual(milliseconds(12.3096), 12_310)
        self.assertEqual(format_timestamp(3661.007), "01:01:01,007")

    def test_srt_formatting_and_unicode(self):
        captions = [
            CaptionSegment(
                12.31,
                14.92,
                "Xin chào thế giới. 你好。",
                [WordTimestamp("Xin", 12.31, 12.6)],
            )
        ]
        self.assertEqual(
            captions_to_srt(captions),
            "1\n00:00:12,310 --> 00:00:14,920\nXin chào thế giới. 你好。\n",
        )

    def test_overlapping_captions_are_rejected(self):
        captions = [
            CaptionSegment(0, 2, "One"),
            CaptionSegment(1.5, 3, "Two"),
        ]
        with self.assertRaisesRegex(ValueError, "must not overlap"):
            captions_to_srt(captions)


if __name__ == "__main__":
    unittest.main()
