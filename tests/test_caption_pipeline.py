import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from caption_engine.models import TranscriptSegment, TranscriptionResult, WordTimestamp
from caption_engine.pipeline import generate_captions


class CaptionPipelineTests(unittest.TestCase):
    @patch("caption_engine.pipeline.transcribe_audio")
    @patch("caption_engine.pipeline.extract_analysis_audio")
    @patch("caption_engine.pipeline.probe_media")
    def test_language_metadata_serialization(
        self, probe_media, extract_audio, transcribe_audio
    ):
        probe_media.side_effect = [
            {"duration": 10.0, "has_audio": True},
            {"duration": 9.9, "has_audio": True},
        ]
        word = WordTimestamp("Xin", 0.0, 1.0, 0.98)
        transcribe_audio.return_value = TranscriptionResult(
            [TranscriptSegment(0.0, 1.0, "Xin", [word])], "vi", 0.97, 9.9
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.mp4"
            source.write_bytes(b"media")
            extract_audio.return_value = Path(directory) / "analysis.wav"

            result = generate_captions(source)
            report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))

            self.assertTrue(Path(result["srt_path"]).is_file())
        self.assertEqual(report["language"], "vi")
        self.assertEqual(report["language_probability"], 0.97)
        self.assertEqual(report["word_count"], 1)
        self.assertEqual(report["caption_count"], 1)
        _, kwargs = transcribe_audio.call_args
        self.assertEqual(kwargs["audio_duration"], 9.9)


if __name__ == "__main__":
    unittest.main()
