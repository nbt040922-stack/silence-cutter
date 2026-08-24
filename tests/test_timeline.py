import math
import unittest

from silence_cutter import SilenceCutterConfig
from silence_cutter.timeline import build_timeline


def config(**changes):
    values = {
        "min_silence_duration": 1.0,
        "speech_pad_before": 0.0,
        "speech_pad_after": 0.0,
        "merge_gap": 0.0,
        "min_keep_duration": 0.5,
    }
    values.update(changes)
    return SilenceCutterConfig(**values)


class TimelineTests(unittest.TestCase):
    def test_no_speech_detected(self):
        self.assertEqual(build_timeline([], 10, config()), {"keep": [], "cut": [{"start": 0.0, "end": 10}]})

    def test_speech_for_entire_video(self):
        result = build_timeline([{"start": 0, "end": 10}], 10, config())
        self.assertEqual(result, {"keep": [{"start": 0.0, "end": 10}], "cut": []})

    def test_one_long_silence(self):
        result = build_timeline([{"start": 0, "end": 2}, {"start": 5, "end": 8}], 8, config())
        self.assertEqual(result["cut"], [{"start": 2.0, "end": 5.0}])

    def test_multiple_long_silences(self):
        speech = [{"start": 1, "end": 2}, {"start": 4, "end": 5}, {"start": 7, "end": 8}]
        self.assertEqual(len(build_timeline(speech, 9, config())["cut"]), 4)

    def test_short_silence_is_preserved(self):
        speech = [{"start": 0, "end": 2}, {"start": 2.5, "end": 5}]
        self.assertEqual(build_timeline(speech, 5, config())["keep"], [{"start": 0.0, "end": 5}])

    def test_overlapping_speech(self):
        speech = [{"start": 1, "end": 3}, {"start": 2, "end": 4}]
        self.assertEqual(build_timeline(speech, 5, config())["keep"], [{"start": 1.0, "end": 4.0}])

    def test_adjacent_timestamps(self):
        speech = [{"start": 1, "end": 2}, {"start": 2, "end": 3}]
        self.assertEqual(build_timeline(speech, 4, config())["keep"], [{"start": 1.0, "end": 3.0}])

    def test_padding_creates_overlap(self):
        speech = [{"start": 1, "end": 2}, {"start": 2.4, "end": 3}]
        result = build_timeline(speech, 4, config(speech_pad_before=0.25, speech_pad_after=0.25))
        self.assertEqual(result["keep"], [{"start": 0.0, "end": 4}])

    def test_speech_at_zero(self):
        self.assertEqual(build_timeline([{"start": 0, "end": 2}], 5, config())["keep"][0]["start"], 0.0)

    def test_speech_at_media_end(self):
        self.assertEqual(build_timeline([{"start": 3, "end": 5}], 5, config())["keep"][-1]["end"], 5)

    def test_very_short_speech_is_expanded_not_deleted(self):
        result = build_timeline([{"start": 4.9, "end": 5.0}], 10, config())
        self.assertAlmostEqual(result["keep"][0]["end"] - result["keep"][0]["start"], 0.5)
        self.assertLessEqual(result["keep"][0]["start"], 4.9)
        self.assertGreaterEqual(result["keep"][0]["end"], 5.0)

    def test_invalid_timestamps(self):
        speech = [
            {"start": 4, "end": 2},
            {"start": "bad", "end": 2},
            {"start": math.nan, "end": 3},
            {"start": -2, "end": 1},
            {"start": 9, "end": 12},
        ]
        result = build_timeline(speech, 10, config())
        self.assertEqual(result["keep"], [{"start": 0.0, "end": 1.0}, {"start": 9.0, "end": 10}])

    def test_tiny_keep_between_cuts(self):
        result = build_timeline([{"start": 4.9, "end": 5.0}], 10, config(min_keep_duration=1.0))
        self.assertEqual(result["keep"], [{"start": 4.45, "end": 5.45}])

    def test_merge_gap(self):
        speech = [{"start": 1, "end": 2}, {"start": 2.3, "end": 3}]
        result = build_timeline(speech, 4, config(merge_gap=0.3))
        self.assertEqual(result["keep"], [{"start": 1.0, "end": 3.0}])

    def test_natural_silence_retention_is_bounded_and_seeded(self):
        settings = config(
            min_keep_duration=0.0,
            natural_silence_min=0.1,
            natural_silence_max=0.3,
            natural_silence_seed=7,
        )
        first = build_timeline([{"start": 0, "end": 1}, {"start": 4, "end": 5}], 5, settings)
        second = build_timeline([{"start": 0, "end": 1}, {"start": 4, "end": 5}], 5, settings)

        self.assertEqual(first, second)
        self.assertEqual(len(first["cut"]), 1)
        retained = 3.0 - (first["cut"][0]["end"] - first["cut"][0]["start"])
        self.assertGreaterEqual(retained, 0.1)
        self.assertLessEqual(retained, 0.3)


if __name__ == "__main__":
    unittest.main()
