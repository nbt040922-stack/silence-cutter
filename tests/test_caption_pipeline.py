import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from caption_engine.__main__ import _benchmark_summary, _parser
from caption_engine.models import TranscriptSegment, TranscriptionResult, WordTimestamp
from caption_engine.pipeline import generate_captions


class CaptionPipelineTests(unittest.TestCase):
    def test_benchmark_flag_and_summary(self):
        self.assertTrue(_parser().parse_args(["input.mp4", "--benchmark"]).benchmark)
        result = {
            "language": "ja",
            "actual_device": "cuda",
            "actual_compute_type": "float16",
            "batch_size": 8,
            "model_initialization_cached": False,
            "audio_duration": 10.0,
            "audio_extraction_time": 1.0,
            "model_initialization_time": 2.0,
            "transcription_inference_time": 4.0,
            "caption_processing_time": 0.5,
            "output_write_time": 0.1,
            "total_processing_time": 7.6,
            "realtime_factor": 0.76,
            "x_realtime": 1.315,
            "word_count": 20,
            "caption_count": 4,
            "cuda_runtime": {
                "applicable": True,
                "available": True,
                "cublas_found": True,
                "cudnn_found": True,
            },
        }
        summary = _benchmark_summary(result, "large-v3-turbo")
        self.assertIn("Backend: CUDA / float16", summary)
        self.assertIn("Inference: 4.0 s", summary)
        self.assertIn("Words/tokens: 20", summary)
        self.assertIn("CUDA runtime: OK", summary)
        self.assertIn("cuBLAS 12: found", summary)
        self.assertIn("cuDNN 9: found", summary)

    @patch("caption_engine.pipeline.transcribe_audio")
    @patch("caption_engine.pipeline.detect_speech", return_value=[{"start": 0.0, "end": 6.0}])
    @patch("caption_engine.pipeline.extract_analysis_audio")
    @patch("caption_engine.pipeline.probe_media")
    def test_language_metadata_serialization(
        self, probe_media, extract_audio, _detect_speech, transcribe_audio
    ):
        probe_media.side_effect = [
            {"duration": 10.0, "has_audio": True},
            {"duration": 9.9, "has_audio": True},
        ]
        word = WordTimestamp("Xin", 0.0, 6.0, 0.98)
        transcribe_audio.return_value = TranscriptionResult(
            [TranscriptSegment(0.0, 6.0, "Xin", [word])],
            "vi",
            0.97,
            9.9,
            requested_device="cuda",
            requested_compute_type="float16",
            actual_device="cuda",
            actual_compute_type="float16",
            batch_enabled=True,
            batch_size=8,
            model_initialization_time=1.2,
            transcription_inference_time=2.3,
            cuda_runtime={
                "applicable": True,
                "available": True,
                "cublas_found": True,
                "cudnn_found": True,
                "runtime_source": "python_environment",
            },
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
        self.assertEqual(report["long_single_token_caption_count"], 1)
        self.assertEqual(report["actual_device"], "cuda")
        self.assertEqual(report["actual_compute_type"], "float16")
        self.assertEqual(report["model_initialization_time"], 1.2)
        self.assertEqual(report["transcription_inference_time"], 2.3)
        self.assertIn("audio_extraction_time", report)
        self.assertIn("output_write_time", report)
        self.assertEqual(report["coverage_diagnostics"]["speech_coverage_percentage"], 100.0)
        self.assertTrue(report["cuda_runtime"]["available"])
        _, kwargs = transcribe_audio.call_args
        self.assertEqual(kwargs["audio_duration"], 9.9)


if __name__ == "__main__":
    unittest.main()
