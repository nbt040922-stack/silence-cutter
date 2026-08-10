import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import hardware
from silence_cutter.runtime_paths import find_executable


class HardwareTests(unittest.TestCase):
    def test_bundled_executable_precedes_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "bin" / "ffmpeg.exe"
            executable.parent.mkdir()
            executable.write_bytes(b"exe")
            with patch.dict("os.environ", {"SILENCE_CUTTER_RESOURCE_DIR": str(root)}):
                self.assertEqual(find_executable("ffmpeg"), str(executable))

    def test_timeline_hash_is_stable_for_key_order(self):
        first = {
            "detected_intro_boundary": 2.0,
            "detected_outro_boundary": 8.0,
            "debug": {
                "keep_intervals": [{"start": 2.123456789, "end": 8.0}],
                "cut_intervals": [], "silero_intervals": [],
                "sensevoice_intervals": [],
            },
        }
        second = json.loads(json.dumps(first, sort_keys=True))
        self.assertEqual(
            hardware.timeline_identity(first)[0], hardware.timeline_identity(second)[0]
        )

    def test_performance_class_uses_measurement_not_gpu_name(self):
        self.assertEqual(hardware._classification(20, 60), "FAST")
        self.assertEqual(hardware._classification(40, 60), "STANDARD")
        self.assertEqual(hardware._classification(70, 60), "SLOW")

    def test_compare_requires_same_video_and_timeline(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / "a.json", Path(directory) / "b.json"]
            for path in paths:
                path.write_text(json.dumps({
                    "timeline_hash": "timeline", "benchmark_video_sha256": "video"
                }), encoding="utf-8")
            self.assertEqual(hardware.compare_reports(paths)["timeline_comparison"], "PASS")
            paths[1].write_text(json.dumps({
                "timeline_hash": "changed", "benchmark_video_sha256": "video"
            }), encoding="utf-8")
            self.assertEqual(hardware.compare_reports(paths)["timeline_comparison"], "FAIL")


if __name__ == "__main__":
    unittest.main()
