import json
import tempfile
import unittest
from pathlib import Path

from brand_scan.models import BrandScanResult, Detection
from brand_scan.pipeline import run_brand_scan


class FakeDetector:
    def __init__(self, result):
        self.result = result

    def scan(self, _source, _duration):
        return self.result


class BrandScanPipelineTests(unittest.TestCase):
    def test_applied_scan_subtracts_brand_interval_from_keep(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            report = root / "pipeline_report.json"
            artifact = root / "brand_ad_scan.json"
            source.write_bytes(b"media")
            report.write_text(json.dumps({
                "input_duration": 20.0,
                "keep_intervals": [{"start": 0.0, "end": 20.0}],
                "expected_output_duration": 20.0,
                "total_removed_duration": 0.0,
            }), encoding="utf-8")
            result = BrandScanResult(
                "APPLIED", [Detection("SPONSOR", 5, 8, 0.9, ("qwen",), "banner")],
                [{"start": 5.0, "end": 8.0}], config={"recall_first": True},
            )
            payload = run_brand_scan(source, report, artifact, FakeDetector(result))
            updated = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "APPLIED")
            self.assertEqual(updated["keep_intervals"], [{"start": 0.0, "end": 5.0}, {"start": 8.0, "end": 20.0}])
            self.assertEqual(updated["brand_removed_duration"], 3.0)

    def test_incomplete_scan_does_not_modify_keep_and_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, report, artifact = root / "source.mp4", root / "report.json", root / "brand.json"
            source.write_bytes(b"media")
            original = {"input_duration": 10.0, "keep_intervals": [{"start": 0.0, "end": 10.0}]}
            report.write_text(json.dumps(original), encoding="utf-8")
            result = BrandScanResult("BRAND_SCAN_INCOMPLETE", [], [], reason="worker down")
            payload = run_brand_scan(source, report, artifact, FakeDetector(result))
            self.assertEqual(payload["status"], "BRAND_SCAN_INCOMPLETE")
            updated = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(updated["keep_intervals"], original["keep_intervals"])
            self.assertEqual(updated["brand_scan_status"], "BRAND_SCAN_INCOMPLETE")


if __name__ == "__main__":
    unittest.main()
