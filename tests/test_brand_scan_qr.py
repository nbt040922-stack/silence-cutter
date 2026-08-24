import unittest

from brand_scan.evidence import combine_detector_evidence, group_frame_evidence
from brand_scan.models import Detection
from brand_scan.qr import QRDetectorUnavailable, detect_qr


class BrandScanQrTests(unittest.TestCase):
    def test_consecutive_frame_evidence_is_grouped_with_padding(self):
        frames = [
            {"timestamp": 10.0, "type": "SPONSOR", "confidence": 0.8, "reason": "logo"},
            {"timestamp": 12.0, "type": "SPONSOR", "confidence": 0.9, "reason": "logo"},
        ]
        result = group_frame_evidence(frames, 20.0, min_consecutive=2, padding=0.5)
        self.assertEqual((result[0].start, result[0].end), (9.5, 12.5))

    def test_single_frame_is_not_accepted_without_qr_support(self):
        result = group_frame_evidence([
            {"timestamp": 10.0, "type": "SPONSOR", "confidence": 0.9},
        ], 20.0, min_consecutive=2, padding=0.5)
        self.assertEqual(result, [])

    def test_qr_support_keeps_medium_confidence_candidate(self):
        qwen = [Detection("QR", 10, 12, 0.55, ("qwen",), "possible QR")]
        qr = [Detection("QR", 10.2, 11.8, 1.0, ("qr",), "decoded")]
        result = combine_detector_evidence(qwen, qr, 20.0)
        self.assertEqual(len(result), 1)
        self.assertIn("qwen", result[0].detectors)
        self.assertIn("qr", result[0].detectors)

    def test_qr_detector_has_explicit_unavailable_error(self):
        try:
            result = detect_qr(None)
        except QRDetectorUnavailable:
            return
        self.assertIsInstance(result, bool)


if __name__ == "__main__":
    unittest.main()
