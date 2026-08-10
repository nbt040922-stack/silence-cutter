import unittest

from caption_engine.config import CaptionConfig
from caption_engine.models import TranscriptSegment, WordTimestamp
from caption_engine.segmenter import (
    segment_transcript,
    segment_transcript_with_diagnostics,
)


ENGLISH = ["I", "actually", "don't", "know", "what", "happened", "there"]
JAPANESE = [
    "今回", "は", "ですね", "今", "から", "夜", "ご", "飯", "を",
    "作", "って", "い", "こう", "と", "思い", "ます", "ので", "よろしく",
]


def make_transcript(tokens, starts, duration):
    words = [
        WordTimestamp(token, start, start + duration)
        for token, start in zip(tokens, starts)
    ]
    return [TranscriptSegment(words[0].start, words[-1].end, "", words)]


class AdaptiveCaptionTests(unittest.TestCase):
    def test_slow_english_uses_fewer_larger_groups_than_fast_english(self):
        slow = make_transcript(
            ENGLISH, [0.0, 0.5, 1.1, 1.7, 2.4, 3.0, 3.8], 0.25
        )
        fast = make_transcript(ENGLISH, [index * 0.2 for index in range(7)], 0.15)
        slow_captions = segment_transcript(slow, CaptionConfig())
        fast_captions = segment_transcript(fast, CaptionConfig())
        self.assertLess(len(slow_captions), len(fast_captions))
        self.assertEqual(len(slow_captions), 1)
        self.assertGreater(
            len(slow_captions[0].words),
            max(len(caption.words) for caption in fast_captions),
        )

    def test_slow_japanese_keeps_more_characters_than_fast_japanese(self):
        tokens = JAPANESE * 2
        slow = make_transcript(
            tokens, [index * 0.18 for index in range(len(tokens))], 0.14
        )
        fast = make_transcript(
            tokens, [index * 0.06 for index in range(len(tokens))], 0.05
        )
        slow_captions = segment_transcript(slow, CaptionConfig())
        fast_captions = segment_transcript(fast, CaptionConfig())
        self.assertGreater(
            max(len(caption.text.replace("\n", "")) for caption in slow_captions),
            max(len(caption.text.replace("\n", "")) for caption in fast_captions),
        )
        self.assertNotIn(" ", "".join(caption.text for caption in fast_captions))

    def test_sentence_punctuation_beats_adaptive_target(self):
        tokens = ["This", "ends.", "New", "sentence", "continues"]
        transcript = make_transcript(tokens, [0, 0.25, 0.5, 0.75, 1.0], 0.2)
        captions = segment_transcript(transcript, CaptionConfig())
        self.assertEqual(captions[0].text, "This ends.")
        self.assertEqual(captions[1].text, "New sentence continues")

    def test_long_pause_splits_even_slow_speech(self):
        transcript = make_transcript(
            ["Slow", "speech", "after", "pause"], [0.0, 0.8, 2.2, 3.0], 0.3
        )
        captions = segment_transcript(transcript, CaptionConfig())
        self.assertEqual([caption.text for caption in captions], ["Slow speech", "after pause"])

    def test_adaptive_does_not_reintroduce_cjk_tiny_tail(self):
        transcript = make_transcript(
            ["ほら超立派な綺麗なピーマンが収穫でき", "たので"],
            [0.0, 4.98],
            0.46,
        )
        transcript[0].words[0].end = 4.98
        captions = segment_transcript(transcript, CaptionConfig())
        self.assertEqual(len(captions), 1)
        self.assertEqual(
            captions[0].text.replace("\n", ""),
            "ほら超立派な綺麗なピーマンが収穫できたので",
        )

    def test_two_line_hard_limit_and_diagnostics(self):
        transcript = make_transcript(
            ENGLISH * 3,
            [index * 0.18 for index in range(len(ENGLISH) * 3)],
            0.14,
        )
        captions, diagnostics = segment_transcript_with_diagnostics(
            transcript, CaptionConfig()
        )
        self.assertTrue(all(len(caption.text.splitlines()) <= 2 for caption in captions))
        self.assertGreater(diagnostics["average_speech_rate"], 0)
        self.assertGreater(diagnostics["median_caption_duration"], 0)
        self.assertGreaterEqual(diagnostics["maximum_reading_load"], 0)
        self.assertEqual(len(diagnostics["captions"]), len(captions))

    def test_adaptive_can_be_disabled(self):
        fast = make_transcript(ENGLISH, [index * 0.2 for index in range(7)], 0.15)
        captions = segment_transcript(
            fast, CaptionConfig(adaptive_segmentation=False)
        )
        self.assertEqual(len(captions), 1)


if __name__ == "__main__":
    unittest.main()
