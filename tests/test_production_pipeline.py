import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from production.pipeline import ProductionRuntime
from speech_detector.config import HighRecallConfig
from speech_detector.sensevoice_detector import SenseVoiceDetector


def analysis():
    metrics = {
        "silero_speech_duration": 2.0, "sensevoice_speech_duration": 2.0,
        "union_speech_duration": 3.0, "final_keep_duration": 3.5,
        "final_cut_duration": 6.5, "removed_percentage": 65.0,
        "silero_interval_count": 1, "sensevoice_interval_count": 1,
        "sensevoice_raw_asr_segment_count": 1,
        "sensevoice_raw_asr_segment_duration": 4.0,
        "sensevoice_fine_speech_interval_count": 2,
        "sensevoice_fine_speech_duration": 2.0,
        "largest_sensevoice_asr_segment": 4.0,
        "largest_sensevoice_fine_speech_interval": 1.0,
        "union_interval_count": 2,
        "sensevoice_model_load_time": 1.0, "sensevoice_inference_time": 0.5,
        "silero_processing_time": 0.4, "detector_wall_time": 1.5,
        "fusion_processing_time": 0.01, "timeline_processing_time": 0.01,
        "warm_model": False, "parallel_detectors": True,
        "known_whisper_gap_count": 18, "protected_by_silero_count": 17,
        "protected_by_sensevoice_count": 17, "protected_by_union_count": 18,
        "still_unprotected_count": 0,
    }
    return {
        "metrics": metrics,
        "silero_intervals": [{"start": 1, "end": 3}],
        "sensevoice_intervals": [{"start": 2, "end": 4}],
        "union_intervals": [{"start": 1, "end": 4}],
        "final_keep_intervals": [{"start": 0.75, "end": 4.25}],
        "final_cut_intervals": [{"start": 4.25, "end": 10}],
    }, []


class ProductionPipelineTests(unittest.TestCase):
    @patch("production.pipeline.render_video")
    @patch("production.pipeline.analyze_audio", return_value=analysis())
    @patch("production.pipeline.extract_analysis_audio")
    @patch("production.pipeline.probe_media")
    def test_default_renders_without_transcription_or_srt(
        self, probe, extract, _analyze, render
    ):
        probe.side_effect = [
            {"duration": 10.0, "has_audio": True},
            {"duration": 3.5, "has_audio": True},
        ]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.mp4"
            output = Path(directory) / "output.mp4"
            source.write_bytes(b"media")
            extract.return_value = Path(directory) / "analysis.wav"
            imported = []
            real_import = __import__

            def guarded(name, *args, **kwargs):
                imported.append(name)
                if name.startswith(("faster_whisper", "caption_engine")):
                    raise AssertionError(f"production imported {name}")
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=guarded):
                result = ProductionRuntime(detector=Mock(loaded=False)).process(source, output)
            report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
            self.assertFalse(list(Path(directory).glob("*.srt")))
        render.assert_called_once()
        self.assertNotIn("debug", report)
        self.assertEqual(report["still_unprotected_count"], 0)
        self.assertFalse(any(name.startswith("faster_whisper") for name in imported))

    @patch("production.pipeline.analyze_audio", return_value=analysis())
    @patch("production.pipeline.extract_analysis_audio")
    @patch("production.pipeline.probe_media", return_value={"duration": 10.0, "has_audio": True})
    def test_analysis_only_reuses_detector_and_does_not_render(
        self, _probe, extract, analyze
    ):
        detector = Mock(loaded=True)
        runtime = ProductionRuntime(HighRecallConfig(), detector)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.mp4"
            source.write_bytes(b"media")
            extract.return_value = Path(directory) / "analysis.wav"
            with patch("production.pipeline.render_video") as render:
                for index in range(2):
                    result = runtime.process(
                        source, Path(directory) / f"output-{index}.mp4", analysis_only=True
                    )
            self.assertFalse(list(Path(directory).glob("*.srt")))
        self.assertEqual(analyze.call_count, 2)
        self.assertTrue(all(call.kwargs["sensevoice_detector"] is detector for call in analyze.call_args_list))
        render.assert_not_called()
        self.assertEqual(result["keep_intervals"], analysis()[0]["final_keep_intervals"])
        self.assertEqual(result["cut_intervals"], analysis()[0]["final_cut_intervals"])

    def test_sensevoice_small_model_is_loaded_once(self):
        factory = Mock(return_value=object())
        detector = SenseVoiceDetector(HighRecallConfig())
        with patch.dict(sys.modules, {"funasr": SimpleNamespace(AutoModel=factory)}):
            self.assertIs(detector._load(), detector._load())
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(factory.call_args.kwargs["model"], "iic/SenseVoiceSmall")
        self.assertNotIn("Nano", factory.call_args.kwargs["model"])

    @patch("production.pipeline.shutil.copy2")
    @patch("production.pipeline.analyze_audio")
    @patch("production.pipeline.extract_analysis_audio")
    @patch("production.pipeline.probe_media")
    def test_no_speech_preserves_source_without_render(
        self, probe, extract, analyze, copy
    ):
        empty, _ = analysis()
        empty["final_keep_intervals"] = []
        empty["final_cut_intervals"] = [{"start": 0.0, "end": 10.0}]
        empty["metrics"] |= {
            "final_keep_duration": 0.0,
            "final_cut_duration": 10.0,
            "removed_percentage": 100.0,
        }
        analyze.return_value = empty, []
        probe.side_effect = [
            {"duration": 10.0, "has_audio": True},
            {"duration": 10.0, "has_audio": True},
        ]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.mp4"
            output = Path(directory) / "output.mp4"
            source.write_bytes(b"media")
            extract.return_value = Path(directory) / "analysis.wav"
            with patch("production.pipeline.render_video") as render:
                result = ProductionRuntime(detector=Mock(loaded=True)).process(source, output)
        copy.assert_called_once_with(source.resolve(), output.resolve())
        render.assert_not_called()
        self.assertTrue(result["no_speech_detected"])


if __name__ == "__main__":
    unittest.main()
