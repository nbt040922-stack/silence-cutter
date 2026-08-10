import unittest

from caption_engine.config import CaptionConfig
from caption_engine.coverage import resolve_caption_coverage
from caption_engine.models import CaptionSegment, WordTimestamp


def caption(text, *times):
    words = [WordTimestamp(str(index), start, end) for index, (start, end) in enumerate(times)]
    return CaptionSegment(words[0].start, words[-1].end, text, words)


class CaptionCoverageTests(unittest.TestCase):
    def test_continuous_phrase_bridges_small_caption_gaps(self):
        source = [
            caption("A", (0.0, 0.5)),
            caption("B C", (0.55, 1.0), (1.05, 1.5)),
        ]
        resolved, diagnostics = resolve_caption_coverage(
            source, [{"start": 0.0, "end": 1.5}], CaptionConfig()
        )
        self.assertEqual(resolved[0].end, resolved[1].start)
        self.assertEqual(diagnostics["bridged_gap_count"], 1)

    def test_genuine_silence_is_not_bridged(self):
        resolved, _ = resolve_caption_coverage(
            [caption("A", (0.0, 2.0)), caption("B", (5.0, 7.0))],
            [{"start": 0.0, "end": 2.0}, {"start": 5.0, "end": 7.0}],
            CaptionConfig(),
        )
        self.assertEqual((resolved[0].end, resolved[1].start), (2.0, 5.0))

    def test_missing_whisper_speech_is_reported_without_fabricating_coverage(self):
        resolved, diagnostics = resolve_caption_coverage(
            [caption("A", (10.0, 10.8)), caption("B", (12.3, 13.0))],
            [{"start": 10.0, "end": 13.0}],
            CaptionConfig(),
        )
        gap = diagnostics["untranscribed_speech_gaps"][0]
        self.assertEqual((gap["start"], gap["end"]), (10.8, 12.3))
        self.assertEqual(diagnostics["untranscribed_speech_gap_count"], 1)
        self.assertEqual((resolved[0].end, resolved[1].start), (10.8, 12.3))

    def test_every_word_remains_inside_its_caption_display_interval(self):
        source = [CaptionSegment(0.2, 0.8, "A", [WordTimestamp("A", 0.0, 1.0)])]
        resolved, _ = resolve_caption_coverage(source, None, CaptionConfig())
        self.assertLessEqual(resolved[0].start, resolved[0].words[0].start)
        self.assertGreaterEqual(resolved[0].end, resolved[0].words[0].end)
        self.assertEqual((source[0].words[0].start, source[0].words[0].end), (0.0, 1.0))


if __name__ == "__main__":
    unittest.main()
