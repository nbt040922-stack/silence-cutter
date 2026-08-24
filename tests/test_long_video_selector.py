import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from long_video_selector.selector import (
    LongVideoSelectorConfig, _select_ranked_ranges, adaptive_target_duration,
    constrain_keep_intervals, run_long_video_selector, validate_selected_ranges,
)
from production.pipeline import _analyze_allowed_ranges


class FakeDetector:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.generation_count = 0
        self.model_load_time = 1.0
        self.torch = SimpleNamespace(cuda=SimpleNamespace(max_memory_allocated=lambda: 123))

    def generate_text(self, _images, _prompt, **_options):
        self.generation_count += 1
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


COARSE = "\n".join([
    "300,0.8,event one", "900,0.9,event two",
    "1500,0.95,event three", "2100,0.85,event four",
])
ENHANCED_COARSE = "\n".join(
    f"{center},{.99 - index * .05:.2f},topic {index}"
    for index, center in enumerate((80, 250, 430, 610, 790, 970))
)
ENHANCED_TWO_COARSE = "\n".join([
    "400,0.99,topic one", "1200,0.95,topic two",
])
class LongVideoSelectorTests(unittest.TestCase):
    def test_default_qwen_threshold_is_25_minutes(self):
        self.assertEqual(LongVideoSelectorConfig().threshold, 1500.0)

    def test_short_video_does_not_invoke_qwen(self):
        with tempfile.TemporaryDirectory() as directory:
            factory = Mock()
            result = run_long_video_selector(
                "source.mp4", 900.0, Path(directory) / "selection.json",
                detector_factory=factory,
            )
        self.assertEqual(result["status"], "NOT_APPLICABLE")
        factory.assert_not_called()

    def test_video_at_or_below_25_minutes_does_not_invoke_qwen(self):
        for duration in (1499.9, 1500.0):
            with self.subTest(duration=duration), tempfile.TemporaryDirectory() as directory:
                factory = Mock()
                result = run_long_video_selector(
                    "source.mp4", duration, Path(directory) / "selection.json",
                    detector_factory=factory,
                )
            self.assertEqual(result["status"], "NOT_APPLICABLE")
            factory.assert_not_called()

    @patch("long_video_selector.selector._contact_sheets", return_value=[])
    @patch("long_video_selector.selector._visual_candidates", return_value=([], []))
    @patch("long_video_selector.selector._extract_sampled_frames", return_value=([Path("frame.jpg")], [0.0]))
    def test_enhanced_runs_below_900_and_preserves_part_indexes(self, _extract, _visual, _sheets):
        detector = FakeDetector([ENHANCED_COARSE])
        with tempfile.TemporaryDirectory() as directory:
            result = run_long_video_selector(
                "source.mp4", 850.0, Path(directory) / "selection.json",
                enhanced=True, detector_factory=lambda: detector,
            )
        self.assertEqual(result["status"], "APPLIED")
        self.assertGreater(len(result["ranked_candidates"]), 3)
        self.assertEqual([item["part_index"] for item in result["selected_ranges"]], [1, 2, 3])
        self.assertTrue(all(180 <= item["duration"] <= 300 for item in result["selected_ranges"]))

    @patch("long_video_selector.selector._contact_sheets", return_value=[])
    @patch("long_video_selector.selector._visual_candidates", return_value=([], []))
    @patch("long_video_selector.selector._extract_sampled_frames", return_value=([Path("frame.jpg")], [0.0]))
    def test_enhanced_accepts_exactly_two_valid_candidates(self, _extract, _visual, _sheets):
        detector = FakeDetector([ENHANCED_TWO_COARSE])
        with tempfile.TemporaryDirectory() as directory:
            result = run_long_video_selector(
                "source.mp4", 1800.0, Path(directory) / "selection.json",
                enhanced=True, detector_factory=lambda: detector,
            )
        self.assertEqual(result["status"], "APPLIED")
        self.assertEqual(len(result["selected_ranges"]), 2)
        self.assertEqual(result["part_count"], 2)
        self.assertEqual([item["part_index"] for item in result["selected_ranges"]], [1, 2])

    @patch("long_video_selector.selector._contact_sheets", return_value=[])
    @patch("long_video_selector.selector._visual_candidates", return_value=([], []))
    @patch("long_video_selector.selector._extract_sampled_frames", return_value=([Path("frame.jpg")], [0.0]))
    def test_enhanced_overlapping_two_candidates_fail_open(self, _extract, _visual, _sheets):
        detector = FakeDetector(["400,0.99,topic one\n450,0.95,topic two"])
        with tempfile.TemporaryDirectory() as directory:
            result = run_long_video_selector(
                "source.mp4", 1800.0, Path(directory) / "selection.json",
                enhanced=True, detector_factory=lambda: detector,
            )
        self.assertEqual(result["status"], "LONG_VIDEO_SELECTOR_SKIPPED")
        self.assertEqual(result["part_count"], 0)

    @patch("long_video_selector.selector._contact_sheets", return_value=[])
    @patch("long_video_selector.selector._visual_candidates", return_value=([], []))
    @patch("long_video_selector.selector._extract_sampled_frames", return_value=([Path("frame.jpg")], [0.0]))
    def test_enhanced_duplicate_two_candidates_fail_open(self, _extract, _visual, _sheets):
        detector = FakeDetector(["400,0.99,same topic\n1200,0.95,same topic"])
        with tempfile.TemporaryDirectory() as directory:
            result = run_long_video_selector(
                "source.mp4", 1800.0, Path(directory) / "selection.json",
                enhanced=True, detector_factory=lambda: detector,
            )
        self.assertEqual(result["status"], "LONG_VIDEO_SELECTOR_SKIPPED")
        self.assertEqual(result["part_count"], 0)

    def test_enhanced_insufficient_duration_skips_without_qwen(self):
        factory = Mock()
        with tempfile.TemporaryDirectory() as directory:
            result = run_long_video_selector(
                "source.mp4", 500.0, Path(directory) / "selection.json",
                enhanced=True, detector_factory=factory,
            )
        self.assertEqual(result["status"], "ENHANCED_SELECTOR_SKIPPED_INSUFFICIENT_DURATION")
        factory.assert_not_called()

    @patch("long_video_selector.selector._contact_sheets", return_value=[])
    @patch("long_video_selector.selector._visual_candidates", return_value=([], []))
    @patch("long_video_selector.selector._extract_sampled_frames", return_value=([Path("frame.jpg")], [0.0]))
    def test_long_video_selects_exactly_three_absolute_ranges(self, _extract, _visual, _sheets):
        detector = FakeDetector([COARSE])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "selection.json"
            result = run_long_video_selector(
                "source.mp4", 2520.0, output, detector_factory=lambda: detector,
            )
            persisted = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "APPLIED")
        self.assertEqual(len(result["selected_ranges"]), 3)
        self.assertTrue(validate_selected_ranges(result["selected_ranges"], 2520.0))
        self.assertEqual(result["generation_count"], 1)
        self.assertEqual(persisted["selected_ranges"], result["selected_ranges"])
        self.assertGreater(result["selected_ranges"][0]["start"], 0)

    def test_adaptive_target_duration(self):
        self.assertEqual(adaptive_target_duration(901), 180)
        self.assertEqual(adaptive_target_duration(1500), 180)
        self.assertEqual(adaptive_target_duration(2400), 240)
        self.assertGreater(adaptive_target_duration(3600), 240)
        self.assertLessEqual(adaptive_target_duration(10000), 300)

    def test_ranker_accepts_seconds_suffix_from_qwen(self):
        from long_video_selector.selector import _ranked_candidates
        self.assertEqual(_ranked_candidates("300.0s,0.85,event", 500)[0]["center"], 300.0)

    def test_validation_rejects_overlap_bad_duration_and_bad_score(self):
        valid = [
            {"start": 0, "end": 180, "score": .9},
            {"start": 300, "end": 500, "score": .8},
            {"start": 600, "end": 900, "score": .7},
        ]
        self.assertTrue(validate_selected_ranges(valid, 1500))
        self.assertTrue(validate_selected_ranges(valid[:2], 1500))
        for changed in (
            [valid[0], {"start": 170, "end": 370, "score": .8}, valid[2]],
            [valid[0], {"start": 300, "end": 400, "score": .8}, valid[2]],
            [valid[0], {"start": 300, "end": 500, "score": float("nan")}, valid[2]],
        ):
            self.assertFalse(validate_selected_ranges(changed, 1500))

    def test_duplicate_semantic_candidate_is_rejected(self):
        ranked = [
            {"center": 200, "score": .95, "topic": "same event", "reason": "same"},
            {"center": 600, "score": .94, "topic": "same event", "reason": "same"},
            {"center": 1000, "score": .93, "topic": "different reveal", "reason": "reveal"},
            {"center": 1400, "score": .92, "topic": "resolution", "reason": "resolution"},
        ]
        selected = _select_ranked_ranges(ranked, 1800, 180)
        self.assertEqual(len(selected), 3)
        self.assertEqual(sum(item["topic"] == "same event" for item in selected), 1)

    def test_similar_scores_prefer_temporally_diverse_middle(self):
        ranked = [
            {"center": 100, "score": .95, "topic": "start", "reason": "start"},
            {"center": 350, "score": .92, "topic": "near", "reason": "near"},
            {"center": 700, "score": .90, "topic": "middle", "reason": "middle"},
            {"center": 1200, "score": .88, "topic": "end", "reason": "end"},
        ]
        selected = _select_ranked_ranges(ranked, 1440, 180)
        self.assertIn("middle", [item["topic"] for item in selected])

    @patch("long_video_selector.selector._contact_sheets", return_value=[])
    @patch("long_video_selector.selector._visual_candidates", return_value=([], []))
    @patch("long_video_selector.selector._extract_sampled_frames", return_value=([Path("frame.jpg")], [0.0]))
    def test_invalid_or_fewer_than_three_or_oom_fails_open(self, _extract, _visual, _sheets):
        cases = [
            ["invalid"],
            ["300,.9,only"],
            [MemoryError("OOM")],
        ]
        for index, responses in enumerate(cases):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as directory:
                result = run_long_video_selector(
                    "source.mp4", 1800, Path(directory) / "selection.json",
                    detector_factory=lambda responses=responses: FakeDetector(responses),
                )
                self.assertEqual(result["status"], "LONG_VIDEO_SELECTOR_SKIPPED")
                self.assertEqual(result["selected_ranges"], [])

    def test_selected_ranges_constrain_keep_without_rebasing_timestamps(self):
        keep = [{"start": 0, "end": 400}, {"start": 800, "end": 1200}]
        allowed = [{"start": 180, "end": 360}, {"start": 900, "end": 1080}]
        self.assertEqual(constrain_keep_intervals(keep, allowed), [
            {"start": 180.0, "end": 360.0}, {"start": 900.0, "end": 1080.0},
        ])

    @patch("production.pipeline.slice_analysis_wav", return_value=Path("slice.wav"))
    @patch("production.pipeline.analyze_audio")
    def test_scoped_silence_analysis_preserves_absolute_timestamps(self, analyze, _slice):
        def local_result(_audio, duration, **_options):
            metrics = {
                "silero_speech_duration": 10, "sensevoice_speech_duration": 10,
                "union_speech_duration": 10, "silero_interval_count": 1,
                "sensevoice_interval_count": 1, "union_interval_count": 1,
                "final_keep_duration": 10, "final_cut_duration": duration - 10,
                "sensevoice_model_load_time": 0, "sensevoice_inference_time": 1,
                "silero_processing_time": 1, "detector_wall_time": 1,
                "fusion_processing_time": 0, "timeline_processing_time": 0,
                "core_analysis_time": 1, "largest_sensevoice_asr_segment": 10,
                "largest_sensevoice_fine_speech_interval": 10,
            }
            intervals = [{"start": 5, "end": 15}]
            return ({
                "silero_intervals": intervals, "sensevoice_intervals": intervals,
                "union_intervals": intervals, "final_keep_intervals": intervals,
                "final_cut_intervals": [{"start": 15, "end": duration}],
                "metrics": metrics,
            }, [])
        analyze.side_effect = local_result
        result, _ = _analyze_allowed_ranges(
            Path("full.wav"), Path("work"),
            [{"start": 100, "end": 280}, {"start": 800, "end": 980}],
            config=Mock(sample_rate=16000, to_dict=lambda: {}), detector=Mock(),
        )
        self.assertEqual(result["final_keep_intervals"], [
            {"start": 105.0, "end": 115.0}, {"start": 805.0, "end": 815.0},
        ])
        self.assertEqual(analyze.call_count, 2)


if __name__ == "__main__":
    unittest.main()
