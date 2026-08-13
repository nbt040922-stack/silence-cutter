import json
import tempfile
import unittest
from pathlib import Path

from semantic_cleaner.cleaner import (
    SemanticCleanerConfig,
    apply_semantic_cleaner,
    subtract_intervals,
)
from semantic_cleaner.qwen import _json_object, _windows


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

    def test_scan_windows_cover_intro_outro_and_full_video_ads(self):
        windows = _windows(200)
        self.assertIn(("INTRO", 0.0, 90.0), windows)
        self.assertIn(("OUTRO", 80.0, 200), windows)
        ads = [item for item in windows if item[0] == "AD"]
        self.assertEqual((ads[0][1], ads[-1][2]), (0.0, 200))
        self.assertTrue(all(right[1] <= left[2] for left, right in zip(ads, ads[1:])))


if __name__ == "__main__":
    unittest.main()
