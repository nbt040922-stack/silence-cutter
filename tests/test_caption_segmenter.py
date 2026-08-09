import unittest

from caption_engine.config import CaptionConfig
from caption_engine.models import TranscriptSegment, WordTimestamp
from caption_engine.segmenter import balance_lines, segment_transcript


def words(*items):
    return [WordTimestamp(text, start, end) for text, start, end in items]


def transcript(items):
    return [TranscriptSegment(items[0].start, items[-1].end, "", items)] if items else []


def config(**changes):
    values = {"min_caption_duration": 0.0}
    values.update(changes)
    return CaptionConfig(**values)


class CaptionSegmenterTests(unittest.TestCase):
    def test_english_and_vietnamese_keep_natural_spacing(self):
        english = words(
            ("Hello", 0, 0.3), ("everyone", 0.3, 0.7), (".", 0.7, 0.8)
        )
        vietnamese = words(
            ("Xin", 0, 0.3),
            ("chào", 0.3, 0.7),
            ("bạn", 0.7, 1.0),
            ("!", 1.0, 1.1),
        )
        self.assertEqual(
            segment_transcript(transcript(english), config())[0].text,
            "Hello everyone.",
        )
        self.assertEqual(
            segment_transcript(transcript(vietnamese), config())[0].text,
            "Xin chào bạn!",
        )

    def test_japanese_ignores_latin_word_limit_and_preserves_timestamps(self):
        tokens = [
            "今回", "は", "ですね", "今", "から", "夜", "ご",
            "飯", "を", "作", "って", "い", "こう",
        ]
        items = [
            WordTimestamp(text, index * 0.3, (index + 1) * 0.3)
            for index, text in enumerate(tokens)
        ]
        captions = segment_transcript(
            transcript(items), config(max_words_per_caption=2, max_caption_duration=10)
        )
        self.assertEqual(len(captions), 1)
        self.assertEqual(captions[0].text, "".join(tokens))
        self.assertEqual((captions[0].start, captions[0].end), (0.0, 3.9))

    def test_chinese_joining_and_punctuation(self):
        items = words(
            ("今天", 0, 0.3),
            ("天气", 0.3, 0.6),
            ("很好", 0.6, 0.9),
            ("。", 0.9, 1.0),
        )
        self.assertEqual(
            segment_transcript(transcript(items), config())[0].text,
            "今天天气很好。",
        )

    def test_korean_preserves_whisper_space_boundaries(self):
        items = [
            WordTimestamp("안녕하세요", 0, 0.5, space_before=False),
            WordTimestamp("여러분", 0.5, 1.0, space_before=True),
            WordTimestamp(".", 1.0, 1.1, space_before=False),
        ]
        self.assertEqual(
            segment_transcript(transcript(items), config())[0].text,
            "안녕하세요 여러분.",
        )

    def test_cjk_two_line_balancing_uses_token_boundaries(self):
        tokens = ["今回", "は", "ですね", "今", "から", "夜ご飯"]
        text = "".join(tokens)
        lines = balance_lines(
            text, max_chars=6, max_lines=2, tokens=tokens
        ).splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual("".join(lines), text)
        self.assertTrue(all(len(line) <= 6 for line in lines))

    def test_punctuation_break(self):
        items = words(("Welcome.", 0, 1), ("Continue", 1.1, 2.1))
        captions = segment_transcript(transcript(items), config())
        self.assertEqual([caption.text for caption in captions], ["Welcome.", "Continue"])

    def test_long_word_gap_break(self):
        items = words(("Hello", 0, 1), ("again", 2, 3))
        self.assertEqual(len(segment_transcript(transcript(items), config())), 2)

    def test_max_duration_break(self):
        items = words(("First", 0, 1), ("second", 2, 3), ("third", 5.5, 6.5))
        captions = segment_transcript(
            transcript(items), config(max_gap_between_words=10, max_caption_duration=5)
        )
        self.assertEqual(len(captions), 2)

    def test_max_word_count_break(self):
        items = words(("Alpha", 0, 1), ("bravo", 1, 2), ("charlie", 2, 3))
        captions = segment_transcript(
            transcript(items), config(max_words_per_caption=2)
        )
        self.assertEqual([len(caption.words) for caption in captions], [2, 1])

    def test_line_balancing(self):
        text = "Officer, put your hands up and turn around right now."
        balanced = balance_lines(text, max_chars=32, max_lines=2)
        lines = balanced.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(len(line) <= 32 for line in lines))
        self.assertLessEqual(abs(len(lines[0]) - len(lines[1])), 8)

    def test_short_caption_merge(self):
        items = words(("Hi", 0, 0.2), ("everyone", 0.3, 1.2))
        captions = segment_transcript(transcript(items), CaptionConfig())
        self.assertEqual(len(captions), 1)
        self.assertEqual(captions[0].text, "Hi everyone")

    def test_timestamps_are_monotonic_and_do_not_overlap(self):
        items = words(
            ("One.", 0, 1),
            ("Two.", 1.1, 2),
            ("Three.", 2.1, 3),
        )
        captions = segment_transcript(transcript(items), config())
        self.assertEqual([caption.start for caption in captions], sorted(caption.start for caption in captions))
        self.assertTrue(all(left.end <= right.start for left, right in zip(captions, captions[1:])))

    def test_overlapping_word_groups_are_merged(self):
        items = words(("First.", 0, 2), ("Second.", 1.5, 3))
        captions = segment_transcript(transcript(items), config())
        self.assertEqual(len(captions), 1)
        self.assertEqual((captions[0].start, captions[0].end), (0.0, 3.0))

    def test_empty_transcription(self):
        self.assertEqual(segment_transcript([], CaptionConfig()), [])

    def test_single_word_transcription(self):
        items = words(("Hello", 1, 1.5))
        captions = segment_transcript(transcript(items), CaptionConfig())
        self.assertEqual(len(captions), 1)
        self.assertEqual((captions[0].start, captions[0].end), (1.0, 1.5))


if __name__ == "__main__":
    unittest.main()
