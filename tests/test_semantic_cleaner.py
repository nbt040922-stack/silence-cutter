import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from semantic_cleaner.cleaner import (
    SemanticCleanerConfig,
    apply_semantic_cleaner,
    subtract_intervals,
)
from semantic_cleaner.qwen import (
    QwenSemanticDetector,
    _align_to_visual_transitions,
    _candidate_windows,
    _contact_sheets,
    _json_object,
    _semantic_response,
    _visual_candidates,
    _windows,
)


class SemanticCleanerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"source-must-remain-untouched")
        self.report_path = self.root / "pipeline_report.json"
        self.output_path = self.root / "semantic_segments.json"

    def tearDown(self):
        self.directory.cleanup()

    def write_report(self, keep=None):
        keep = keep or [{"start": 0.0, "end": 100.0}]
        report = {
            "input_duration": 100.0,
            "expected_output_duration": sum(item["end"] - item["start"] for item in keep),
            "keep_duration": sum(item["end"] - item["start"] for item in keep),
            "total_removed_duration": 100 - sum(item["end"] - item["start"] for item in keep),
            "keep_intervals": keep,
            "cut_intervals": [],
            "debug": {"keep_intervals": keep, "union_intervals": keep, "render": {}},
        }
        self.report_path.write_text(json.dumps(report), encoding="utf-8")
        return report

    def apply(self, segments, keep=None):
        self.write_report(keep)
        return apply_semantic_cleaner(
            self.source, self.report_path, self.output_path,
            detector=lambda _source, _duration: {
                "model": "fake-qwen", "segments": segments,
                "model_load_time": 1.0, "semantic_scan_time": 2.0,
                "peak_vram_bytes": 3,
            },
            config=SemanticCleanerConfig(threshold=0.85, snap_tolerance=0.0),
        )

    @staticmethod
    def segment(label, start, end, confidence=0.95):
        return {
            "type": label, "start": start, "end": end,
            "confidence": confidence, "reason": "test",
        }

    def final_keep(self):
        return json.loads(self.report_path.read_text(encoding="utf-8"))["keep_intervals"]

    def test_intro_ad_and_outro_subtraction(self):
        for label, interval, expected in (
            ("INTRO", (0, 10), [{"start": 10, "end": 100}]),
            ("AD", (40, 50), [{"start": 0, "end": 40}, {"start": 50, "end": 100}]),
            ("OUTRO", (90, 100), [{"start": 0, "end": 90}]),
        ):
            with self.subTest(label=label):
                self.apply([self.segment(label, *interval)])
                self.assertEqual(self.final_keep(), expected)

    def test_multiple_and_overlapping_semantic_intervals(self):
        self.apply([
            self.segment("INTRO", 0, 10), self.segment("AD", 30, 50),
            self.segment("AD", 45, 60), self.segment("OUTRO", 90, 100),
        ])
        self.assertEqual(self.final_keep(), [
            {"start": 10, "end": 30}, {"start": 60, "end": 90},
        ])

    def test_semantic_overlap_with_existing_cut_only_removes_keep(self):
        keep = [{"start": 0.0, "end": 10.0}, {"start": 20.0, "end": 30.0}]
        artifact = self.apply([self.segment("AD", 5, 25)], keep)
        self.assertEqual(self.final_keep(), [
            {"start": 0.0, "end": 5.0}, {"start": 25.0, "end": 30.0},
        ])
        self.assertEqual(artifact["removed_duration"], 10.0)

    def test_below_threshold_and_content_remain_keep(self):
        artifact = self.apply([
            self.segment("AD", 10, 20, 0.84), self.segment("CONTENT", 30, 40),
        ])
        self.assertEqual(self.final_keep(), [{"start": 0.0, "end": 100.0}])
        self.assertEqual(len(artifact["kept_uncertain_segments"]), 2)

    def test_invalid_timestamps_are_ignored(self):
        artifact = self.apply([
            self.segment("AD", -1, 5), self.segment("AD", 20, 10),
            self.segment("OUTRO", 95, 101),
        ])
        self.assertEqual(self.final_keep(), [{"start": 0.0, "end": 100.0}])
        self.assertEqual(len(artifact["invalid_segments"]), 3)

    def test_approximate_interval_without_safe_end_is_kept(self):
        self.write_report()
        artifact = apply_semantic_cleaner(
            self.source, self.report_path, self.output_path,
            detector=lambda _source, _duration: {
                "segments": [self.segment("AD", 28, 36)],
            },
            config=SemanticCleanerConfig(threshold=0.85, snap_tolerance=10.0),
        )
        self.assertEqual(self.final_keep(), [{"start": 0.0, "end": 100.0}])
        self.assertEqual(len(artifact["invalid_segments"]), 1)

    def test_sampled_ad_bounds_snap_forward_to_safe_boundaries(self):
        report = self.write_report()
        report["debug"]["union_intervals"] = [
            {"start": 0.0, "end": 20.0}, {"start": 30.0, "end": 100.0},
        ]
        self.report_path.write_text(json.dumps(report), encoding="utf-8")
        artifact = apply_semantic_cleaner(
            self.source, self.report_path, self.output_path,
            detector=lambda _source, _duration: {
                "segments": [self.segment("AD", 18.75, 25.0, 0.9)],
            },
            config=SemanticCleanerConfig(threshold=0.85, snap_tolerance=10.0),
        )
        self.assertEqual(artifact["removed_segments"][0]["start"], 20.0)
        self.assertEqual(artifact["removed_segments"][0]["end"], 30.0)

    def test_qwen_failure_falls_back_to_original_keep(self):
        original = self.write_report()

        def fail(_source, _duration):
            raise RuntimeError("CUDA OOM")

        artifact = apply_semantic_cleaner(
            self.source, self.report_path, self.output_path, detector=fail,
        )
        self.assertEqual(artifact["status"], "SEMANTIC_CLEANER_SKIPPED")
        self.assertEqual(
            json.loads(self.report_path.read_text(encoding="utf-8")), original,
        )

    def test_formatter_mapping_is_valid_and_no_intermediate_video_is_created(self):
        before = self.source.read_bytes()
        self.apply([self.segment("AD", 40, 50)])
        report = json.loads(self.report_path.read_text(encoding="utf-8"))
        mapping = report["debug"]["render"]["segments"]
        self.assertEqual(mapping, [
            {"output_start": 0.0, "output_end": 40.0, "source_start": 0.0, "source_end": 40.0},
            {"output_start": 40.0, "output_end": 90.0, "source_start": 50.0, "source_end": 100.0},
        ])
        self.assertEqual(report["expected_output_duration"], 90.0)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(list(self.root.glob("*.mp4")), [self.source])

    def test_stale_rendered_output_duration_is_removed(self):
        report = self.write_report()
        report["output_duration"] = 100.05
        self.report_path.write_text(json.dumps(report), encoding="utf-8")
        apply_semantic_cleaner(
            self.source, self.report_path, self.output_path,
            detector=lambda *_: {"segments": []},
        )
        self.assertNotIn("output_duration", json.loads(self.report_path.read_text()))

    def test_subtraction_preserves_sorted_non_overlapping_timeline(self):
        result = subtract_intervals(
            [{"start": 0, "end": 20}],
            [{"start": 12, "end": 18}, {"start": 5, "end": 10}],
        )
        self.assertEqual(result, [
            {"start": 0.0, "end": 5}, {"start": 10, "end": 12},
            {"start": 18, "end": 20.0},
        ])

    def test_qwen_parser_accepts_wrapped_object_and_fenced_list(self):
        segment = self.segment("AD", 10, 20)
        self.assertEqual(_json_object(json.dumps({"segments": [segment]}))["segments"], [segment])
        self.assertEqual(
            _json_object(f"```json\n{json.dumps([segment])}\n```")["segments"], [segment],
        )

    def test_qwen_parser_recovers_required_fields_from_malformed_reason(self):
        malformed = (
            '{"segments":[{"type":"AD","start":20,"end":30,'
            '"confidence":0.96,"reason":"use code "CLEAN30" now"}]}'
        )
        segment = _json_object(malformed)["segments"][0]
        self.assertEqual(
            {key: segment[key] for key in ("type", "start", "end", "confidence")},
            {"type": "AD", "start": "20", "end": "30", "confidence": "0.96"},
        )

    def test_compact_semantic_response_keeps_absolute_timestamps(self):
        self.assertEqual(_semantic_response("AD,315.0,350.5,0.91"), [{
            "type": "AD", "start": 315.0, "end": 350.5,
            "confidence": 0.91, "reason": "Qwen visual semantic evidence",
        }])
        self.assertEqual(_semantic_response("NONE"), [])

    def test_scan_windows_cover_intro_outro_and_full_video_ads(self):
        windows = _windows(200)
        self.assertIn(("INTRO", 0.0, 90.0), windows)
        self.assertIn(("OUTRO", 80.0, 200), windows)
        ads = [item for item in windows if item[0] == "AD"]
        self.assertEqual((ads[0][1], ads[-1][2]), (0.0, 200))
        self.assertTrue(all(right[1] <= left[2] for left, right in zip(ads, ads[1:])))

    def test_contact_sheet_preserves_absolute_timestamp_labels(self):
        frame = self.root / "frame.jpg"
        Image.new("RGB", (640, 360), "red").save(frame)
        sheets = _contact_sheets([frame], [315.0])
        try:
            self.assertEqual(sheets[0].size, (960, 150))
            self.assertIsNotNone(sheets[0].crop((0, 0, 240, 28)).getbbox())
        finally:
            sheets[0].close()

    def test_candidate_windows_remain_absolute_and_merge_context(self):
        candidates = _candidate_windows([
            self.segment("AD", 315, 330, 0.8),
            self.segment("AD", 340, 350, 0.7),
            self.segment("CONTENT", 500, 510, 1.0),
        ], 900)
        self.assertEqual(candidates, [(300.0, 365.0)])

    def test_fine_boundaries_align_to_measured_visual_transitions(self):
        segment = self.segment("AD", 16, 32, 0.9)
        coarse = [
            {"start": 10, "end": 30}, {"start": 20, "end": 40},
        ]
        self.assertEqual(
            _align_to_visual_transitions([segment], coarse, 10)[0],
            self.segment("AD", 20, 30, 0.9),
        )

    def _fake_detector(self, responses):
        detector = object.__new__(QwenSemanticDetector)
        detector.model_reference = "fake"
        detector.model_load_time = 1.0
        detector.generation_count = 0
        detector.torch = type("Torch", (), {
            "OutOfMemoryError": RuntimeError,
            "cuda": type("Cuda", (), {
                "empty_cache": staticmethod(lambda: None),
                "max_memory_allocated": staticmethod(lambda: 3),
                "memory_allocated": staticmethod(lambda: 2),
                "memory_reserved": staticmethod(lambda: 4),
            }),
        })

        def classify(_images, _prompt):
            detector.generation_count += 1
            return responses.pop(0)

        detector._classify = classify
        return detector

    @patch("semantic_cleaner.qwen._visual_candidates", return_value=([], []))
    @patch("semantic_cleaner.qwen._contact_sheets")
    @patch("semantic_cleaner.qwen._extract_sampled_frames")
    def test_no_candidate_skips_generation_and_fine(self, extract, sheets, _candidates):
        extract.return_value = ([Path("fake")], [0.0])
        sheets.return_value = [Image.new("RGB", (10, 10))]
        detector = self._fake_detector([])
        result = detector.detect(self.source, 100)
        self.assertEqual(result["generation_count"], 0)
        self.assertEqual(result["candidate_count"], result["fine_frame_count"])
        self.assertEqual(extract.call_count, 1)

    @patch("semantic_cleaner.qwen._visual_candidates", return_value=([{"type": "VISUAL_CANDIDATE", "start": 35, "end": 55}], [(25, 65)]))
    @patch("semantic_cleaner.qwen._contact_sheets")
    @patch("semantic_cleaner.qwen._extract_sampled_frames")
    def test_only_candidates_are_fine_scanned_with_same_model(self, extract, sheets, _candidates):
        extract.return_value = ([Path("fake")], [0.0])
        sheets.side_effect = lambda *_args, **_kwargs: [Image.new("RGB", (10, 10))]
        ad = self.segment("AD", 40, 50, 0.9)
        detector = self._fake_detector([[ad]])
        result = detector.detect(self.source, 100)
        self.assertEqual(result["generation_count"], 1)
        self.assertEqual(result["candidate_windows"], [{"start": 25.0, "end": 65.0}])
        self.assertEqual(extract.call_count, 2)

    def test_batch_oom_falls_back_to_single_sheets(self):
        detector = self._fake_detector([])
        calls = []

        def classify(images, _prompt):
            calls.append(len(images))
            if len(images) > 1:
                raise RuntimeError("OOM")
            return []

        detector._classify = classify
        images = [Image.new("RGB", (10, 10)) for _ in range(2)]
        try:
            self.assertEqual(detector._classify_with_oom_fallback(images, "prompt"), [])
            self.assertEqual(calls, [2, 1, 1])
        finally:
            for image in images:
                image.close()

    @patch("semantic_cleaner.qwen._visual_candidates", return_value=([{"type": "VISUAL_CANDIDATE", "start": 980, "end": 1000}], [(965, 1000)]))
    @patch("semantic_cleaner.qwen._contact_sheets")
    @patch("semantic_cleaner.qwen._extract_sampled_frames")
    def test_final_120_seconds_ad_false_positive_is_kept(self, extract, sheets, _candidates):
        extract.return_value = ([Path("fake")], [975.0])
        sheets.return_value = [Image.new("RGB", (10, 10))]
        detector = self._fake_detector([[self.segment("AD", 975, 987, 0.95)]])
        result = detector.detect(self.source, 1000)
        self.assertEqual(result["segments"], [])

    @patch("semantic_cleaner.qwen._visual_candidates", return_value=([{"type": "VISUAL_CANDIDATE", "start": 480, "end": 520}], [(465, 535)]))
    @patch("semantic_cleaner.qwen._contact_sheets")
    @patch("semantic_cleaner.qwen._extract_sampled_frames")
    def test_intro_outro_labels_stay_in_specialized_regions(self, extract, sheets, _candidates):
        extract.return_value = ([Path("fake")], [500.0])
        sheets.return_value = [Image.new("RGB", (10, 10))]
        detector = self._fake_detector([[
            self.segment("INTRO", 480, 500, 0.95),
            self.segment("OUTRO", 500, 520, 0.95),
        ]])
        self.assertEqual(detector.detect(self.source, 1000)["segments"], [])


if __name__ == "__main__":
    unittest.main()
