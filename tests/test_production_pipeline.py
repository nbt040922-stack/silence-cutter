import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from production.pipeline import (
    BrandingTailConfig, ProductionRuntime, VisualSafetyConfig,
    _apply_intro_greeting_heuristic, _fuse_intro_boundaries,
    _apply_post_intro_visual_trim, _branding_tail_enabled,
    _remove_intro_branding_tail,
)
from production.content_boundary import ContentWindow
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
        "known_gap_count_total": 18,
        "known_gap_count_inside_content": 18,
        "known_gap_count_removed_by_intro": 0,
        "known_gap_count_removed_by_outro": 0,
        "protected_inside_content": 18,
        "fully_protected_inside_content": 18,
        "partially_protected_inside_content": 0,
        "still_unprotected_inside_content": 0,
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
    def test_intro_fusion_policy(self):
        cases = [
            (28.0, 0.9, 7.0, True, 28.0, "structural_later_than_greeting"),
            (None, 0.4, 8.0, True, 8.0, "greeting_fallback"),
            (25.0, 0.9, None, False, 25.0, "structural"),
            (None, 0.2, None, False, None, "none"),
            (5.0, 0.9, 8.0, True, 8.0, "greeting_later_than_structural"),
        ]
        for structural, confidence, greeting, valid, final, source in cases:
            with self.subTest(structural=structural, greeting=greeting):
                fusion = _fuse_intro_boundaries(
                    structural, confidence, greeting, valid
                )
                self.assertEqual(fusion["final_boundary"], final)
                self.assertEqual(fusion["selected_source"], source)

    def test_two_short_greetings_trim_at_second_speech_end(self):
        speech = [
            {"start": 1.0, "end": 3.0},
            {"start": 5.0, "end": 8.0},
            {"start": 10.0, "end": 20.0},
        ]
        keep, removed, debug, fusion = _apply_intro_greeting_heuristic(
            speech, speech, 0.0, enabled=True, detected_intro_boundary=None,
            structural_confidence=0.0,
        )
        self.assertEqual(keep, [speech[2]])
        self.assertEqual(debug["proposed_boundary"], 8.0)
        self.assertTrue(debug["applied"])
        self.assertEqual(fusion["selected_source"], "greeting_fallback")
        self.assertEqual(len(removed), 2)

    def test_second_greeting_ending_at_25_seconds_is_allowed(self):
        speech = [
            {"start": 1.0, "end": 4.0},
            {"start": 20.0, "end": 25.0},
            {"start": 27.0, "end": 40.0},
        ]
        keep, _removed, debug, _fusion = _apply_intro_greeting_heuristic(
            speech, speech, 0.0, enabled=True, detected_intro_boundary=None,
            structural_confidence=0.0,
        )
        self.assertTrue(debug["applied"])
        self.assertEqual(keep[0]["start"], 27.0)

    def test_second_greeting_after_30_seconds_is_not_applied(self):
        speech = [
            {"start": 1.0, "end": 4.0},
            {"start": 25.0, "end": 31.0},
            {"start": 33.0, "end": 40.0},
        ]
        keep, removed, debug, _fusion = _apply_intro_greeting_heuristic(
            speech, speech, 0.0, enabled=True, detected_intro_boundary=None,
            structural_confidence=0.0,
        )
        self.assertEqual(keep, speech)
        self.assertEqual(removed, [])
        self.assertFalse(debug["applied"])
        self.assertIn("after 30-second", debug["reason"])

    def test_greeting_boundary_never_lands_inside_fused_speech(self):
        speech = [
            {"start": 1.0, "end": 4.0},
            {"start": 5.0, "end": 9.0},
            {"start": 9.5, "end": 20.0},
        ]
        keep, _removed, debug, _fusion = _apply_intro_greeting_heuristic(
            [{"start": 1.0, "end": 20.0}], speech, 0.0,
            enabled=True, detected_intro_boundary=None, structural_confidence=0.0,
        )
        boundary = debug["proposed_boundary"]
        self.assertEqual(boundary, 9.0)
        self.assertFalse(any(item["start"] < boundary < item["end"] for item in speech))
        self.assertEqual(keep[0]["start"], boundary)

    def test_confident_structural_intro_is_preserved(self):
        speech = [
            {"start": 1.0, "end": 3.0},
            {"start": 5.0, "end": 8.0},
            {"start": 10.0, "end": 20.0},
        ]
        keep, removed, debug, fusion = _apply_intro_greeting_heuristic(
            speech, speech, 0.0, enabled=True, detected_intro_boundary=26.5,
            structural_confidence=0.9,
        )
        self.assertEqual(keep, speech)
        self.assertEqual(removed, [])
        self.assertFalse(debug["applied"])
        self.assertEqual(fusion["final_boundary"], 26.5)
        self.assertEqual(fusion["selected_source"], "structural_later_than_greeting")

    @patch("production.pipeline.SenseVoiceDetector")
    @patch("production.pipeline.analyze_audio")
    def test_greeting_helper_invokes_no_detector_or_asr(self, analyze, sensevoice):
        _apply_intro_greeting_heuristic(
            [{"start": 1.0, "end": 8.0}],
            [{"start": 1.0, "end": 3.0}, {"start": 5.0, "end": 8.0}],
            0.0, enabled=True, detected_intro_boundary=None, structural_confidence=0.0,
        )
        analyze.assert_not_called()
        sensevoice.assert_not_called()

    def test_detected_intro_applies_visual_safety_trim(self):
        keep = [{"start": 3.0, "end": 10.0}]
        final, removed, clean_start = _apply_post_intro_visual_trim(
            keep, 10.0, enabled=True, config=VisualSafetyConfig()
        )
        self.assertEqual(clean_start, 3.0)
        self.assertEqual(final, [{"start": 3.3, "end": 10.0}])
        self.assertEqual(
            removed,
            [{"start": 3.0, "end": 3.3, "reason": "intro_visual_safety"}],
        )

    def test_no_intro_does_not_apply_visual_safety_trim(self):
        keep = [{"start": 3.0, "end": 10.0}]
        self.assertEqual(
            _apply_post_intro_visual_trim(
                keep, 10.0, enabled=False, config=VisualSafetyConfig()
            ),
            (keep, [], 3.0),
        )

    def test_manual_content_start_remains_exact_without_visual_trim(self):
        self.assertFalse(_branding_tail_enabled(2.0, 3.0, False))
        keep = [{"start": 3.0, "end": 10.0}]
        self.assertEqual(
            _apply_post_intro_visual_trim(
                keep, 10.0,
                enabled=_branding_tail_enabled(2.0, 3.0, False),
                config=VisualSafetyConfig(),
            )[0],
            keep,
        )

    def test_keep_intro_outro_bypasses_visual_trim(self):
        self.assertFalse(_branding_tail_enabled(2.0, None, True))

    def test_short_branding_burst_before_silence_and_sustained_content_is_removed(self):
        keep = [{"start": 3.0, "end": 4.0}, {"start": 5.0, "end": 12.0}]
        final, removed = _remove_intro_branding_tail(
            keep, 2.0, enabled=True, config=BrandingTailConfig()
        )
        self.assertEqual(final, keep[1:])
        self.assertEqual(
            removed,
            [{"start": 3.0, "end": 4.0, "reason": "intro_branding_tail"}],
        )

    def test_short_pause_does_not_remove_branding_candidate(self):
        keep = [{"start": 3.0, "end": 4.0}, {"start": 4.2, "end": 12.0}]
        self.assertEqual(
            _remove_intro_branding_tail(
                keep, 2.0, enabled=True, config=BrandingTailConfig()
            ),
            (keep, []),
        )

    def test_following_short_burst_does_not_remove_first_burst(self):
        keep = [{"start": 3.0, "end": 4.0}, {"start": 5.0, "end": 6.0}]
        self.assertEqual(
            _remove_intro_branding_tail(
                keep, 2.0, enabled=True, config=BrandingTailConfig()
            ),
            (keep, []),
        )

    def test_sustained_first_speech_is_kept(self):
        keep = [{"start": 3.0, "end": 7.0}, {"start": 8.0, "end": 12.0}]
        self.assertEqual(
            _remove_intro_branding_tail(
                keep, 2.0, enabled=True, config=BrandingTailConfig()
            ),
            (keep, []),
        )

    def test_no_intro_disables_branding_tail_removal(self):
        self.assertFalse(_branding_tail_enabled(None, None, False))

    def test_manual_content_start_disables_branding_tail_removal(self):
        self.assertFalse(_branding_tail_enabled(2.0, 3.0, False))

    def test_keep_intro_outro_disables_branding_tail_removal(self):
        self.assertFalse(_branding_tail_enabled(2.0, None, True))

    @patch("production.pipeline.render_video")
    @patch("production.pipeline.detect_content_window")
    @patch("production.pipeline.analyze_audio")
    @patch("production.pipeline.extract_analysis_audio")
    @patch("production.pipeline.probe_media")
    def test_content_window_offsets_tight_timeline_and_cut_reasons(
        self, probe, extract, analyze, detect_boundary, render
    ):
        probe.side_effect = [
            {"duration": 10.0, "has_audio": True},
            {"duration": 5.0, "has_audio": True},
        ]
        detect_boundary.return_value = (
            ContentWindow(2, 8, 2, 2, 0.9, 0.9, "intro", "outro"),
            {
                "boundary_analysis_time": 0.1,
                "intro_boundary_time": 0.05,
                "outro_boundary_time": 0.05,
                "detected_intro_boundary": 2.0,
                "detected_outro_boundary": 8.0,
                "post_intro_trim": 0.0,
            },
        )
        data, disagreements = analysis()
        data["final_keep_intervals"] = [{"start": 0.0, "end": 3.0}]
        data["final_cut_intervals"] = [{"start": 3.0, "end": 6.0}]
        data["silero_intervals"] = [{"start": 0.0, "end": 3.0}]
        data["sensevoice_intervals"] = []
        analyze.return_value = data, disagreements
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.mp4"
            output = Path(directory) / "output.mp4"
            source.write_bytes(b"media")
            audio = Path(directory) / "analysis.wav"
            extract.return_value = audio
            with patch("production.pipeline.slice_analysis_wav", return_value=audio):
                result = ProductionRuntime(detector=Mock(loaded=True)).process(source, output)
        render.assert_called_once_with(
            source.resolve(), output.resolve(), [{"start": 2.3, "end": 5.0}]
        )
        self.assertEqual(
            result["cut_segments"],
            [
                {"start": 0.0, "end": 2, "reason": "intro"},
                {"start": 2.0, "end": 2.3, "reason": "intro_visual_safety"},
                {"start": 5.0, "end": 8.0, "reason": "silence"},
                {"start": 8, "end": 10.0, "reason": "outro"},
            ],
        )
        self.assertEqual(result["intro_removed_duration"], 2)
        self.assertEqual(result["outro_removed_duration"], 2)
        self.assertEqual(result["silence_removed_duration"], 3)
        self.assertAlmostEqual(result["visual_safety_removed_duration"], 0.3)
        self.assertFalse(result["intro_greeting_heuristic"]["applied"])
        self.assertIn("structural", result["intro_greeting_heuristic"]["reason"])

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
        self.assertEqual(
            result["cut_intervals"],
            [item | {"reason": "silence"} for item in analysis()[0]["final_cut_intervals"]],
        )

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
