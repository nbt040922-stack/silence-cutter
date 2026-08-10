import unittest

from caption_engine.config import CaptionConfig
from caption_engine.models import TranscriptSegment, WordTimestamp
from caption_engine.segmenter import (
    classify_boundary,
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
    def test_classify_boundary_protects_japanese_lexical_fragments(self):
        pairs = [
            ("\u53cb", "\u9054"),
            ("\u72b6", "\u614b"),
            ("\u53ce", "\u7a6b"),
            ("\u71b1", "\u4e2d"),
            ("\u30d4", "\u30fc"),
            ("\u30bf\u30fc", "\u541b"),
            ("\u4e0a", "\u306b"),
            ("\u305b", "\u3044"),
        ]
        for left, right in pairs:
            with self.subTest(pair=(left, right)):
                result = classify_boundary(
                    WordTimestamp(left, 0.0, 0.1),
                    WordTimestamp(right, 0.1, 0.2),
                )
                self.assertEqual(result["classification"], "protected")

    def test_realistic_whisper_lexical_units_are_not_split(self):
        units = [
            ["\u53cb", "\u9054"],
            ["\u72b6", "\u614b"],
            ["\u53ce", "\u7a6b"],
            ["\u71b1", "\u4e2d", "\u75c7"],
            ["\u30d4", "\u30fc", "\u30de", "\u30f3"],
            ["\u30bf\u30fc", "\u541b"],
        ]
        for unit in units:
            with self.subTest(unit=unit):
                tokens = ["\u8aac\u660e"] * 6 + unit + [
                    "\u3067\u3059\u3002", "\u6b21", "\u3067\u3059\u3002"
                ]
                captions = segment_transcript(transcript(tokens), CaptionConfig())
                selected = boundaries(captions)
                self.assertTrue(
                    all(pair not in selected for pair in zip(unit, unit[1:]))
                )

    def test_hiragana_lexical_fragments_are_not_split(self):
        cases = [
            (["5", "\u4eba", "\u304f", "\u3089\u3044", "\u6765\u3066\u3066"], ("\u304f", "\u3089\u3044")),
            (["\u305d\u3063", "\u3061\u3083\u3093"], ("\u305d\u3063", "\u3061\u3083\u3093")),
            (["\u3061\u3087\u3063", "\u3068"], ("\u3061\u3087\u3063", "\u3068")),
            (["\u3084\u3063", "\u3071\u308a"], ("\u3084\u3063", "\u3071\u308a")),
        ]
        for middle, protected_pair in cases:
            with self.subTest(pair=protected_pair):
                tokens = ["\u8aac\u660e"] * 6 + middle + [
                    "\u7d9a\u304d", "\u3067\u3059\u3002"
                ]
                captions = segment_transcript(transcript(tokens), CaptionConfig())
                self.assertNotIn(protected_pair, boundaries(captions))

    def test_early_natural_cjk_boundary_beats_size_target(self):
        tokens = [
            "\u3053\u308c\u306f", "\u77ed\u3044", "\u81ea\u7136\u306a",
            "\u533a\u5207\u308a", "\u3067\u3059", "\u6b21\u306e",
            "\u6587\u3067\u3059",
        ]
        captions = segment_transcript(
            transcript(tokens, step=0.30, duration=0.24), CaptionConfig()
        )
        self.assertTrue(
            captions[0].text.replace("\n", "").endswith("\u3067\u3059")
        )
        self.assertLess(
            visible_character_count(captions[0].text),
            CaptionConfig().preferred_cjk_chars,
        )

    def test_short_phrase_cue_without_pause_does_not_flicker(self):
        captions = segment_transcript(
            transcript(
                ["\u4eca", "\u304b\u3089", "\u591c", "\u3054\u98ef"],
                step=0.22,
                duration=0.18,
            ),
            CaptionConfig(),
        )
        self.assertEqual(len(captions), 1)

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
        self.assertGreaterEqual(max(sizes), 10)

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
                "lexical_penalty",
                "orphan_penalty",
                "size_overrun_penalty",
            },
        )

    def test_protection_and_forced_counts_are_reported(self):
        tokens = ["\u53cb", "\u9054"] * 20 + ["\u64ae", "\u3063\u3066"]
        _, diagnostics = segment_transcript_with_diagnostics(
            transcript(tokens), CaptionConfig()
        )
        self.assertGreater(diagnostics["lexical_protected_boundary_count"], 0)
        self.assertGreater(diagnostics["grammatical_protected_boundary_count"], 0)
        self.assertGreater(diagnostics["forced_boundary_count"], 0)
        self.assertTrue(
            all(
                "lexical_penalty" in boundary and "forced" in boundary
                for boundary in diagnostics["selected_boundaries"]
            )
        )


if __name__ == "__main__":
    unittest.main()
