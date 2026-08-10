import unittest

from caption_engine.config import CaptionConfig
from caption_engine.models import TranscriptSegment, WordTimestamp
from caption_engine.segmenter import (
    segment_transcript,
    segment_transcript_with_diagnostics,
    visible_character_count,
)


def transcript(tokens, step=0.16, duration=0.12, gaps=None):
    gaps = gaps or {}
    words = []
    start = 0.0
    for index, token in enumerate(tokens):
        if index:
            start += gaps.get(index, step - duration)
        words.append(WordTimestamp(token, start, start + duration))
        start += duration
    return [TranscriptSegment(words[0].start, words[-1].end, "", words)]


def boundaries(captions):
    return {
        (left.words[-1].text, right.words[0].text)
        for left, right in zip(captions, captions[1:])
    }


class BoundaryScoringTests(unittest.TestCase):
    def test_japanese_connected_forms_are_protected_at_slow_and_fast_rates(self):
        pairs = [
            ("撮", "って"),
            ("思", "って"),
            ("作", "って"),
            ("な", "ので"),
            ("ん", "です"),
            ("けれ", "ども"),
            ("暑", "くなって"),
            ("食わ", "ず"),
            ("トイ", "レ"),
        ]
        for step in (0.30, 0.09):
            for left, right in pairs:
                with self.subTest(step=step, pair=(left, right)):
                    tokens = ["説明"] * 8 + [left, right] + ["続き"] * 8
                    captions = segment_transcript(
                        transcript(tokens, step=step, duration=step * 0.75),
                        CaptionConfig(),
                    )
                    self.assertNotIn((left, right), boundaries(captions))

    def test_strong_punctuation_and_pause_beat_duration_target(self):
        words = [
            WordTimestamp("Short", 0.0, 0.35),
            WordTimestamp("phrase.", 0.35, 0.85),
            WordTimestamp("Continues", 1.35, 1.8),
            WordTimestamp("normally", 1.8, 2.3),
        ]
        captions = segment_transcript(
            [TranscriptSegment(0, 2.3, "", words)], CaptionConfig()
        )
        self.assertEqual(captions[0].text, "Short phrase.")

    def test_weak_boundaries_do_not_create_flicker(self):
        captions = segment_transcript(
            transcript(["continuous"] * 10, step=0.11, duration=0.10),
            CaptionConfig(),
        )
        self.assertLessEqual(len(captions), 3)
        self.assertTrue(
            all(caption.end - caption.start >= 0.35 for caption in captions)
        )

    def test_visual_pressure_stays_within_absolute_cjk_capacity(self):
        captions = segment_transcript(
            transcript(["日本語"] * 20, step=0.16, duration=0.14),
            CaptionConfig(),
        )
        sizes = [visible_character_count(caption.text) for caption in captions]
        self.assertLessEqual(max(sizes), 30)
        self.assertGreaterEqual(max(sizes), CaptionConfig().preferred_cjk_chars - 1)

    def test_short_complete_phrase_between_pauses_is_preserved(self):
        words = [
            WordTimestamp("前です", 0.0, 0.5),
            WordTimestamp("思ってて", 1.0, 1.45),
            WordTimestamp("続きます", 2.0, 2.7),
        ]
        captions = segment_transcript(
            [TranscriptSegment(0, 2.7, "", words)], CaptionConfig()
        )
        self.assertIn("思ってて", [caption.text for caption in captions])

    def test_orphan_suffixes_are_not_standalone(self):
        tokens = ["これは"] * 7 + ["撮", "って", "ない", "ので", "続きます"]
        captions = segment_transcript(transcript(tokens), CaptionConfig())
        orphans = {"って", "ので", "から", "かった"}
        self.assertFalse(
            orphans.intersection(caption.text.replace("\n", "") for caption in captions)
        )

    def test_selected_boundary_diagnostics_expose_score_components(self):
        _, diagnostics = segment_transcript_with_diagnostics(
            transcript(
                ["First", "phrase.", "Second", "phrase."],
                step=0.30,
                duration=0.20,
                gaps={2: 0.50},
            ),
            CaptionConfig(),
        )
        selected = diagnostics["selected_boundaries"]
        self.assertTrue(selected)
        self.assertIn("score", selected[0])
        self.assertEqual(
            set(selected[0]["components"]),
            {
                "pause",
                "punctuation",
                "segment_boundary",
                "phrase",
                "visual_capacity",
                "duration_pressure",
                "reading_load",
                "grammatical_penalty",
                "orphan_penalty",
                "size_overrun_penalty",
            },
        )


if __name__ == "__main__":
    unittest.main()
