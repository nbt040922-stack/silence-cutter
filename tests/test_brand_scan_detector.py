import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from brand_scan.detector import BrandScanConfig, BrandScanDetector


class FakeQwen:
    def __init__(self, responses):
        self.responses = list(responses)
        self.generation_count = 0

    def generate_text(self, _images, _prompt, **_kwargs):
        self.generation_count += 1
        return self.responses.pop(0) if self.responses else "NONE"


class BrandScanDetectorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.source = Path(self.directory.name) / "source.mp4"
        self.source.write_bytes(b"media")

    def tearDown(self):
        self.directory.cleanup()

    @patch("brand_scan.detector._contact_sheets")
    @patch("brand_scan.detector._extract_sampled_frames")
    def test_qwen_candidate_is_fine_scanned_and_applied(self, extract, sheets):
        frame = Path(self.directory.name) / "frame.jpg"
        Image.new("RGB", (10, 10), "red").save(frame)
        extract.side_effect = [([frame], [10.0]), ([frame, frame], [9.0, 11.0])]
        sheets.side_effect = lambda *_args, **_kwargs: [Image.new("RGB", (10, 10))]
        qwen = FakeQwen(["SPONSOR,10,12,0.9", "SPONSOR,10,12,0.9"])
        detector = BrandScanDetector(qwen, qr_detector=lambda _frame: False)
        result = detector.scan(self.source, 30.0)
        self.assertEqual(result.status, "APPLIED")
        self.assertEqual(len(result.cut_intervals), 1)
        self.assertGreaterEqual(result.qwen_generation_count, 1)
        self.assertLessEqual(result.cut_intervals[0]["start"], 10.0)

    @patch("brand_scan.detector._contact_sheets", return_value=[])
    @patch("brand_scan.detector._extract_sampled_frames")
    def test_qr_only_evidence_is_applied(self, extract, _sheets):
        frame = Path(self.directory.name) / "frame.jpg"
        Image.new("RGB", (10, 10), "red").save(frame)
        extract.return_value = ([frame, frame], [4.0, 6.0])
        detector = BrandScanDetector(FakeQwen(["NONE"]), qr_detector=lambda _frame: True)
        result = detector.scan(self.source, 20.0)
        self.assertEqual(result.status, "APPLIED")
        self.assertEqual(result.detections[0].type, "QR")

    @patch("brand_scan.detector._extract_sampled_frames", side_effect=RuntimeError("decode failed"))
    def test_frame_failure_is_incomplete(self, _extract):
        result = BrandScanDetector(FakeQwen([]), qr_detector=lambda _frame: False).scan(self.source, 20.0)
        self.assertEqual(result.status, "BRAND_SCAN_INCOMPLETE")

    def test_config_reads_recall_first(self):
        config = BrandScanConfig.from_environment()
        self.assertTrue(config.recall_first)


if __name__ == "__main__":
    unittest.main()
