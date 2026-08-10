import unittest
from pathlib import Path
from unittest.mock import patch

from production.content_boundary import (
    BoundaryConfig, ContentWindow, EdgeFeatures, detect_content_window, score_edge,
)
from speech_detector.config import HighRecallConfig


def energy(before: float, after: float, boundary: float, end: float = 60):
    return tuple(
        (index / 2, before if index / 2 < boundary else after)
        for index in range(int(end * 2))
    )


def strong_intro(boundary: float, end: float = 120) -> EdgeFeatures:
    return EdgeFeatures(
        0, end,
        scene_changes=(boundary - 6, boundary - 3, boundary),
        energy=energy(0.12, 0.015, boundary, end),
    )


class ContentBoundaryTests(unittest.TestCase):
    def detect(
        self, intro, *, outro=None, duration=100, content_start=None,
        content_end=None, disabled=False,
    ):
        def visual(_video, start, end, _config):
            return EdgeFeatures(start, end)

        with (
            patch("production.content_boundary._visual_features", side_effect=visual),
            patch("production.content_boundary._audio_energy", return_value=()),
            patch(
                "production.content_boundary.score_edge",
                side_effect=[
                    (intro, 0.9 if intro is not None else 0.2, "intro", []),
                    (outro, 0.9 if outro is not None else 0.2, "outro", []),
                ],
            ),
        ):
            return detect_content_window(
                Path("video.mp4"), Path("audio.wav"), duration,
                content_start=content_start, content_end=content_end, disabled=disabled,
            )

    def test_obvious_static_end_card_is_trimmed(self):
        features = EdgeFeatures(
            60, 120, scene_changes=(80, 90), freezes=((90, 120),),
            energy=energy(0.05, 0.05, 90, 120),
        )
        timestamp, confidence, _reason, candidates = score_edge(
            features, intro=False, threshold=0.70
        )
        self.assertEqual(timestamp, 90)
        self.assertGreaterEqual(confidence, 0.70)
        candidate = next(item for item in candidates if item["timestamp"] == 90)
        self.assertEqual(candidate["visual_static_score"], 1.0)
        self.assertGreaterEqual(candidate["terminal_support"], 0.5)

    def test_scene_transition_into_outro_music_is_trimmed(self):
        features = EdgeFeatures(
            60, 120, scene_changes=(90,), energy=energy(0.02, 0.10, 90, 120)
        )
        self.assertEqual(score_edge(features, intro=False, threshold=0.70)[0], 90)

    def test_spoken_cta_with_closing_visual_is_trimmed(self):
        features = EdgeFeatures(
            60, 120, scene_changes=(90,), freezes=((90, 120),),
            energy=energy(0.06, 0.06, 90, 120),
        )
        self.assertEqual(score_edge(features, intro=False, threshold=0.70)[0], 90)

    def test_single_scene_transition_near_end_is_kept(self):
        features = EdgeFeatures(60, 120, scene_changes=(110,), energy=energy(0.05, 0.05, 90, 120))
        self.assertIsNone(score_edge(features, intro=False, threshold=0.70)[0])

    def test_normal_speech_continuing_to_eof_is_kept(self):
        features = EdgeFeatures(60, 120, energy=energy(0.06, 0.06, 90, 120))
        self.assertIsNone(score_edge(features, intro=False, threshold=0.70)[0])

    def test_short_silence_at_eof_is_not_outro(self):
        features = EdgeFeatures(60, 120, energy=energy(0.06, 0.0, 119, 120))
        self.assertIsNone(score_edge(features, intro=False, threshold=0.70)[0])

    def test_black_terminal_card_with_static_support_is_trimmed(self):
        features = EdgeFeatures(
            60, 120, freezes=((90, 120),), black_frames=((90, 120),),
            energy=energy(0.02, 0.0, 90, 120),
        )
        self.assertEqual(score_edge(features, intro=False, threshold=0.70)[0], 90)

    def test_weak_outro_candidate_is_kept(self):
        features = EdgeFeatures(
            60, 120, scene_changes=(90,), energy=energy(0.05, 0.04, 90, 120)
        )
        self.assertIsNone(score_edge(features, intro=False, threshold=0.70)[0])

    def test_manual_content_end_is_exact(self):
        window, report = self.detect(10, outro=90, duration=100, content_end=87.25)
        self.assertEqual(window.end, 87.25)
        self.assertEqual(report["content_end"], 87.25)

    def test_keep_intro_outro_bypasses_outro(self):
        window, report = self.detect(10, outro=90, duration=100, disabled=True)
        self.assertEqual(window.end, 100)
        self.assertIsNone(report["detected_outro_boundary"])

    def test_post_intro_trim_applies_after_detected_boundary(self):
        window, report = self.detect(26.5)
        self.assertEqual(window.start, 28.5)
        self.assertEqual(report["detected_intro_boundary"], 26.5)
        self.assertEqual(report["post_intro_trim"], 2.0)
        self.assertEqual(report["final_content_start"], 28.5)

    def test_no_detected_intro_does_not_apply_post_trim(self):
        self.assertEqual(self.detect(None)[0].start, 0)

    def test_manual_content_start_bypasses_post_trim(self):
        self.assertEqual(self.detect(26.5, content_start=12.25)[0].start, 12.25)

    def test_disabled_detection_bypasses_post_trim(self):
        self.assertEqual(self.detect(26.5, disabled=True)[0].start, 0)

    def test_post_intro_trim_is_clamped_before_content_end(self):
        window, _report = self.detect(29, duration=30)
        self.assertGreater(window.start, 29)
        self.assertLessEqual(window.start, window.end)

    def test_tight2_defaults_are_unchanged(self):
        config = HighRecallConfig()
        self.assertEqual(
            (config.speech_pad_before, config.speech_pad_after,
             config.merge_gap, config.min_silence_duration),
            (0.0, 0.0, 0.15, 0.50),
        )

    def test_early_scene_audio_beats_later_weak_stability(self):
        features = EdgeFeatures(
            0, 60, scene_changes=(20, 22, 26.5, 33.25),
            energy=energy(0.12, 0.015, 26.5),
        )
        timestamp, _confidence, _reason, candidates = score_edge(
            features, intro=True, threshold=0.70
        )
        self.assertEqual(timestamp, 26.5)
        early = next(item for item in candidates if item["timestamp"] == 26.5)
        late = next(item for item in candidates if item["timestamp"] == 33.25)
        self.assertGreater(early["branding_sting_end_score"], 0)
        self.assertGreater(late["lateness_penalty"], 0)

    def test_earliest_valid_branding_sting_wins_nearby_candidates(self):
        features = EdgeFeatures(
            0, 60, scene_changes=(21, 24, 26.5, 28.25),
            energy=energy(0.14, 0.012, 26.5),
        )
        self.assertEqual(score_edge(features, intro=True, threshold=0.70)[0], 26.5)

    def test_strong_later_candidate_can_beat_weak_early_candidate(self):
        features = EdgeFeatures(
            0, 60,
            scene_changes=(10, 16),
            freezes=((14, 16),),
            black_frames=((15, 16),),
            energy=energy(0.11, 0.015, 10),
        )
        timestamp, _confidence, _reason, _candidates = score_edge(
            features, intro=True, threshold=0.70
        )
        self.assertEqual(timestamp, 16)

    def test_no_structural_evidence_preserves_full_edges(self):
        features = EdgeFeatures(0, 60, energy=energy(0.1, 0.1, 30))
        timestamp, confidence, reason, candidates = score_edge(
            features, intro=True, threshold=0.70
        )
        self.assertIsNone(timestamp)
        self.assertEqual(confidence, 0)
        self.assertEqual(reason, "insufficient evidence")
        self.assertEqual(candidates, [])

    def test_clear_eight_second_intro_scores_strong_boundary(self):
        features = EdgeFeatures(
            0, 60, scene_changes=(1, 3, 5, 8), energy=energy(0.03, 0.15, 8)
        )
        timestamp, confidence, _reason, _candidates = score_edge(
            features, intro=True, threshold=0.70
        )
        self.assertEqual(timestamp, 8)
        self.assertGreaterEqual(confidence, 0.70)

    def test_intro_search_default_is_120_seconds(self):
        self.assertEqual(BoundaryConfig().intro_search_window, 120.0)

    def test_continuous_main_content_for_120_seconds_keeps_start(self):
        features = EdgeFeatures(0, 120, energy=energy(0.1, 0.1, 60, 120))
        self.assertIsNone(score_edge(features, intro=True, threshold=0.70)[0])

    def test_supported_intro_boundaries_across_120_second_window(self):
        for boundary in (20, 45, 75, 110):
            with self.subTest(boundary=boundary):
                timestamp, confidence, _reason, _candidates = score_edge(
                    strong_intro(boundary), intro=True, threshold=0.70
                )
                self.assertEqual(timestamp, boundary)
                self.assertGreaterEqual(confidence, 0.70)

    def test_weak_random_scene_change_at_90_seconds_is_rejected(self):
        features = EdgeFeatures(
            0, 120, scene_changes=(90,), energy=energy(0.1, 0.1, 90, 120)
        )
        self.assertIsNone(score_edge(features, intro=True, threshold=0.70)[0])

    def test_short_video_intro_scan_is_clamped_to_duration(self):
        inspected = []

        def visual(_video, start, end, _config):
            inspected.append((start, end))
            return EdgeFeatures(start, end)

        with (
            patch("production.content_boundary._visual_features", side_effect=visual),
            patch("production.content_boundary._audio_energy", return_value=()),
        ):
            window, report = detect_content_window(
                Path("video.mp4"), Path("audio.wav"), 80
            )
        self.assertEqual(window.start, 0)
        self.assertEqual(report["intro_search_window"], 120.0)
        self.assertIn((0.0, 80), inspected)

    def test_clear_outro_scores_start_of_static_end_card(self):
        features = EdgeFeatures(
            60, 120, scene_changes=(86, 89, 92), freezes=((92, 120),),
            energy=energy(0.14, 0.04, 92, 120),
        )
        timestamp, confidence, _reason, _candidates = score_edge(
            features, intro=False, threshold=0.70
        )
        self.assertEqual(timestamp, 92)
        self.assertGreaterEqual(confidence, 0.70)

    def test_low_confidence_candidates_are_kept(self):
        intro = EdgeFeatures(0, 60, scene_changes=(8,), energy=energy(0.1, 0.1, 8))
        outro = EdgeFeatures(60, 120, scene_changes=(112,), energy=energy(0.1, 0.1, 112, 120))
        self.assertIsNone(score_edge(intro, intro=True, threshold=0.70)[0])
        self.assertIsNone(score_edge(outro, intro=False, threshold=0.70)[0])

    def test_content_window_rejects_non_monotonic_bounds(self):
        with self.assertRaises(ValueError):
            ContentWindow(8, 8, 8, 0, 1, 1, "manual", "manual")


if __name__ == "__main__":
    unittest.main()
