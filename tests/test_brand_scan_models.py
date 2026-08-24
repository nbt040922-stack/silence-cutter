import unittest

from brand_scan.intervals import expand_temporal_evidence, merge_intervals
from brand_scan.models import BrandScanResult, Detection


class BrandScanModelTests(unittest.TestCase):
    def test_detection_validates_and_serializes(self):
        detection = Detection("QR", 2.0, 4.0, 0.75, ("qwen", "qr"), "visible code")
        self.assertEqual(detection.to_dict()["type"], "QR")
        self.assertEqual(detection.to_dict()["detectors"], ["qwen", "qr"])

    def test_detection_rejects_invalid_values(self):
        for args in (("UNKNOWN", 0, 1, 0.9, (), ""), ("QR", -1, 1, 0.9, (), ""),
                     ("QR", 2, 1, 0.9, (), ""), ("QR", 0, 1, 1.1, (), "")):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    Detection(*args)

    def test_merge_intervals_pads_clamps_and_merges(self):
        self.assertEqual(
            merge_intervals([{"start": 1, "end": 3}, {"start": 3.1, "end": 5}], 0.2, 5),
            [{"start": 0.8, "end": 5.0}],
        )

    def test_expand_evidence_clamps_source_bounds(self):
        detection = Detection("SPONSOR", 1, 2, 0.9, ("qwen",), "banner")
        expanded = expand_temporal_evidence([detection], 2.5, 0.5, 1.0)
        self.assertEqual((expanded[0].start, expanded[0].end), (0.5, 2.5))

    def test_result_serializes_report_fields(self):
        result = BrandScanResult("NO_CANDIDATES", [], [], 1.2, 0.3, 0.0, 0.0, 0.0)
        payload = result.to_dict()
        self.assertEqual(payload["status"], "NO_CANDIDATES")
        self.assertIn("removed_duration", payload)


if __name__ == "__main__":
    unittest.main()
