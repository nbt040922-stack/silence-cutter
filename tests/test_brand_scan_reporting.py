import json
import tempfile
import unittest
from pathlib import Path

from brand_scan.models import BrandScanResult
from brand_scan.pipeline import run_brand_scan


class _Detector:
    def __init__(self, result):
        self.result = result

    def scan(self, *_args):
        return self.result


class BrandScanReportingTests(unittest.TestCase):
    def test_artifact_has_stable_status_and_timing_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, report, artifact = root / "source.mp4", root / "report.json", root / "brand_ad_scan.json"
            source.write_bytes(b"media")
            report.write_text(json.dumps({"input_duration": 10.0, "keep_intervals": [{"start": 0, "end": 10}]}), encoding="utf-8")
            result = run_brand_scan(source, report, artifact, _Detector(BrandScanResult("NO_CANDIDATES", [], [], config={"recall_first": True})))
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "NO_CANDIDATES")
            for key in ("scan_version", "recall_first", "detections", "cut_intervals", "removed_duration", "total_scan_time"):
                self.assertIn(key, payload)
            self.assertEqual(payload["recall_first"], True)


if __name__ == "__main__":
    unittest.main()
