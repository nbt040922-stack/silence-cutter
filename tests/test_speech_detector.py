import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, call, patch

from speech_detector.config import HighRecallConfig
from speech_detector.fusion import (
    build_keep_cut, fully_covered, normalize_intervals, subtract_intervals,
    union_intervals,
)
from speech_detector.models import SpeechInterval
from speech_detector.pipeline import known_gap_metrics
from speech_detector.silero_detector import detect_with_silero
from speech_detector.sensevoice_detector import SenseVoiceDetector


def interval(start, end, source="silero"):
    return SpeechInterval(start, end, source)


class SpeechFusionTests(unittest.TestCase):
    def test_sensevoice_cuda_load_failure_falls_back_to_cpu_only(self):
        cpu_model = Mock()
        detector = SenseVoiceDetector(HighRecallConfig())
        with patch.object(
            detector, "_create_model",
            side_effect=[RuntimeError("CUDA out of memory"), cpu_model],
        ) as create:
            self.assertIs(detector._load(), cpu_model)
        self.assertEqual(create.call_args_list, [call("cuda:0"), call("cpu")])
        self.assertEqual(detector.active_device, "cpu")
        self.assertTrue(detector.cuda_fallback)
        self.assertIn("out of memory", detector.cuda_error)

    def test_sensevoice_cuda_inference_failure_retries_on_cpu(self):
        cuda_model = Mock()
        cuda_model.vad_model = object()
        cuda_model.vad_kwargs = {}
        cuda_model.inference.side_effect = RuntimeError("CUDA runtime failure")
        cpu_model = Mock()
        cpu_model.vad_model = object()
        cpu_model.vad_kwargs = {}
        cpu_model.inference.return_value = [{"value": [[100, 800]]}]
        detector = SenseVoiceDetector(HighRecallConfig())
        detector._model = cuda_model
        with patch.object(detector, "_create_model", return_value=cpu_model) as create:
            intervals, _elapsed, diagnostics = detector.detect(Path("audio.wav"), 1)
        create.assert_called_once_with("cpu")
        self.assertEqual([(item.start, item.end) for item in intervals], [(0.1, 0.8)])
        self.assertEqual(diagnostics["sensevoice_active_device"], "cpu")
        self.assertTrue(diagnostics["sensevoice_cuda_fallback"])

    def test_sensevoice_uses_fine_vad_without_redundant_asr(self):
        model = unittest.mock.Mock()
        model.vad_model = object()
        model.vad_kwargs = {}
        model.inference.return_value = [
            {"value": [[0, 1000], [1700, 2500], [3700, 5000]]}
        ]
        detector = SenseVoiceDetector(HighRecallConfig())
        detector._model = model

        fine, _elapsed, diagnostics = detector.detect(Path("analysis.wav"), 5)

        self.assertEqual(
            [(item.start, item.end) for item in fine],
            [(0, 1), (1.7, 2.5), (3.7, 5)],
        )
        model.generate.assert_not_called()
        self.assertEqual(diagnostics["sensevoice_raw_asr_segment_count"], 0)
        self.assertEqual(diagnostics["sensevoice_fine_speech_interval_count"], 3)
        self.assertEqual(diagnostics["largest_sensevoice_asr_segment"], 0)
        self.assertAlmostEqual(
            diagnostics["largest_sensevoice_fine_speech_interval"], 1.3
        )
        self.assertEqual(model.inference.call_args.kwargs["max_end_silence_time"], 200)
        timeline = build_keep_cut(fine, 5, HighRecallConfig())
        self.assertIn({"start": 1, "end": 1.7}, timeline["cut"])
        self.assertIn({"start": 2.5, "end": 3.7}, timeline["cut"])

    def test_known_gap_hit_and_strict_edge_coverage_are_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gaps.json"
            path.write_text(json.dumps({"gaps": [{"start": 1, "end": 2}]}))
            metrics = known_gap_metrics(
                path,
                [interval(1, 1.97)],
                [],
                [{"start": 1, "end": 1.97}],
            )
        self.assertEqual(metrics["protected_by_union_count"], 1)
        self.assertEqual(metrics["fully_protected_by_union_count"], 0)
        self.assertEqual(metrics["partially_protected_by_union_count"], 1)
        self.assertEqual(metrics["still_unprotected_count"], 0)

    def test_known_gap_accounting_uses_effective_content_window(self):
        gaps = {
            "gaps": [
                {"start": 0.5, "end": 1.0},
                {"start": 2.0, "end": 3.0},
                {"start": 4.0, "end": 5.0},
                {"start": 9.0, "end": 10.0},
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gaps.json"
            path.write_text(json.dumps(gaps))
            metrics = known_gap_metrics(
                path,
                [],
                [],
                [{"start": 2.5, "end": 8.0}],
                content_start=2.5,
                content_end=8.0,
            )
        self.assertEqual(metrics["known_gap_count_total"], 4)
        self.assertEqual(metrics["known_gap_count_inside_content"], 2)
        self.assertEqual(metrics["known_gap_count_removed_by_intro"], 1)
        self.assertEqual(metrics["known_gap_count_removed_by_outro"], 1)
        self.assertEqual(metrics["protected_inside_content"], 2)
        self.assertEqual(metrics["fully_protected_inside_content"], 2)
        self.assertEqual(metrics["still_unprotected_inside_content"], 0)

    @patch("speech_detector.silero_detector.detect_speech", return_value=[])
    def test_silero_wrapper_does_not_add_timing_padding(self, detect):
        detect_with_silero("analysis.wav", sample_rate=16_000, threshold=0.5)
        detect.assert_called_once_with(
            "analysis.wav", sample_rate=16_000, threshold=0.5
        )

    def test_tight_production_defaults(self):
        config = HighRecallConfig()
        self.assertEqual(config.speech_pad_before, 0)
        self.assertEqual(config.speech_pad_after, 0)
        self.assertEqual(config.merge_gap, 0.15)
        self.assertEqual(config.min_silence_duration, 0.50)
        self.assertEqual(config.min_keep_duration, 0)

    def test_detector_positive_intervals_survive_union_and_overlap_merges(self):
        silero = [interval(1, 3), interval(6, 8)]
        sensevoice = [interval(2.8, 4.5, "sensevoice"), interval(9, 10, "sensevoice")]
        union = union_intervals(silero, sensevoice, 12)
        self.assertEqual(
            [(item.start, item.end) for item in union],
            [(1, 4.5), (6, 8), (9, 10)],
        )
        for item in [*silero, *sensevoice]:
            self.assertTrue(fully_covered(item.start, item.end, union))

    def test_padding_short_gap_merge_long_gap_and_complete_timeline(self):
        config = HighRecallConfig(
            speech_pad_before=0.25, speech_pad_after=0.30,
            merge_gap=0.35, min_silence_duration=0,
        )
        union = [interval(1, 2, "union"), interval(2.2, 3, "union"), interval(4.5, 5, "union")]
        timeline = build_keep_cut(union, 6, config)
        self.assertEqual(timeline["keep"], [
            {"start": 0.75, "end": 3.3}, {"start": 4.25, "end": 5.3}
        ])
        all_items = sorted(timeline["keep"] + timeline["cut"], key=lambda item: item["start"])
        self.assertEqual(all_items[0]["start"], 0)
        self.assertEqual(all_items[-1]["end"], 6)
        self.assertTrue(all(a["end"] == b["start"] for a, b in zip(all_items, all_items[1:])))
        self.assertTrue(all(item["end"] > item["start"] for item in all_items))
        for cut in timeline["cut"]:
            self.assertFalse(any(cut["start"] < speech.end and speech.start < cut["end"] for speech in union))

    def test_clamping_monotonicity_and_subtraction(self):
        normalized = normalize_intervals(
            [interval(8, 12), interval(2, 4), interval(3, 5)], 10, "union"
        )
        self.assertEqual([(item.start, item.end) for item in normalized], [(2, 5), (8, 10)])
        remaining = subtract_intervals(
            [interval(1, 5)], [interval(2, 4, "sensevoice")], "silero_only"
        )
        self.assertEqual([(item.start, item.end) for item in remaining], [(1, 2), (4, 5)])
        self.assertTrue(all(item.start >= 0 and item.end > item.start for item in normalized + remaining))

    def test_invalid_interval_is_rejected(self):
        with self.assertRaises(ValueError):
            interval(1, 1)

    def test_neither_detector_marks_full_duration_as_cut_candidate(self):
        timeline = build_keep_cut([], 10, HighRecallConfig())
        self.assertEqual(timeline["keep"], [])
        self.assertEqual(timeline["cut"], [{"start": 0.0, "end": 10}])

    def test_zero_padding_and_gap_merge_boundary(self):
        config = HighRecallConfig(min_silence_duration=0)
        merged = build_keep_cut(
            [interval(1, 2), interval(2.10, 3)], 4, config
        )
        separate = build_keep_cut(
            [interval(1, 2), interval(2.20, 3)], 4, config
        )
        self.assertEqual(merged["keep"], [{"start": 1, "end": 3}])
        self.assertEqual(
            separate["keep"],
            [{"start": 1, "end": 2}, {"start": 2.2, "end": 3}],
        )

    def test_silence_cut_threshold_is_inclusive(self):
        config = HighRecallConfig()
        below = build_keep_cut([interval(0, 1), interval(1.49, 2)], 2, config)
        boundary = build_keep_cut([interval(0, 1), interval(1.50, 2)], 2, config)
        long = build_keep_cut([interval(0, 1), interval(2, 3)], 3, config)
        self.assertEqual(below["keep"], [{"start": 0.0, "end": 2}])
        self.assertEqual(boundary["cut"], [{"start": 1, "end": 1.5}])
        self.assertEqual(long["cut"], [{"start": 1, "end": 2}])

    def test_tight_timeline_preserves_speech_and_partitions_duration(self):
        speech = [interval(0.2, 0.3), interval(1, 2), interval(2.7, 3)]
        timeline = build_keep_cut(speech, 4, HighRecallConfig())
        keeps, cuts = timeline["keep"], timeline["cut"]
        for item in speech:
            self.assertTrue(
                any(keep["start"] <= item.start and keep["end"] >= item.end for keep in keeps)
            )
        all_items = sorted(keeps + cuts, key=lambda item: item["start"])
        self.assertEqual(all_items[0]["start"], 0)
        self.assertEqual(all_items[-1]["end"], 4)
        self.assertTrue(all(a["end"] == b["start"] for a, b in zip(all_items, all_items[1:])))


if __name__ == "__main__":
    unittest.main()
