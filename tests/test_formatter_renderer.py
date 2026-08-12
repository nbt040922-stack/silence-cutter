import hashlib
import json
import math
import subprocess
import tempfile
import unittest
from array import array
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from formatter.planner import build_layout, center_crop_geometry, fit_title
from formatter.preview import render_overlay
from formatter.renderer import (
    FormatterProgress, _command, _progress_seconds, _select_cuda_decoder, build_render_jobs,
    map_clean_range_to_source, render_format_plan,
)
from silence_cutter.audio import MediaProcessError
from silence_cutter.runtime_paths import find_executable


def make_plan(root: Path, duration: float = 3.0, part_count: int = 3) -> dict:
    title = fit_title("日本語のテスト動画", {"y": 100, "max_width": 918, "horizontal_padding": 36, "vertical_padding": 24, "radius": 36})
    layout = build_layout(title, "パート1")
    layout["crop_geometry"] = center_crop_geometry(320, 180)
    parts = [{
        "index": index, "label": f"パート{index}",
        "clean_start": duration * (index - 1) / part_count,
        "clean_end": duration * index / part_count,
        "duration": duration / part_count,
    } for index in range(1, part_count + 1)]
    return {
        "schema_version": 2, "formatter_status": "PLANNED",
        "clean_video_path": str(root / "rendered.mp4"),
        "clean_video_duration": duration, "part_count": part_count, "parts": parts,
        "layout": layout, "title": title,
        "source_job_path": str(root / "job.json"),
        "detector_reuse": {"silero_invoked": False, "sensevoice_invoked": False, "asr_invoked": False},
    }


