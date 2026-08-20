import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from formatter.planner import (
    PART_BANNER, TITLE_BANNER, VIDEO_PLACEMENT,
    build_layout, center_crop_geometry, detect_title_language,
    fit_title, formatter_status, plan_done_job, plan_parts,
)


def segment(output_start, output_end, source_start, source_end):
    return {
        "output_start": output_start, "output_end": output_end,
        "source_start": source_start, "source_end": source_end,
    }


class FormatterTests(unittest.TestCase):
    def setUp(self):
        self.segments = [
            segment(0, 190, 0, 190),
            segment(190, 400, 191, 401),
            segment(400, 600, 411, 611),
            segment(600, 800, 612, 812),
        ]

    def test_exactly_three_unequal_parts_use_edit_junctions(self):
        parts, candidates = plan_parts(800, self.segments)
        self.assertEqual(len(parts), 3)
        self.assertNotEqual(len({round(item["duration"], 3) for item in parts}), 1)
        selected = [item for item in candidates if item["selected"]]
        self.assertEqual(len(selected), 2)
        self.assertTrue(all(item["type"] == "edit_junction" for item in selected))
        self.assertIn(400, [item["clean_timestamp"] for item in selected])

    def test_part_count_thresholds(self):
        expected = {
            540.0: 2,
            599.9: 2,
            600.0: 3,
            900.0: 3,
            1200.0: 3,
        }
        for duration, part_count in expected.items():
            with self.subTest(duration=duration):
                parts, _ = plan_parts(
                    duration, [segment(0, duration, 0, duration)]
                )
                self.assertEqual(len(parts), part_count)
        self.assertEqual(formatter_status(1200.1), "PLANNED")

    def test_two_part_plan_prefers_unequal_natural_boundary(self):
        parts, candidates = plan_parts(540, [
            segment(0, 220, 0, 220),
            segment(220, 540, 226, 546),
        ])
        self.assertEqual([item["duration"] for item in parts], [220, 320])
        selected = [item for item in candidates if item["selected"]]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["type"], "edit_junction")

    def test_two_part_localized_labels_stop_at_two(self):
        parts, _ = plan_parts(540, [segment(0, 540, 0, 540)])
        from formatter.planner import PART_LABELS
        for part in parts:
            part["label"] = PART_LABELS["ja"].format(number=part["index"])
        self.assertEqual([item["label"] for item in parts], ["パート1", "パート2"])

    def test_done_japanese_job_persists_two_part_count_and_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clean = root / "rendered.mp4"
            clean.write_bytes(b"clean")
            report = root / "pipeline_report.json"
            report.write_text(json.dumps({
                "output_duration": 540,
                "debug": {"render": {"segments": [
                    segment(0, 220, 0, 220), segment(220, 540, 226, 546),
                ]}},
            }), encoding="utf-8")
            (root / "job.json").write_text(json.dumps({
                "id": "short", "status": "DONE", "title": "日本語の動画",
                "report_path": str(report), "output_path": str(clean),
            }, ensure_ascii=False), encoding="utf-8")
            with (
                patch("formatter.planner.probe_video_geometry", return_value=(1920, 1080)),
                patch("formatter.preview.render_preview", return_value=root / "part1_preview.png"),
            ):
                plan = plan_done_job(
                    root, output_path=root / "format_plan.json",
                    preview_path=root / "part1_preview.png",
                )
        self.assertEqual(plan["part_count"], 2)
        self.assertEqual([part["label"] for part in plan["parts"]], ["パート1", "パート2"])

    def test_analysis_only_job_plans_directly_from_source_keep_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            report = root / "pipeline_report.json"
            keep = [{"start": 10, "end": 210}, {"start": 220, "end": 560}]
            report.write_text(json.dumps({
                "input_duration": 600,
                "output_duration": None,
                "expected_output_duration": 540,
                "keep_intervals": keep,
                "debug": {"keep_intervals": keep, "union_intervals": []},
            }), encoding="utf-8")
            (root / "job.json").write_text(json.dumps({
                "id": "direct", "status": "DONE", "title": "Direct",
                "source_path": str(source), "output_path": None,
                "report_path": str(report),
            }), encoding="utf-8")
            with (
                patch("formatter.planner.probe_video_geometry", return_value=(1920, 1080)),
                patch("formatter.preview.render_preview", return_value=root / "part1_preview.png"),
                patch("speech_detector.silero_detector.detect_with_silero") as silero,
                patch("speech_detector.sensevoice_detector.SenseVoiceDetector.detect") as sensevoice,
            ):
                plan = plan_done_job(
                    root, output_path=root / "format_plan.json",
                    preview_path=root / "part1_preview.png",
                )
        silero.assert_not_called()
        sensevoice.assert_not_called()
        self.assertTrue(plan["direct_source_render"])
        self.assertIsNone(plan["clean_video_path"])
        self.assertEqual(plan["source_video_path"], str(source))
        self.assertEqual(plan["clean_video_duration"], 540)
        self.assertEqual(plan["render_segments"], [
            {"output_start": 0, "output_end": 200, "source_start": 10, "source_end": 210},
            {"output_start": 200, "output_end": 540, "source_start": 220, "source_end": 560},
        ])

    def test_long_video_has_no_duration_only_review_gate(self):
        for duration in (720, 900, 1080, 1199, 1200):
            with self.subTest(duration=duration):
                self.assertEqual(formatter_status(duration), "PLANNED")
                first, second = duration / 3, duration * 2 / 3
                parts, _ = plan_parts(duration, [
                    segment(0, first, 0, first),
                    segment(first, second, first + 1, second + 1),
                    segment(second, duration, second + 2, duration + 2),
                ])
                self.assertEqual(len(parts), 3)
        for duration in (1200.1, 1800.0, 3600.0):
            self.assertEqual(formatter_status(duration), "PLANNED")
            self.assertEqual(formatter_status(duration, format_anyway=True), "PLANNED")

    def test_six_to_seven_minute_natural_parts_are_accepted(self):
        parts, candidates = plan_parts(1140, [
            segment(0, 380, 0, 380),
            segment(380, 780, 381, 781),
            segment(780, 1140, 782, 1142),
        ])
        self.assertEqual([item["duration"] for item in parts], [380, 400, 360])
        self.assertTrue(all(
            item["type"] == "edit_junction"
            for item in candidates if item["selected"]
        ))

    def test_balance_guard_prefers_reasonable_natural_plan(self):
        parts, _ = plan_parts(1140, [
            segment(0, 180, 0, 180),
            segment(180, 360, 190, 370),
            segment(360, 740, 371, 751),
            segment(740, 1140, 752, 1152),
        ])
        self.assertTrue(all(180 <= item["duration"] <= 420 for item in parts))

    def test_parts_cover_complete_timeline_without_gaps_or_overlaps(self):
        parts, _ = plan_parts(800, self.segments)
        self.assertEqual(parts[0]["clean_start"], 0)
        self.assertEqual(parts[-1]["clean_end"], 800)
        self.assertTrue(all(
            left["clean_end"] == right["clean_start"]
            for left, right in zip(parts, parts[1:])
        ))
        self.assertAlmostEqual(sum(item["duration"] for item in parts), 800)

    def test_larger_original_pause_scores_higher(self):
        _parts, candidates = plan_parts(800, self.segments)
        pause_ten = next(item for item in candidates if item["original_silence_duration"] == 10)
        pause_one = next(item for item in candidates if item["original_silence_duration"] == 1)
        self.assertGreater(pause_ten["score"], pause_one["score"])

    def test_existing_speech_pause_is_preferred_over_arbitrary_fallback(self):
        speech = [
            {"start": 0, "end": 255},
            {"start": 255.2, "end": 520},
            {"start": 520.3, "end": 800},
        ]
        parts, candidates = plan_parts(800, [segment(0, 800, 0, 800)], speech)
        selected = [item for item in candidates if item["selected"]]
        self.assertEqual(len(parts), 3)
        self.assertTrue(all(item["type"] == "detected_speech_pause" for item in selected))
        self.assertTrue(all(item["speech_ended_cleanly"] for item in selected))

    def test_center_crop_16_by_9_to_four_by_three_without_stretch(self):
        cases = {
            (1920, 1080): (1440, 1080, 240, 0),
            (3840, 2160): (2880, 2160, 480, 0),
            (2560, 1440): (1920, 1440, 320, 0),
        }
        for (source_width, source_height), (width, height, x, y) in cases.items():
            with self.subTest(source=(source_width, source_height)):
                crop = center_crop_geometry(source_width, source_height)
                self.assertEqual(
                    (crop["width"], crop["height"], crop["x"], crop["y"]),
                    (width, height, x, y),
                )
                self.assertEqual(crop["width"] / crop["height"], 4 / 3)
        self.assertEqual(VIDEO_PLACEMENT["width"] / VIDEO_PLACEMENT["height"], 4 / 3)

    def test_localized_part_labels(self):
        cases = {
            "家族で買い物に行きました": ("ja", "パート1"),
            "Family shopping vlog": ("en", "PART 1"),
            "가족과 함께 쇼핑": ("ko", "파트 1"),
            "Đi mua sắm với gia đình": ("vi", "PHẦN 1"),
            "Viaje por España y compras": ("es", "PARTE 1"),
            "Viagem para Portugal": ("pt", "PARTE 1"),
            "Voyage avec la famille": ("fr", "PARTIE 1"),
            "Reise mit der Familie": ("de", "TEIL 1"),
            "Viaggio e acquisti": ("it", "PARTE 1"),
            "家庭购物记录": ("zh-Hans", "第1部分"),
            "家庭購物記錄": ("zh-Hant", "第1部分"),
            "ZXQ 123": ("unknown", "PART 1"),
        }
        from formatter.planner import PART_LABELS
        for title, expected in cases.items():
            with self.subTest(title=title):
                language, confidence = detect_title_language(title)
                self.assertEqual(language, expected[0])
                self.assertGreaterEqual(confidence, 0 if language == "unknown" else 0.65)
                self.assertEqual(PART_LABELS[language].format(number=1), expected[1])

    def test_long_title_shrinks_or_wraps_and_short_title_stays_large(self):
        short = fit_title("Simple vlog", TITLE_BANNER)
        long = fit_title(
            "A detailed family shopping vlog with recipes updates and many useful ideas",
            TITLE_BANNER,
        )
        self.assertEqual(short["rendered_size_px"], 62)
        self.assertLessEqual(long["rendered_size_px"], short["rendered_size_px"])
        self.assertGreater(len(long["wrapped_lines"]), 1)
        self.assertLessEqual(len(long["wrapped_lines"]), 3)
        self.assertLessEqual(long["measured_width"], long["safe_text_width"])

    def test_content_fit_banner_geometry_and_vertical_spacing(self):
        short = fit_title("Simple vlog", TITLE_BANNER)
        short_layout = build_layout(short, "PART 1")
        title_banner = short_layout["title_banner_geometry"]
        part_banner = short_layout["part_banner_geometry"]
        video = short_layout["video_placement"]
        self.assertEqual(
            title_banner["width"],
            short["measured_width"] + TITLE_BANNER["horizontal_padding"] * 2,
        )
        self.assertEqual(
            title_banner["height"],
            len(short["wrapped_lines"]) * short["line_height"]
            + TITLE_BANNER["vertical_padding"] * 2,
        )
        self.assertEqual(title_banner["x"], (1080 - title_banner["width"]) // 2)
        self.assertEqual(part_banner["x"], (1080 - part_banner["width"]) // 2)
        self.assertLessEqual(title_banner["width"], 918)
        self.assertGreaterEqual(title_banner["x"], 81)
        self.assertGreaterEqual(video["y"], title_banner["y"] + title_banner["height"])
        self.assertGreaterEqual(part_banner["y"], video["y"] + video["height"])
        self.assertEqual((video["width"], video["height"]), (1080, 810))

    def test_complete_content_block_is_vertically_centered(self):
        title = fit_title("Simple vlog", TITLE_BANNER)
        layout = build_layout(title, "PART 1")
        title_banner = layout["title_banner_geometry"]
        part_banner = layout["part_banner_geometry"]
        video = layout["video_placement"]
        block = layout["content_block"]
        top_free_space = block["y"]
        bottom_free_space = 1920 - (part_banner["y"] + part_banner["height"])

        self.assertEqual(title_banner["y"], block["y"])
        self.assertEqual(video["y"], title_banner["y"] + title_banner["height"] + 52)
        self.assertEqual(part_banner["y"], video["y"] + video["height"] + 52)
        self.assertLessEqual(abs(top_free_space - bottom_free_space), 1)

    def test_three_line_title_uses_only_required_height(self):
        title = fit_title(
            "【弾丸！！】急遽思い立ってコストコへ 約１時間滞在の購入品はいつもと比べてどうだったのでしょうか",
            TITLE_BANNER,
        )
        self.assertEqual(len(title["wrapped_lines"]), 3)
        banner = build_layout(title, "パート1")["title_banner_geometry"]
        self.assertEqual(
            banner["height"],
            3 * title["line_height"] + TITLE_BANNER["vertical_padding"] * 2,
        )

    def test_part_banner_fits_localized_text(self):
        title = fit_title("家族で買い物に行きました", TITLE_BANNER)
        layout = build_layout(title, "パート1")
        banner = layout["part_banner_geometry"]
        measured = layout["part_label_font"]["measured_width"]
        self.assertEqual(
            banner["width"], measured + PART_BANNER["horizontal_padding"] * 2
        )

    def test_part_labels_are_exact(self):
        parts, _ = plan_parts(800, self.segments)
        self.assertEqual([item["label"] for item in parts], ["PART 1", "PART 2", "PART 3"])

    def test_done_job_reuses_report_and_never_invokes_detectors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clean = root / "rendered.mp4"
            clean.write_bytes(b"clean")
            report = root / "pipeline_report.json"
            report.write_text(json.dumps({
                "output_duration": 800,
                "debug": {
                    "render": {"segments": self.segments},
                    "union_intervals": [
                        {"start": 0, "end": 250},
                        {"start": 250.2, "end": 520},
                        {"start": 520.2, "end": 800},
                    ],
                },
            }), encoding="utf-8")
            job = root / "job.json"
            job.write_text(json.dumps({
                "id": "job", "status": "DONE", "title": "Source title",
                "report_path": str(report), "output_path": str(clean),
            }), encoding="utf-8")
            plan_path = root / "format_plan.json"
            preview_path = root / "part1_preview.png"
            with (
                patch("formatter.planner.probe_video_geometry", return_value=(1920, 1080)),
                patch("formatter.preview.render_preview", return_value=preview_path),
                patch("speech_detector.silero_detector.detect_with_silero") as silero,
                patch("speech_detector.sensevoice_detector.SenseVoiceDetector.detect") as sensevoice,
            ):
                plan = plan_done_job(
                    root, output_path=plan_path, preview_path=preview_path
                )
            silero.assert_not_called()
            sensevoice.assert_not_called()
            self.assertEqual(plan["title"]["source_title"], "Source title")
            self.assertEqual(plan["title"]["language"], "unknown")
            self.assertEqual(plan["part_label_template"], "PART {number}")
            self.assertEqual(plan["detector_reuse"], {
                "timeline_source": "pipeline_report.debug.render.segments",
                "speech_boundary_source": "pipeline_report.debug.union_intervals",
                "silero_invoked": False, "sensevoice_invoked": False, "asr_invoked": False,
            })
            self.assertTrue(plan_path.is_file())

    def test_long_done_job_is_planned_without_duration_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clean = root / "rendered.mp4"
            clean.write_bytes(b"clean")
            report = root / "pipeline_report.json"
            report.write_text(json.dumps({
                "output_duration": 1200.1,
                "debug": {"render": {"segments": [segment(0, 1200.1, 0, 1200.1)]}},
            }), encoding="utf-8")
            (root / "job.json").write_text(json.dumps({
                "id": "long", "status": "DONE", "title": "Family vlog",
                "report_path": str(report), "output_path": str(clean),
            }), encoding="utf-8")
            with (
                patch("formatter.planner.probe_video_geometry", return_value=(1920, 1080)),
                patch("formatter.preview.render_preview", return_value=root / "part1_preview.png"),
            ):
                plan = plan_done_job(
                    root, output_path=root / "format_plan.json",
                    preview_path=root / "part1_preview.png",
                )
            self.assertEqual(plan["formatter_status"], "PLANNED")
            self.assertEqual(len(plan["parts"]), 3)
            self.assertNotIn("auto_format_" + "max_duration", plan)
            self.assertNotIn("review_reason", plan)


if __name__ == "__main__":
    unittest.main()
