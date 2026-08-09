import unittest

from caption_engine.models import WordTimestamp
from timeline_engine.mapper import (
    build_timeline_segments,
    map_source_time_to_output_time,
    remap_words,
)
from timeline_engine.models import TimelineConfig, TimelineSegment


class TimelineMapperTests(unittest.TestCase):
    def setUp(self):
        self.timeline = build_timeline_segments(
            [
                {"start": 0.0, "end": 4.0},
                {"start": 6.0, "end": 10.0},
                {"start": 20.0, "end": 30.0},
            ]
        )

    def test_basic_mapping_across_multiple_keeps(self):
        self.assertEqual(map_source_time_to_output_time(2.0, self.timeline), 2.0)
        self.assertEqual(map_source_time_to_output_time(7.0, self.timeline), 5.0)
        self.assertEqual(map_source_time_to_output_time(21.0, self.timeline), 9.0)

    def test_mapping_outside_and_at_keep_boundaries(self):
        self.assertIsNone(map_source_time_to_output_time(-1.0, self.timeline))
        self.assertIsNone(map_source_time_to_output_time(5.0, self.timeline))
        self.assertIsNone(map_source_time_to_output_time(31.0, self.timeline))
        self.assertEqual(map_source_time_to_output_time(0.0, self.timeline), 0.0)
        self.assertEqual(map_source_time_to_output_time(4.0, self.timeline), 4.0)
        self.assertEqual(map_source_time_to_output_time(6.0, self.timeline), 4.0)
        self.assertEqual(map_source_time_to_output_time(30.0, self.timeline), 18.0)

    def test_floating_point_boundaries_and_consecutive_keeps(self):
        timeline = build_timeline_segments(
            [{"start": 0.1, "end": 0.3}, {"start": 0.3, "end": 0.6}]
        )
        self.assertAlmostEqual(map_source_time_to_output_time(0.3, timeline), 0.2)
        self.assertAlmostEqual(timeline[-1].output_end, 0.5)

    def test_invalid_keep_timeline_is_rejected(self):
        for keep in (
            [{"start": 2.0, "end": 3.0}, {"start": 1.0, "end": 1.5}],
            [{"start": 0.0, "end": 2.0}, {"start": 1.5, "end": 3.0}],
        ):
            with self.subTest(keep=keep):
                with self.assertRaisesRegex(ValueError, "sorted and non-overlapping"):
                    build_timeline_segments(keep)

    def test_non_contiguous_output_timeline_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "contiguous"):
            from timeline_engine.mapper import validate_timeline_segments

            validate_timeline_segments(
                [TimelineSegment(0, 1, 0, 1), TimelineSegment(2, 3, 1.1, 2.1)]
            )

    def test_output_duration_equals_sum_of_keep_durations(self):
        expected = sum(item.source_duration for item in self.timeline)
        self.assertAlmostEqual(self.timeline[-1].output_end, expected)
        self.assertTrue(
            all(
                left.output_end == right.output_start
                for left, right in zip(self.timeline, self.timeline[1:])
            )
        )

    def test_words_fully_kept_and_removed(self):
        words = [
            WordTimestamp("kept", 1.0, 1.5, 0.9, True),
            WordTimestamp("removed", 4.5, 5.5),
        ]
        result = remap_words(words, self.timeline)
        self.assertEqual(result.words_after, 1)
        mapped = result.words[0].word
        self.assertEqual((mapped.text, mapped.start, mapped.end), ("kept", 1.0, 1.5))
        self.assertEqual(mapped.probability, 0.9)
        self.assertTrue(mapped.space_before)

    def test_word_clipped_at_left_and_right_boundaries(self):
        timeline = build_timeline_segments([{"start": 10.0, "end": 12.0}])
        result = remap_words(
            [WordTimestamp("left", 9.8, 10.2), WordTimestamp("right", 11.8, 12.2)],
            timeline,
        )
        expected = [(0.0, 0.2), (1.8, 2.0)]
        for actual, wanted in zip(
            [(item.word.start, item.word.end) for item in result.words], expected
        ):
            self.assertAlmostEqual(actual[0], wanted[0])
            self.assertAlmostEqual(actual[1], wanted[1])
        self.assertEqual(result.boundary_word_clipped_count, 2)

    def test_multi_keep_word_chooses_larger_then_earlier_on_tie(self):
        timeline = build_timeline_segments(
            [{"start": 10.0, "end": 12.0}, {"start": 14.0, "end": 16.0}]
        )
        tied = remap_words([WordTimestamp("tie", 11.8, 14.2)], timeline)
        self.assertAlmostEqual(tied.words[0].source_start, 11.8)
        self.assertAlmostEqual(tied.words[0].source_end, 12.0)
        larger = remap_words([WordTimestamp("later", 11.9, 14.4)], timeline)
        self.assertAlmostEqual(larger.words[0].source_start, 14.0)
        self.assertAlmostEqual(larger.words[0].source_end, 14.4)
        self.assertEqual(tied.boundary_word_multi_keep_count, 1)
        self.assertEqual(larger.boundary_word_multi_keep_count, 1)

    def test_touching_boundary_without_surviving_duration_is_removed(self):
        timeline = build_timeline_segments([{"start": 10.0, "end": 12.0}])
        result = remap_words(
            [WordTimestamp("touch", 9.8, 10.0), WordTimestamp("inside", 10.0, 10.1)],
            timeline,
        )
        self.assertEqual([item.word.text for item in result.words], ["inside"])

    def test_too_small_surviving_fragment_is_removed(self):
        timeline = build_timeline_segments([{"start": 10.0, "end": 12.0}])
        result = remap_words(
            [WordTimestamp("tiny", 9.99, 10.02)],
            timeline,
            TimelineConfig(min_surviving_word_duration=0.03),
        )
        self.assertEqual(result.words_after, 0)


if __name__ == "__main__":
    unittest.main()
