import json
import tempfile
import unittest
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
        })
        self.assertEqual(settings["max_concurrent_jobs"], 1)
        self.assertTrue(Path(settings["workspace_folder"]).is_dir())
        self.assertTrue(Path(settings["output_folder"]).is_dir())

    @patch("formatter.planner.plan_done_job")
    def test_formatter_rpc_consumes_done_job_artifacts(self, planner):
        job = job_runner.create_jobs(["https://example.com/video"])[0]
        job["status"] = "DONE"
        job_runner._write_job(job)
        planner.return_value = {
            "formatter_status": "PLANNED", "preview_path": "part1_preview.png",
            "parts": [{"index": 1}, {"index": 2}, {"index": 3}],
        }

        result = job_runner._rpc({
            "operation": "plan_tiktok_formatter", "payload": {"id": job["id"]}
        })

        planner.assert_called_once()
        self.assertEqual([item["index"] for item in result["parts"]], [1, 2, 3])
        self.assertEqual(result["formatter_status"], "PLANNED")
        self.assertEqual(Path(result["format_plan"]).name, "format_plan.json")
        self.assertEqual(Path(result["preview"]).name, "part1_preview.png")

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
            160,
        )

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
        self.assertNotIn("--keep-intro-outro", command_seen)
        self.assertNotIn("--content-start", command_seen)
        self.assertEqual(json.loads(report.read_text(encoding="utf-8")), intro_report)


if __name__ == "__main__":
    unittest.main()
