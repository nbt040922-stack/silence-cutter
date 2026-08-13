import json
import tempfile
import unittest
from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from backend import job_runner


class JobRunnerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.old_root = job_runner.ROOT
        self.old_settings = job_runner.SETTINGS_PATH
        job_runner.ROOT = self.root
        job_runner.SETTINGS_PATH = self.root / "workspace" / "desktop-settings.json"
        settings = job_runner.default_settings()
        settings.update(
            workspace_folder=str(self.root / "workspace"),
            input_folder=str(self.root / "input"),
            output_folder=str(self.root / "output"),
        )
        job_runner._atomic_json(job_runner.SETTINGS_PATH, settings)

    def tearDown(self):
        job_runner.ROOT = self.old_root
        job_runner.SETTINGS_PATH = self.old_settings
        self.directory.cleanup()

    def test_three_jobs_run_sequentially_and_survive_reload(self):
        jobs = job_runner.create_jobs([
            "https://example.com/one", "https://example.com/two", "https://example.com/three"
        ])
        order = []

        def download(job, directory):
            order.append(("download", job["url"]))
            source = directory / "source.mp4"
            source.write_bytes(b"source")
            job["title"] = job["url"].rsplit("/", 1)[-1]
            return source

        def pipeline(job, directory, source):
            order.append(("pipeline", job["url"]))
            rendered = directory / "rendered.mp4"
            report = directory / "pipeline_report.json"
            rendered.write_bytes(source.read_bytes() + b"-done")
            report.write_text("{}", encoding="utf-8")
            return rendered, report

        for _ in range(3):
            job_runner.process_next_job(downloader=download, pipeline=pipeline)

        reloaded = job_runner.list_jobs()
        self.assertEqual([job["status"] for job in reloaded], ["DONE"] * 3)
        self.assertEqual([item[1] for item in order[::2]], [job["url"] for job in jobs])
        self.assertTrue(all(Path(job["output_path"]).is_file() for job in reloaded))

    def test_active_jobs_recover_as_interrupted_and_can_retry(self):
        job = job_runner.create_jobs(["https://example.com/video"])[0]
        job["status"] = "RENDERING"
        job["stage"] = "rendering"
        job_runner._write_job(job)

        self.assertEqual(job_runner.recover_interrupted(), 1)
        interrupted = job_runner.list_jobs()[0]
        self.assertEqual(interrupted["status"], "INTERRUPTED")
        self.assertEqual(job_runner.retry_job(job["id"])["status"], "QUEUED")

    def test_invalid_url_fails_and_remove_cleans_job(self):
        job = job_runner.create_jobs(["not-a-url"])[0]
        self.assertEqual(job["status"], "FAILED")
        self.assertIn("http", job["error"])
        self.assertEqual(job_runner.remove_job(job["id"]), {"removed": True})
        self.assertEqual(job_runner.list_jobs(), [])

    def test_settings_force_single_concurrent_job(self):
        settings = job_runner.save_settings({
            "workspace_folder": str(self.root / "custom-workspace"),
            "output_folder": str(self.root / "custom-output"),
            "max_concurrent_jobs": 8,
            "keep_clean_master": True,
        })
        self.assertEqual(settings["max_concurrent_jobs"], 1)
        self.assertTrue(Path(settings["workspace_folder"]).is_dir())
        self.assertTrue(Path(settings["output_folder"]).is_dir())
        self.assertTrue(settings["keep_clean_master"])
        job = job_runner.create_jobs(["https://example.com/video"])[0]
        self.assertTrue(job["keep_clean_master"])

    def test_local_scan_finds_supported_videos_and_ignores_other_files(self):
        folder = Path(job_runner.load_settings()["input_folder"])
        folder.mkdir(parents=True, exist_ok=True)
        for extension in (".mp4", ".mkv", ".mov", ".webm", ".m4v"):
            (folder / f"video{extension}").write_bytes(b"stable")
        (folder / "notes.txt").write_text("ignore", encoding="utf-8")

        first = job_runner.scan_local_folder(now=0, probe=lambda _path: 10.0)
        ready = job_runner.scan_local_folder(now=8, probe=lambda _path: 10.0)

        self.assertEqual(first["counts"]["total_files"], 5)
        self.assertTrue(all(item["status"] == "STABILIZING" for item in first["files"]))
        self.assertEqual({item["filename"] for item in ready["files"]}, {
            "video.mp4", "video.mkv", "video.mov", "video.webm", "video.m4v",
        })
        self.assertTrue(all(item["status"] == "READY" for item in ready["files"]))

    def test_growing_file_stays_unready_until_size_and_mtime_are_stable(self):
        folder = Path(job_runner.load_settings()["input_folder"])
        folder.mkdir(parents=True, exist_ok=True)
        source = folder / "copying.mp4"
        source.write_bytes(b"partial")
        job_runner.scan_local_folder(now=0, probe=lambda _path: 10.0)
        source.write_bytes(b"partial-more")

        changed = job_runner.scan_local_folder(now=8, probe=lambda _path: 10.0)
        stable = job_runner.scan_local_folder(now=16, probe=lambda _path: 10.0)

        self.assertEqual(changed["files"][0]["status"], "STABILIZING")
        self.assertEqual(stable["files"][0]["status"], "READY")

    def test_start_processing_queues_ready_file_once(self):
        folder = Path(job_runner.load_settings()["input_folder"])
        folder.mkdir(parents=True, exist_ok=True)
        source = folder / "local.mp4"
        source.write_bytes(b"source")
        job_runner.scan_local_folder(now=0, probe=lambda _path: 12.0)
        job_runner.scan_local_folder(now=8, probe=lambda _path: 12.0)

        with patch.object(job_runner, "_probe_local_media", return_value=12.0):
            first = job_runner.start_local_processing()
            duplicate = job_runner.start_local_processing()

        jobs = job_runner.list_jobs()
        self.assertEqual((first["enqueued"], duplicate["enqueued"]), (1, 0))
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["input_mode"], "LOCAL_FOLDER")
        self.assertEqual(jobs[0]["status"], "READY")
        self.assertEqual(Path(jobs[0]["source_path"]), source.resolve())

    def test_watch_folder_enqueues_stable_new_file(self):
        settings = job_runner.save_settings({"watch_input_folder": True})
        folder = Path(settings["input_folder"])
        source = folder / "watched.mp4"
        source.write_bytes(b"source")
        job_runner.scan_local_folder(now=0, probe=lambda _path: 20.0)
        job_runner.scan_local_folder(now=8, probe=lambda _path: 20.0)
        manager = job_runner.DownloadManager(clock=lambda: 9.0)
        pending = Future()
        manager.process_future = pending
        try:
            manager.tick()
        finally:
            manager.close()
        jobs = job_runner.list_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["source_fingerprint"], job_runner._local_fingerprint(
            source, source.stat().st_size, source.stat().st_mtime_ns,
        ))

    def test_local_job_skips_downloader_and_source_remains_untouched(self):
        settings = job_runner.load_settings()
        folder = Path(settings["input_folder"])
        folder.mkdir(parents=True, exist_ok=True)
        source = folder / "readonly.mp4"
        original = b"do-not-change"
        source.write_bytes(original)
        job = job_runner._create_local_job(source, "fingerprint", 500.0, settings)

        def pipeline(_job, directory, pipeline_source):
            self.assertEqual(pipeline_source, source.resolve())
            report = directory / "pipeline_report.json"
            report.write_text(json.dumps({"expected_output_duration": 500}), encoding="utf-8")
            return None, report

        with patch.object(job_runner, "_format_done_job", return_value={"formatter_status": "DONE"}):
            result = job_runner._process_ready_job(job, pipeline=pipeline)

        self.assertEqual(result["status"], "DONE")
        self.assertEqual(source.read_bytes(), original)
        self.assertTrue(source.is_file())
        self.assertEqual(result["download_time"], 0.0)

    def test_local_ready_job_never_invokes_downloader(self):
        settings = job_runner.load_settings()
        source = Path(settings["input_folder"]) / "local.mp4"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"source")
        job_runner._create_local_job(source, "local-fingerprint", 30.0, settings)
        downloads = []

        def downloader(*args):
            downloads.append(args)
            raise AssertionError("local jobs must not invoke the downloader")

        with patch.object(job_runner, "_process_ready_job", return_value={}) as process:
            manager = job_runner.DownloadManager(downloader=downloader)
            try:
                manager.tick()
            finally:
                manager.close()

        self.assertEqual(downloads, [])
        process.assert_called_once()

    def test_local_eta_contains_analysis_and_format_only(self):
        snapshot = job_runner._eta_snapshot({
            "input_mode": "LOCAL_FOLDER", "status": "READY", "stage": "ready",
            "started_at": self.instant(100), "duration": 600,
            "clean_video_duration": 500,
        }, {
            "analysis_seconds_per_video_minute": 6,
            "formatter_render_speed_x": 5,
        }, now=110)
        self.assertEqual(snapshot["total_eta_seconds"], 160)

    @patch("silence_cutter.audio.probe_media", return_value={"duration": 900.0})
    @patch("backend.job_runner.subprocess.run")
    def test_normal_video_does_not_invoke_long_selector(self, run, _probe):
        job_dir = self.root / "job"
        job_dir.mkdir()
        (job_dir / "logs").mkdir()
        result = job_runner._run_long_video_stage({}, job_dir, job_dir / "source.mp4")
        self.assertEqual(result["status"], "NOT_APPLICABLE")
        run.assert_not_called()

    @patch("silence_cutter.audio.probe_media", return_value={"duration": 901.0})
    @patch("backend.job_runner.subprocess.run")
    def test_long_selector_timeout_fails_open(self, run, _probe):
        import subprocess
        run.side_effect = subprocess.TimeoutExpired("selector", 90)
        job_dir = self.root / "job"
        job_dir.mkdir()
        (job_dir / "logs").mkdir()
        result = job_runner._run_long_video_stage({}, job_dir, job_dir / "source.mp4")
        self.assertEqual(result["status"], "LONG_VIDEO_SELECTOR_SKIPPED")
        self.assertEqual(result["selected_ranges"], [])

    @patch.object(job_runner, "_run_process")
    def test_applied_selection_is_passed_to_production(self, run):
        job_dir = self.root / "job"
        job_dir.mkdir()
        source = job_dir / "source.mp4"
        source.write_bytes(b"source")
        job_runner._atomic_json(job_dir / "long_video_selection.json", {
            "status": "APPLIED", "selected_ranges": [
                {"start": 0, "end": 180, "score": .9},
                {"start": 300, "end": 480, "score": .8},
                {"start": 600, "end": 780, "score": .7},
            ],
        })
        def complete(command, _job, directory):
            (directory / "pipeline_report.json").write_text("{}", encoding="utf-8")
        run.side_effect = complete
        job_runner._pipeline({}, job_dir, source)
        command = run.call_args.args[0]
        self.assertIn("--allowed-ranges-json", command)

    def test_local_folder_settings_persist_after_restart(self):
        saved = job_runner.save_settings({
            "input_folder": str(self.root / "incoming"),
            "output_folder": str(self.root / "finished"),
            "watch_input_folder": True,
        })
        loaded = job_runner.load_settings()
        self.assertEqual(loaded["input_folder"], saved["input_folder"])
        self.assertEqual(loaded["output_folder"], saved["output_folder"])
        self.assertTrue(loaded["watch_input_folder"])
        self.assertEqual(loaded["input_mode"], "LOCAL_FOLDER")

    def test_youtube_controls_are_collapsed_under_advanced_tools(self):
        html = (Path(__file__).parents[1] / "desktop" / "index.html").read_text(encoding="utf-8")
        main_input = html.index('id="inputFolder"')
        advanced = html.index('id="advancedTools"')
        youtube = html.index('id="urlInput"')
        self.assertLess(main_input, advanced)
        self.assertLess(advanced, youtube)
        self.assertNotIn("Paste video links", html)
        self.assertNotIn('id="youtubeProfileButton"', html[:html.index("<main>")])

    @patch("formatter.renderer.render_format_plan")
    @patch("formatter.planner.plan_done_job")
    def test_formatter_rpc_consumes_done_job_artifacts(self, planner, renderer):
        job = job_runner.create_jobs(["https://example.com/video"])[0]
        job["status"] = "DONE"
        job_runner._write_job(job)
        planner.return_value = {
            "formatter_status": "PLANNED", "preview_path": "part1_preview.png",
            "parts": [{"index": 1}, {"index": 2}, {"index": 3}],
        }
        renderer.return_value = {
            **planner.return_value, "formatter_status": "DONE",
            "formatted_outputs": [{"index": 1}, {"index": 2}, {"index": 3}],
        }

        result = job_runner._rpc({
            "operation": "plan_tiktok_formatter", "payload": {"id": job["id"]}
        })

        planner.assert_called_once()
        self.assertEqual([item["index"] for item in result["parts"]], [1, 2, 3])
        renderer.assert_called_once()
        self.assertEqual(result["formatter_status"], "DONE")
        self.assertEqual(Path(result["format_plan"]).name, "format_plan.json")
        self.assertEqual(Path(result["preview"]).name, "part1_preview.png")

    @patch.object(job_runner, "_format_done_job")
    def test_done_pipeline_auto_starts_formatter_without_failing_master(self, formatter):
        job = job_runner.create_jobs(["https://example.com/video"])[0]
        source = job_runner._job_path(job["id"]).parent / "source.mp4"
        source.write_bytes(b"source")
        job.update(status="READY", source_path=str(source), title="Video", display_name="Video")
        job_runner._write_job(job)

        def pipeline(_job, directory, _source):
            rendered = directory / "rendered.mp4"
            report = directory / "pipeline_report.json"
            rendered.write_bytes(b"clean")
            report.write_text("{}", encoding="utf-8")
            return rendered, report

        formatter.return_value = {"formatter_status": "DONE"}
        result = job_runner._process_ready_job(job, pipeline=pipeline)

        self.assertEqual(result["status"], "DONE")
        self.assertEqual(Path(result["output_path"]).name, "clean_master.mp4")
        self.assertEqual(Path(result["output_path"]).parent.name, f"Video_{job['id'][:8]}")
        self.assertEqual(
            job_runner.list_jobs()[0]["output_path"], result["output_path"]
        )
        formatter.assert_called_once()

    @patch.object(job_runner, "_format_done_job")
    def test_auto_format_two_and_three_part_jobs_skip_clean_master(self, formatter):
        formatter.return_value = {"formatter_status": "DONE"}
        for duration in (599.9, 800.0):
            with self.subTest(duration=duration):
                job = job_runner.create_jobs([f"https://example.com/{duration}"])[0]
                job_dir = job_runner._job_path(job["id"]).parent
                source = job_dir / "source.mp4"
                source.write_bytes(b"source")
                job.update(
                    status="READY", source_path=str(source), title=f"Video {duration}",
                    display_name=f"Video {duration}",
                )
                job_runner._write_job(job)

                def pipeline(_job, directory, _source):
                    report = directory / "pipeline_report.json"
                    report.write_text(json.dumps({
                        "expected_output_duration": duration,
                    }), encoding="utf-8")
                    return None, report

                result = job_runner._process_ready_job(job, pipeline=pipeline)
                self.assertEqual(result["status"], "DONE")
                self.assertFalse(result["clean_master_required"])
                self.assertFalse(result["clean_master_rendered"])
                self.assertTrue(result["intermediate_render_skipped"])
                self.assertIsNone(result["output_path"])
                self.assertFalse((Path(result["output_folder"]) / "clean_master.mp4").exists())
                self.assertTrue(source.is_file())

    @patch.object(job_runner, "_format_done_job")
    @patch.object(job_runner, "_render_clean_master_from_report")
    def test_needs_review_job_renders_clean_master_without_detector_rerun(
        self, render_clean, formatter,
    ):
        job = job_runner.create_jobs(["https://example.com/long"])[0]
        job_dir = job_runner._job_path(job["id"]).parent
        source = job_dir / "source.mp4"
        source.write_bytes(b"source")
        job.update(status="READY", source_path=str(source), title="Long", display_name="Long")
        job_runner._write_job(job)

        def pipeline(_job, directory, _source):
            report = directory / "pipeline_report.json"
            report.write_text(json.dumps({"expected_output_duration": 1200.1}), encoding="utf-8")
            return None, report

        def render(_source, rendered, _report):
            rendered.write_bytes(b"clean")
            return rendered

        render_clean.side_effect = render
        formatter.return_value = {"formatter_status": "NEEDS_REVIEW"}
        result = job_runner._process_ready_job(job, pipeline=pipeline)

        render_clean.assert_called_once()
        self.assertTrue(result["clean_master_required"])
        self.assertTrue(result["clean_master_rendered"])
        self.assertEqual(Path(result["output_path"]).name, "clean_master.mp4")
        self.assertTrue(Path(result["output_path"]).is_file())
        self.assertTrue(source.is_file())

    @patch.object(job_runner, "_format_done_job")
    def test_direct_formatter_failure_preserves_recovery_files(self, formatter):
        job = job_runner.create_jobs(["https://example.com/fail"])[0]
        job_dir = job_runner._job_path(job["id"]).parent
        source = job_dir / "source.mp4"
        source.write_bytes(b"source")
        job.update(status="READY", source_path=str(source), title="Fail", display_name="Fail")
        job_runner._write_job(job)

        def pipeline(_job, directory, _source):
            report = directory / "pipeline_report.json"
            report.write_text(json.dumps({"expected_output_duration": 500}), encoding="utf-8")
            return None, report

        def fail(job_file, **_options):
            stored = job_runner._read_job(job_file)
            stored.update(formatter_status="FAILED", formatter_error="render failed")
            job_runner._write_job(stored)
            return {"formatter_status": "FAILED"}

        formatter.side_effect = fail
        result = job_runner._process_ready_job(job, pipeline=pipeline)
        self.assertEqual(result["status"], "DONE")
        self.assertEqual(result["formatter_status"], "FAILED")
        self.assertTrue(source.is_file())
        self.assertTrue(Path(result["report_path"]).is_file())
        self.assertFalse((Path(result["output_folder"]) / "clean_master.mp4").exists())

    def test_unicode_titles_persist_log_serialize_and_sanitize(self):
        titles = [
            "辻ちゃんネル 夏休み",
            "Đây là video thử nghiệm",
            "테스트 영상",
            "Test video 🎬🔥",
            "旅行 vlog – Hà Nội 🇻🇳",
        ]
        for title in titles:
            job = job_runner.create_jobs(["https://example.com/video"])[0]
            job["title"] = title
            job_runner._write_job(job)
            job_dir = job_runner._job_path(job["id"]).parent
            job_runner._log(job_dir, title)

            persisted = job_runner._read_job(job_runner._job_path(job["id"]))
            response = json.dumps(
                {"ok": True, "result": persisted}, ensure_ascii=False
            ).encode("utf-8").decode("utf-8")
            self.assertEqual(persisted["title"], title)
            self.assertIn(title, response)
            self.assertIn(
                title,
                (job_dir / "logs" / "job.log").read_text(encoding="utf-8"),
            )
            self.assertEqual(job_runner._sanitize_title(title), title)

        self.assertEqual(job_runner._sanitize_title('動画:<test>? .'), "動画__test__")
        self.assertEqual(job_runner._sanitize_title("CON.txt"), "_CON.txt")
        self.assertLessEqual(
            len(job_runner._sanitize_title("🎬" * 200).encode("utf-16-le")) // 2,
            110,
        )

    def test_human_readable_output_folder_sanitization(self):
        for title in (
            "【コストコ保存】購入品のその後",
            "Đi mua sắm với gia đình",
            "Family vlog 🎬🔥",
        ):
            with self.subTest(title=title):
                self.assertEqual(job_runner._sanitize_title(title), title)
        self.assertEqual(job_runner._sanitize_title('bad<>:"/\\|?*name'), "bad_________name")
        self.assertEqual(job_runner._sanitize_title("title...   "), "title")

    def test_output_folder_uses_title_and_short_id_without_collisions(self):
        first = job_runner._user_output_folder(
            self.root, "My Video", "00eec09aeb5244f0a27a1028287cb084"
        )
        second = job_runner._user_output_folder(
            self.root, "My Video", "11ffc09aeb5244f0a27a1028287cb084"
        )
        self.assertEqual(first.name, "My Video_00eec09a")
        self.assertEqual(second.name, "My Video_11ffc09a")
        self.assertNotEqual(first, second)

    def test_long_output_title_is_recognizable_and_path_safe(self):
        folder = job_runner._user_output_folder(
            self.root, "日本語タイトル" * 40, "00eec09aeb5244f0a27a1028287cb084"
        )
        title_part = folder.name.removesuffix("_00eec09a")
        self.assertTrue(title_part.startswith("日本語タイトル"))
        self.assertLessEqual(len(title_part.encode("utf-16-le")) // 2, 110)

    def test_existing_uuid_job_loads_without_display_name(self):
        job = job_runner.create_jobs(["https://example.com/video"])[0]
        stored = job_runner._read_job(job_runner._job_path(job["id"]))
        stored.pop("display_name")
        job_runner._write_job(stored)
        loaded = job_runner.list_jobs()[0]
        self.assertEqual(loaded["id"], job["id"])
        self.assertEqual(loaded["title"], "example.com")

    def test_ui_uses_display_title_and_short_id(self):
        source = (Path(__file__).parents[1] / "desktop" / "src" / "main.js").read_text(encoding="utf-8")
        self.assertIn("job.display_name || job.title || job.url", source)
        self.assertIn(".slice(0, 8)", source)
        self.assertIn("job.formatter_part_count", source)
        self.assertNotIn("formatter_current_part || 1}/3", source)

    @patch.object(job_runner, "_yt_dlp_command", return_value=["yt-dlp"])
    @patch.object(job_runner, "_run_process")
    def test_mocked_ytdlp_unicode_metadata_and_progress(self, run_process, _command):
        title = "旅行 vlog – Hà Nội 🇻🇳 🎬🔥"
        job = job_runner.create_jobs(["https://example.com/video"])[0]
        job_dir = job_runner._job_path(job["id"]).parent

        def fake_process(command, current_job, directory, **options):
            callback = options.get("on_line")
            if "--dump-single-json" in command:
                callback(json.dumps({
                    "title": title,
                    "duration": 12.5,
                    "filename": "source.mp4",
                    "webpage_url": current_job["url"],
                }, ensure_ascii=False))
            else:
                callback("download: 57.5% — 日本語 🎬")
                (directory / "source.mp4").write_bytes(b"media")

        run_process.side_effect = fake_process
        source = job_runner._download(job, job_dir)

        self.assertEqual(source.name, "source.mp4")
        self.assertEqual(job["title"], title)
        self.assertEqual(job["display_name"], title)
        self.assertEqual(job["duration"], 12.5)
        self.assertEqual(job["progress"], 57.5)
        self.assertEqual(
            job_runner._read_job(job_runner._job_path(job["id"]))["title"], title
        )

    def test_utf8_child_environment_preserves_existing_values(self):
        with patch.dict(job_runner.os.environ, {"EXISTING_VALUE": "kept"}, clear=True):
            environment = job_runner._utf8_environment()
        self.assertEqual(environment["EXISTING_VALUE"], "kept")
        self.assertEqual(environment["PYTHONUTF8"], "1")
        self.assertEqual(environment["PYTHONIOENCODING"], "utf-8")

    def test_pipeline_invocation_preserves_intro_defaults_and_report(self):
        job = job_runner.create_jobs(["https://example.com/video"])[0]
        job_dir = job_runner._job_path(job["id"]).parent
        source = job_dir / "source.mp4"
        source.write_bytes(b"source")
        intro_report = {
            "detected_intro_boundary": 26.5,
            "post_intro_trim": 2.0,
            "final_content_start": 28.5,
            "final_clean_content_start": 33.05,
            "post_intro_visual_trim": 0.3,
            "final_render_start": 33.35,
            "detected_outro_boundary": 100.0,
        }
        command_seen = []

        def fake_process(command, _job, directory, **options):
            command_seen.extend(command)
            (directory / "rendered.mp4").write_bytes(b"rendered")
            (directory / "pipeline_report.json").write_text(
                json.dumps(intro_report), encoding="utf-8"
            )

        with patch.object(job_runner, "_run_process", side_effect=fake_process):
            _rendered, report = job_runner._pipeline(job, job_dir, source)

        self.assertEqual(command_seen[1:3], ["-m", "production"])
        self.assertIn("--debug", command_seen)
        self.assertIn("--analysis-only", command_seen)
        self.assertIsNone(_rendered)
        self.assertNotIn("--keep-intro-outro", command_seen)
        self.assertNotIn("--content-start", command_seen)
        self.assertEqual(json.loads(report.read_text(encoding="utf-8")), intro_report)

    @staticmethod
    def instant(seconds):
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat()

    def test_download_eta_covers_download_analysis_and_direct_formatter(self):
        snapshot = job_runner._eta_snapshot({
            "status": "DOWNLOADING", "stage": "downloading",
            "started_at": self.instant(100), "download_started_at": self.instant(100),
            "duration": 600, "progress": 50, "overall_progress": 0,
        }, {
            "analysis_seconds_per_video_minute": 6,
            "formatter_render_speed_x": 5,
        }, now=110)
        self.assertEqual(snapshot["total_elapsed_seconds"], 10)
        self.assertEqual(snapshot["total_eta_seconds"], 190)
        self.assertEqual(snapshot["estimated_total_job_time"], 200)

    def test_direct_ready_eta_excludes_clean_master_render(self):
        snapshot = job_runner._eta_snapshot({
            "status": "READY", "stage": "ready", "started_at": self.instant(100),
            "duration": 600, "clean_video_duration": 500,
            "clean_master_required": False, "intermediate_render_skipped": True,
        }, {
            "analysis_seconds_per_video_minute": 6,
            "formatter_render_speed_x": 5,
        }, now=110)
        self.assertEqual(snapshot["total_eta_seconds"], 160)

    def test_formatter_live_eta_replaces_history_and_includes_future_parts(self):
        snapshot = job_runner._eta_snapshot({
            "status": "DONE", "stage": "formatting", "started_at": self.instant(100),
            "finished_at": None, "duration": 600, "clean_video_duration": 500,
            "formatter_status": "RENDERING", "formatter_progress": 20,
            "formatter_eta_seconds": 40,
        }, {
            "analysis_seconds_per_video_minute": 6,
            "formatter_render_speed_x": 2,
        }, now=160)
        self.assertEqual(snapshot["total_eta_seconds"], 40)
        self.assertEqual(snapshot["estimated_total_job_time"], 100)

    def test_total_progress_never_decreases(self):
        snapshot = job_runner._eta_snapshot({
            "status": "ANALYZING", "started_at": self.instant(100),
            "analysis_started_at": self.instant(150), "duration": 600,
            "overall_progress": 70,
        }, {
            "analysis_seconds_per_video_minute": 60,
            "formatter_render_speed_x": 1,
        }, now=160)
        self.assertGreaterEqual(snapshot["overall_progress"], 70)

    def test_stage_transition_does_not_reset_total_elapsed(self):
        history = {
            "analysis_seconds_per_video_minute": 6,
            "formatter_render_speed_x": 5,
        }
        downloading = job_runner._eta_snapshot({
            "status": "DOWNLOADING", "started_at": self.instant(100),
            "download_started_at": self.instant(100), "duration": 600, "progress": 50,
        }, history, now=160)
        analyzing = job_runner._eta_snapshot({
            "status": "ANALYZING", "started_at": self.instant(100),
            "analysis_started_at": self.instant(150), "duration": 600,
        }, history, now=160)
        self.assertEqual(downloading["total_elapsed_seconds"], 60)
        self.assertEqual(analyzing["total_elapsed_seconds"], 60)

    def test_done_and_needs_review_finish_eta_policy(self):
        done = job_runner._eta_snapshot({
            "status": "DONE", "formatter_status": "DONE",
            "started_at": self.instant(100), "finished_at": self.instant(200),
        }, {}, now=300)
        review = job_runner._eta_snapshot({
            "status": "DONE", "formatter_status": "NEEDS_REVIEW",
            "started_at": self.instant(100), "finished_at": self.instant(180),
        }, {}, now=300)
        self.assertEqual((done["overall_progress"], done["total_eta_seconds"]), (100, 0))
        self.assertEqual(review["overall_progress"], 100)
        self.assertIsNone(review["total_eta_seconds"])
        self.assertEqual(review["eta_status"], "NOT_APPLICABLE")

    def test_final_metrics_use_started_and_finished_wall_clock(self):
        job = job_runner.create_jobs(["https://example.com/timing"])[0]
        job.update(
            status="DONE", formatter_status="DONE", started_at=self.instant(100),
            downloaded_at=self.instant(120), analysis_time=30,
            total_format_render_time=40, estimated_total_time_at_start=110,
        )
        result = job_runner._finalize_job(job, "done")
        self.assertAlmostEqual(
            result["total_job_time"],
            job_runner._seconds_between(result["started_at"], result["finished_at"]),
        )
        self.assertEqual(result["download_time"], 20)
        self.assertEqual(result["format_render_time"], 40)
        self.assertAlmostEqual(
            result["final_estimation_error"], abs(result["total_job_time"] - 110)
        )

    def test_ui_primary_timer_uses_end_to_end_eta(self):
        source = (Path(__file__).parents[1] / "desktop" / "src" / "main.js").read_text(encoding="utf-8")
        self.assertIn("job.total_eta_seconds", source)
        self.assertIn("job.estimated_total_job_time", source)
        self.assertIn("job.overall_progress", source)
        self.assertNotIn("formatterEta(job)", source)


if __name__ == "__main__":
    unittest.main()