class FormatterRendererTests(unittest.TestCase):
    def test_plan_creates_exact_three_contiguous_jobs_with_exact_timestamps(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = make_plan(Path(directory))
            jobs = build_render_jobs(plan, Path(directory) / "formatted")
        self.assertEqual(len(jobs), 3)
        self.assertEqual([(item["start"], item["end"]) for item in jobs], [(0, 1), (1, 2), (2, 3)])
        self.assertEqual([item["label"] for item in jobs], ["パート1", "パート2", "パート3"])

    def test_renderer_creates_exactly_configured_two_parts(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = build_render_jobs(
                make_plan(Path(directory), duration=4, part_count=2),
                Path(directory) / "formatted",
            )
        self.assertEqual(len(jobs), 2)
        self.assertEqual([item["path"].name for item in jobs], ["PART_1.mp4", "PART_2.mp4"])

    def test_direct_mapping_preserves_keep_and_excludes_intro_silence_outro(self):
        mapping = [
            {"output_start": 0, "output_end": 2, "source_start": 10, "source_end": 12},
            {"output_start": 2, "output_end": 5, "source_start": 15, "source_end": 18},
        ]
        segments = map_clean_range_to_source(1, 4, mapping)
        self.assertEqual(segments, [
            {"start": 11, "end": 12},
            {"start": 15, "end": 17},
        ])
        self.assertEqual(sum(item["end"] - item["start"] for item in segments), 3)
        self.assertTrue(all(not (12 < item["start"] < 15) for item in segments))

    def test_filter_uses_planned_crop_without_stretch_and_expected_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = make_plan(root, duration=2, part_count=2)
            part = build_render_jobs(plan, root)[0]
            command = _command("ffmpeg", root / "rendered.mp4", root / "overlay.png", root / "out.mp4", part, plan["layout"], "h264_nvenc")
        graph = command[command.index("-filter_complex") + 1]
        self.assertIn("trim=start=0.000000000:end=1.000000000", graph)
        self.assertIn("crop=240:180:40:0", graph)
        self.assertIn("scale=1080:810", graph)
        self.assertIn("pad=1080:1920", graph)
        self.assertIn("aresample=48000", graph)
        self.assertIn("asetrate=48000*1.030000000", graph)
        self.assertIn("atempo=0.970873786", graph)
        self.assertIn("equalizer=f=250", graph)
        self.assertIn("equalizer=f=3000", graph)
        self.assertIn("equalizer=f=8000", graph)
        self.assertIn("alimiter=limit=0.95", graph)
        self.assertEqual(command[command.index("-progress") + 1], "pipe:1")
        self.assertIn("-nostats", command)

    def test_input_seek_precedes_source_and_trims_are_relative(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = make_plan(root, duration=3, part_count=3)
            part = build_render_jobs(plan, root)[0]
            part["source_segments"] = [
                {"start": 11.0, "end": 12.0},
                {"start": 15.0, "end": 17.0},
            ]
            part["duration"] = 3.0
            command = _command(
                "ffmpeg", root / "source.mp4", root / "overlay.png",
                root / "out.mp4", part, plan["layout"], "h264_nvenc",
            )
        source_input = command.index("-i")
        self.assertEqual(command[source_input - 2:source_input], ["-ss", "11.000000000"])
        graph = command[command.index("-filter_complex") + 1]
        self.assertIn("trim=start=0.000000000:end=1.000000000", graph)
        self.assertIn("trim=start=4.000000000:end=6.000000000", graph)
        self.assertNotIn("trim=start=11.000000000", graph)

    def test_cuda_path_uses_hardware_decode_filters_nvenc_and_keeps_seek(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = make_plan(root, duration=3, part_count=3)
            part = build_render_jobs(plan, root)[0]
            part["source_segments"] = [{"start": 11.0, "end": 12.0}]
            command = _command(
                "ffmpeg", root / "source.mp4", root / "overlay.png",
                root / "out.mp4", part, plan["layout"], "h264_nvenc",
                cuda_decoder="h264_cuvid",
            )
        source_input = command.index("-i")
        self.assertEqual(command[source_input - 2:source_input], ["-ss", "11.000000000"])
        self.assertIn("h264_cuvid", command)
        self.assertIn("0x0x40x40", command)
        self.assertIn("h264_metadata=crop_right=8", command)
        graph = command[command.index("-filter_complex") + 1]
        self.assertIn("scale_cuda=1080:810", graph)
        self.assertIn("reset_sar=1", graph)
        self.assertIn(
            f"pad_cuda=1080:1920:0:{plan['layout']['video_placement']['y']}:"
            "color=black,setsar=1",
            graph,
        )
        self.assertIn("pad_cuda=1080:1920", graph)
        self.assertIn("overlay_cuda=0:0", graph)
        self.assertNotIn("crop=240:180", graph)

    def test_cuda_decoder_selected_only_when_decoder_and_filters_exist(self):
        available = [
            SimpleNamespace(stdout="av1_cuvid h264_cuvid"),
            SimpleNamespace(stdout="scale_cuda pad_cuda overlay_cuda hwupload_cuda"),
        ]
        with patch("formatter.renderer._run", side_effect=available):
            self.assertEqual(_select_cuda_decoder("ffmpeg", "av1"), "av1_cuvid")
        missing_filter = [
            SimpleNamespace(stdout="av1_cuvid"),
            SimpleNamespace(stdout="scale_cuda pad_cuda hwupload_cuda"),
        ]
        with patch("formatter.renderer._run", side_effect=missing_filter):
            self.assertIsNone(_select_cuda_decoder("ffmpeg", "av1"))

    def test_overlay_uses_planned_title_and_part_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = make_plan(root, duration=2, part_count=2)
            output = render_overlay(plan, root / "overlay.png", "パート2")
            image = Image.open(output).convert("RGBA")
            title_box = plan["layout"]["title_banner_geometry"]
            outside = image.getpixel((0, 0))
            inside = image.getpixel((title_box["x"] + title_box["width"] // 2, title_box["y"] + 5))
        self.assertEqual(image.size, (1080, 1920))
        self.assertEqual(outside[3], 0)
        self.assertEqual(inside, (255, 255, 255, 255))

    def test_success_and_failure_states_preserve_completed_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = make_plan(root)
            (root / "rendered.mp4").write_bytes(b"clean")
            (root / "job.json").write_text(json.dumps({"status": "DONE"}), encoding="utf-8")
            plan_path = root / "format_plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            calls = 0

            def fake_run(command, _operation, on_progress):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise MediaProcessError("part two failed")
                on_progress(1.0)
                Path(command[-1]).write_bytes(b"video")

            media = {"duration": 1.0, "width": 1080, "height": 1920, "video_codec": "h264", "audio_codec": "aac", "video_duration": 1.0, "audio_duration": 1.0}
            with (
                patch("formatter.renderer._require_executable", side_effect=lambda name: name),
                patch("formatter.renderer._has_nvenc", return_value=True),
                patch("formatter.renderer._select_cuda_decoder", return_value=None),
                patch("formatter.renderer._run_ffmpeg_progress", side_effect=fake_run),
                patch("formatter.renderer._probe", return_value=media),
            ):
                result = render_format_plan(plan_path)
        self.assertEqual(result["formatter_status"], "FAILED")
        self.assertEqual(result["formatter_failed_part"], 2)
        self.assertEqual(len(result["formatted_outputs"]), 1)
        self.assertIn("part two failed", result["formatter_error"])

    def test_formatted_outputs_use_persisted_user_folder_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            user_folder = root / "Japanese title_00eec09a"
            plan = make_plan(root, duration=2, part_count=2)
            (root / "rendered.mp4").write_bytes(b"clean")
            (root / "job.json").write_text(json.dumps({
                "status": "DONE", "output_folder": str(user_folder),
            }), encoding="utf-8")
            plan_path = root / "format_plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")

            def fake_run(command, _operation, on_progress):
                on_progress(1.0)
                Path(command[-1]).write_bytes(b"video")

            media = {"duration": 1.0, "width": 1080, "height": 1920, "video_codec": "h264", "audio_codec": "aac", "video_duration": 1.0, "audio_duration": 1.0}
            with (
                patch("formatter.renderer._require_executable", side_effect=lambda name: name),
                patch("formatter.renderer._has_nvenc", return_value=True),
                patch("formatter.renderer._select_cuda_decoder", return_value=None),
                patch("formatter.renderer._run_ffmpeg_progress", side_effect=fake_run),
                patch("formatter.renderer._probe", return_value=media),
            ):
                result = render_format_plan(plan_path)
        self.assertEqual(result["formatter_status"], "DONE")
        self.assertEqual(len(result["formatted_outputs"]), 2)
        self.assertTrue(all(
            Path(item["path"]).parent == user_folder
            for item in result["formatted_outputs"]
        ))
        self.assertEqual(result["source_codec"], "h264")
        self.assertEqual(result["decoder"], "h264_software")
        self.assertEqual(result["video_filter_device"], "CPU")
        self.assertEqual(result["encoder"], "h264_nvenc")

    def test_cuda_filter_failure_falls_back_to_cpu_filters_but_keeps_nvenc(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = make_plan(root, duration=2, part_count=2)
            (root / "rendered.mp4").write_bytes(b"clean")
            (root / "job.json").write_text(json.dumps({"status": "DONE"}), encoding="utf-8")
            plan_path = root / "format_plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            commands = []

            def fake_run(command, _operation, on_progress):
                commands.append(command)
                if len(commands) == 1:
                    raise MediaProcessError("CUDA filter unavailable at runtime")
                on_progress(1.0)
                Path(command[-1]).write_bytes(b"video")

            media = {
                "duration": 1.0, "width": 1080, "height": 1920,
                "video_codec": "av1", "audio_codec": "aac",
                "video_duration": 1.0, "audio_duration": 1.0,
            }
            with (
                patch("formatter.renderer._require_executable", side_effect=lambda name: name),
                patch("formatter.renderer._has_nvenc", return_value=True),
                patch("formatter.renderer._select_cuda_decoder", return_value="av1_cuvid"),
                patch("formatter.renderer._run_ffmpeg_progress", side_effect=fake_run),
                patch("formatter.renderer._probe", return_value=media),
            ):
                result = render_format_plan(plan_path)
        self.assertEqual(result["formatter_status"], "DONE")
        self.assertEqual(len(commands), 3)
        self.assertIn("av1_cuvid", commands[0])
        self.assertNotIn("av1_cuvid", commands[1])
        self.assertTrue(all("h264_nvenc" in command for command in commands))
        self.assertTrue(all("libx264" not in command for command in commands))
        self.assertEqual(result["decoder"], "av1_software")
        self.assertEqual(result["video_filter_device"], "CPU")
        self.assertTrue(all(
            item["video_filter_path"] == "cpu_nvenc"
            and item["encoder"] == "h264_nvenc"
            and item["render_speed"] > 0
            for item in result["formatted_outputs"]
        ))

    def test_missing_nvenc_fails_without_libx264_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = make_plan(root)
            (root / "rendered.mp4").write_bytes(b"clean")
            (root / "job.json").write_text(json.dumps({"status": "DONE"}), encoding="utf-8")
            plan_path = root / "format_plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            with (
                patch("formatter.renderer._require_executable", return_value="ffmpeg"),
                patch("formatter.renderer._has_nvenc", return_value=False),
                patch("formatter.renderer._run_ffmpeg_progress") as run,
            ):
                result = render_format_plan(plan_path)
        self.assertEqual(result["formatter_status"], "FAILED")
        self.assertIn("requires working h264_nvenc", result["formatter_error"])
        run.assert_not_called()

    @unittest.skipUnless(find_executable("ffmpeg") and find_executable("ffprobe"), "FFmpeg/ffprobe unavailable")
    def test_ffmpeg_render_outputs_dimensions_codecs_and_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            subprocess.run([
                find_executable("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=5",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=5,volume=7",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source),
            ], check=True)
            clean_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            plan = make_plan(root)
            plan.update(
                clean_video_path=None, source_video_path=str(source),
                direct_source_render=True, input_duration=5,
                render_segments=[
                    {"output_start": 0, "output_end": 1.5, "source_start": 0.5, "source_end": 2.0},
                    {"output_start": 1.5, "output_end": 3.0, "source_start": 3.0, "source_end": 4.5},
                ],
            )
            plan_path = root / "format_plan.json"
            plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
            with (
                patch("formatter.renderer._has_nvenc", return_value=True),
                patch("formatter.renderer._select_cuda_decoder", return_value=None),
                patch("speech_detector.silero_detector.detect_with_silero") as silero,
                patch("speech_detector.sensevoice_detector.SenseVoiceDetector.detect") as sensevoice,
            ):
                result = render_format_plan(plan_path)
            silero.assert_not_called()
            sensevoice.assert_not_called()
            self.assertEqual(result["formatter_status"], "DONE")
            self.assertEqual(len(result["formatted_outputs"]), 3)
            for output in result["formatted_outputs"]:
                self.assertTrue(Path(output["path"]).is_file())
                self.assertEqual((output["width"], output["height"]), (1080, 1920))
                self.assertEqual(output["sample_aspect_ratio"], "1:1")
                self.assertEqual((output["video_codec"], output["audio_codec"]), ("h264", "aac"))
                self.assertEqual(output["audio_sample_rate"], 48000)
                self.assertLessEqual(output["duration_error"], 0.08)
                self.assertLessEqual(output["av_delta"], 0.08)
                pcm = subprocess.check_output([
                    find_executable("ffmpeg"), "-v", "error", "-i", output["path"],
                    "-map", "0:a:0", "-f", "f32le", "-acodec", "pcm_f32le", "-",
                ])
                samples = array("f")
                samples.frombytes(pcm)
                self.assertTrue(samples)
                self.assertTrue(all(math.isfinite(value) for value in samples))
                self.assertLessEqual(max(abs(value) for value in samples), 1.0)
            self.assertEqual(result["formatter_progress"], 100)
            self.assertEqual(result["formatter_eta_seconds"], 0)
            self.assertEqual(len(result["part_render_times"]), 3)
            self.assertTrue(result["audio_effect_enabled"])
            self.assertEqual(result["pitch_ratio"], 1.03)
            self.assertEqual(result["final_audio_sample_rate"], 48000)
            self.assertTrue(result["audio_sync_duration_validation"]["passed"])
            self.assertTrue(result["intermediate_render_skipped"])
            self.assertFalse((root / "clean_master.mp4").exists())
            self.assertFalse((root / "rendered.mp4").exists())
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), clean_hash)


class FormatterProgressTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.tracker = FormatterProgress(
            [100.0, 300.0, 600.0], clock=lambda: self.now,
            warmup_seconds=7.0, alpha=0.25,
        )

    def test_duration_weighted_progress_and_completed_part_contribution(self):
        self.now = 10
        first_half = self.tracker.update(1, 50)
        self.assertEqual(first_half["formatter_progress"], 5)
        first_done = self.tracker.update(1, 100)
        self.assertEqual(first_done["formatter_progress"], 10)
        self.tracker.start_part(2)
        self.now = 20
        second = self.tracker.update(2, 50)
        self.assertEqual(second["formatter_progress"], 15)

    def test_eta_waits_for_warmup_then_appears(self):
        self.now = 4
        early = self.tracker.update(1, 20)
        self.assertIsNone(early["formatter_eta_seconds"])
        self.now = 10
        ready = self.tracker.update(1, 50)
        self.assertGreater(ready["formatter_eta_seconds"], 0)

    def test_speed_uses_ema_smoothing(self):
        self.now = 10
        first = self.tracker.update(1, 20)
        self.assertAlmostEqual(first["formatter_render_speed"], 2.0)
        self.now = 20
        second = self.tracker.update(1, 60)
        self.assertAlmostEqual(second["formatter_render_speed"], 2.25)

    def test_progress_is_clamped_and_completion_has_zero_eta(self):
        self.now = 10
        self.tracker.update(1, 1000)
        self.tracker.start_part(3)
        self.now = 100
        final = self.tracker.update(3, 1000)
        self.assertEqual(final["formatter_progress"], 100)
        self.assertEqual(final["formatter_part_progress"], 1)
        self.assertEqual(final["formatter_eta_seconds"], 0)

    def test_part_transition_does_not_reset_total_progress_or_eta(self):
        self.now = 10
        before = self.tracker.update(1, 100)
        after = self.tracker.start_part(2)
        self.assertEqual(before["formatter_progress"], after["formatter_progress"])
        self.assertIsNotNone(after["formatter_eta_seconds"])

    def test_machine_progress_time_fields(self):
        self.assertEqual(_progress_seconds({"out_time_us": "2500000"}), 2.5)
        self.assertEqual(_progress_seconds({"out_time_ms": "3000000"}), 3.0)
        self.assertEqual(_progress_seconds({"out_time": "00:01:02.500000"}), 62.5)

    def test_eta_progress_supports_two_parts(self):
        now = 0.0
        tracker = FormatterProgress([200.0, 300.0], clock=lambda: now, warmup_seconds=0)
        now = 10
        first = tracker.update(1, 100)
        self.assertEqual(first["formatter_progress"], 20)
        tracker.update(1, 200)
        transitioned = tracker.start_part(2)
        self.assertEqual(transitioned["formatter_progress"], 40)
        now = 20
        final = tracker.update(2, 300)
        self.assertEqual(final["formatter_progress"], 100)
        self.assertEqual(final["formatter_eta_seconds"], 0)


if __name__ == "__main__":
    unittest.main()
