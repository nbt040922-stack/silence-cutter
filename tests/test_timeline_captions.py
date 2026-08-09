import unittest

from caption_engine.config import CaptionConfig
from caption_engine.models import CaptionSegment, WordTimestamp
from timeline_engine.captions import remap_captions
from timeline_engine.mapper import build_timeline_segments


def caption(text, *words):
    items = [WordTimestamp(*item) for item in words]
    return CaptionSegment(items[0].start, items[-1].end, text, items)


class TimelineCaptionTests(unittest.TestCase):
    def test_fully_kept_caption_preserves_original_text(self):
        source = caption("Original\nlayout", ("Original", 0, 0.5), ("layout", 0.5, 1.0))
        result = remap_captions(
            [source], build_timeline_segments([{"start": 0, "end": 2}])
        )
        self.assertEqual(result.captions[0].text, "Original\nlayout")

    def test_fully_removed_caption_is_removed(self):
        source = caption("Gone", ("Gone", 2, 2.5))
        result = remap_captions(
            [source], build_timeline_segments([{"start": 0, "end": 1}])
        )
        self.assertEqual(result.captions, ())

    def test_caption_is_split_across_cut(self):
        source = caption(
            "A B C D",
            ("A", 3.0, 3.5),
            ("B", 3.5, 4.0),
            ("C", 6.0, 6.5),
            ("D", 6.5, 7.0),
        )
        timeline = build_timeline_segments(
            [{"start": 0, "end": 4}, {"start": 6, "end": 10}]
        )
        result = remap_captions([source], timeline)
        self.assertEqual([item.text for item in result.captions], ["A B", "C D"])
        self.assertEqual(
            [(item.start, item.end) for item in result.captions],
            [(3.0, 4.0), (4.0, 5.0)],
        )

    def test_caption_reconstructs_text_after_word_removal(self):
        source = caption(
            "Hello discarded",
            ("Hello", 0.0, 0.5),
            ("discarded", 0.99, 1.02),
        )
        result = remap_captions(
            [source], build_timeline_segments([{"start": 0, "end": 1}])
        )
        self.assertEqual(result.captions[0].text, "Hello")

    def test_multilingual_joining_and_punctuation_are_preserved(self):
        cases = [
            (["Hello", "everyone", "."], "Hello everyone."),
            (["Xin", "chào", "bạn", "!"], "Xin chào bạn!"),
            (["今日", "は", "晴れ", "。"], "今日は晴れ。"),
            (["今天", "很好", "。"], "今天很好。"),
        ]
        for tokens, expected in cases:
            with self.subTest(expected=expected):
                words = [
                    WordTimestamp(token, index * 0.2, (index + 1) * 0.2)
                    for index, token in enumerate(tokens)
                ]
                words.append(WordTimestamp("removed", 1.99, 2.01))
                source = CaptionSegment(0, 2.01, "wrong", words)
                result = remap_captions(
                    [source],
                    build_timeline_segments([{"start": 0, "end": 2}]),
                    caption_config=CaptionConfig(min_caption_duration=0),
                )
                self.assertEqual(result.captions[0].text, expected)

    def test_remapped_caption_invariants(self):
        source = [
            caption("One", ("One", 0.1, 0.5)),
            caption("Two", ("Two", 2.1, 2.5)),
        ]
        result = remap_captions(
            source,
            build_timeline_segments(
                [{"start": 0, "end": 1}, {"start": 2, "end": 3}]
            ),
        )
        captions = result.captions
        self.assertTrue(all(item.start >= 0 and item.start < item.end for item in captions))
        self.assertTrue(all(a.end <= b.start for a, b in zip(captions, captions[1:])))
        self.assertTrue(all(len(item.text.splitlines()) <= 2 for item in captions))


if __name__ == "__main__":
    unittest.main()
