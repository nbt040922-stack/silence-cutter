import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from silence_cutter.audio import MediaProcessError
from silence_cutter.pipeline import cut_silence


class PipelineTests(unittest.TestCase):
    @patch(
        "silence_cutter.pipeline.build_timeline",
        return_value={
            "keep": [{"start": 0.0, "end": 3.0}, {"start": 6.0, "end": 10.0}],
            "cut": [{"start": 3.0, "end": 6.0}],
        },
    )
    @patch("silence_cutter.pipeline.render_video")
    @patch(
        "silence_cutter.pipeline.detect_speech",
        return_value=[{"start": 0.0, "end": 1.0}],
    )
    @patch("silence_cutter.pipeline.extract_analysis_audio")
    @patch("silence_cutter.pipeline.probe_media")
    def test_duration_accounting_uses_timeline(
        self,
        probe_media,
        extract_audio,
        _detect_speech,
        render_video,
        _build_timeline,
    ):
        probe_media.side_effect = [
            {"duration": 10.0, "has_audio": True},
            {"duration": 7.1, "has_audio": True},
        ]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.mp4"
            output = Path(directory) / "output.mp4"
            source.write_bytes(b"source")
            extract_audio.return_value = Path(directory) / "analysis.wav"

            result = cut_silence(source, output)
            report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))

        render_video.assert_called_once()
        self.assertEqual(report["timeline_removed_duration"], 3.0)
        self.assertEqual(report["removed_duration"], 3.0)
        self.assertEqual(report["expected_output_duration"], 7.0)
        self.assertEqual(report["actual_output_duration"], 7.1)
        self.assertAlmostEqual(report["duration_error"], 0.1)

    @patch("silence_cutter.pipeline.render_video")
    @patch("silence_cutter.pipeline.detect_speech", return_value=[])
    @patch("silence_cutter.pipeline.extract_analysis_audio")
    @patch("silence_cutter.pipeline.probe_media")
    def test_no_speech_preserves_source(
        self, probe_media, extract_audio, _detect_speech, render_video
    ):
        probe_media.return_value = {"duration": 10.0, "has_audio": True}
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.mp4"
            output = Path(directory) / "output.mp4"
            source.write_bytes(b"original video content")
            extract_audio.return_value = Path(directory) / "analysis.wav"

            result = cut_silence(source, output)
            report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))

            self.assertEqual(output.read_bytes(), source.read_bytes())
        render_video.assert_not_called()
        self.assertTrue(report["no_speech_detected"])
        self.assertEqual(report["keep_segments"], [{"start": 0.0, "end": 10.0}])
        self.assertEqual(report["cut_segments"], [])
        self.assertEqual(report["timeline_removed_duration"], 0.0)
        self.assertEqual(report["expected_output_duration"], 10.0)
        self.assertEqual(report["actual_output_duration"], 10.0)
        self.assertEqual(report["duration_error"], 0.0)

    @patch("silence_cutter.pipeline.extract_analysis_audio")
    @patch(
        "silence_cutter.pipeline.probe_media",
        return_value={"duration": 10.0, "has_audio": False},
    )
    def test_missing_audio_fails_before_extraction(self, _probe_media, extract_audio):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.mp4"
            source.write_bytes(b"video only")
            with self.assertRaisesRegex(
                MediaProcessError, "input media contains no audio stream"
            ):
                cut_silence(source, Path(directory) / "output.mp4")
        extract_audio.assert_not_called()


if __name__ == "__main__":
    unittest.main()
