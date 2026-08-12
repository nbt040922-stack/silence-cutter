import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend import job_runner


class Clock:
    def __init__(self):
        self.value = 1_000.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class DownloaderManagerTests(unittest.TestCase):
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
        self.clock = Clock()
        self.config = job_runner.DownloaderManagerConfig(
            download_cooldown_min_seconds=55,
            download_cooldown_max_seconds=55,
        )
        self.managers = []

    def tearDown(self):
        for manager in self.managers:
            manager.close()
        job_runner.ROOT = self.old_root
        job_runner.SETTINGS_PATH = self.old_settings
        self.directory.cleanup()

    def manager(self, downloader, pipeline=None):
        self.ready_youtube_profile()
        manager = job_runner.DownloadManager(
            config=self.config,
            downloader=downloader,
            pipeline=pipeline or self.pipeline,
            clock=self.clock,
            cooldown=lambda _minimum, _maximum: 55,
        )
        self.managers.append(manager)
        return manager

    @staticmethod
    def pipeline(job, directory, source):
        rendered = directory / "rendered.mp4"
        report = directory / "pipeline_report.json"
        rendered.write_bytes(source.read_bytes() + b"-rendered")
        report.write_text("{}", encoding="utf-8")
        return rendered, report

    @staticmethod
    def source(job, directory):
        source = directory / "source.mp4"
        source.write_bytes(job["url"].encode("utf-8"))
        return source

    def pump(self, manager, condition, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            manager.tick()
            time.sleep(0.005)
        self.fail("scheduler condition timed out")

    def statuses(self):
        return [job["status"] for job in job_runner.list_jobs()]

    def ready_youtube_profile(self):
        cookies = job_runner.youtube_profile_dir() / "Default" / "Network" / "Cookies"
        cookies.parent.mkdir(parents=True, exist_ok=True)
        cookies.write_bytes(b"browser-owned-cookie-db")

    def auth_attempt(self, downloader):
        job = job_runner.create_jobs(["https://youtube.com/watch?v=test"])[0]
        return job_runner._download_attempt(
            job["id"], downloader=downloader,
            config=self.config, clock=self.clock,
            cooldown=lambda _minimum, _maximum: 55,
        )

    def test_download_uses_profile_immediately_and_never_logs_anonymous(self):
        self.ready_youtube_profile()
        calls = []
        result = self.auth_attempt(
            lambda job, directory: calls.append(True) or self.source(job, directory)
        )
        self.assertEqual(result["status"], "READY")
        self.assertEqual(calls, [True])
        log = (job_runner._job_path(result["id"]).parent / "logs" / "job.log").read_text()
        self.assertIn("YouTube profile", log)
        self.assertNotIn("anonymous", log.lower())

    def test_no_profile_requests_login_without_invoking_downloader(self):
        calls = []
        result = self.auth_attempt(lambda *_args: calls.append(True))
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["stage"], "auth_required")
        self.assertEqual(calls, [])
        self.assertEqual(job_runner.youtube_login_status()["status"], "LOGIN REQUIRED")

    def test_profile_403_stops_without_fallback_or_retry(self):
        self.ready_youtube_profile()
        calls = []

        def fail(_job, _directory):
            calls.append(True)
            raise RuntimeError("HTTP Error 403: Forbidden")

        result = self.auth_attempt(fail)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["stage"], "profile_error")
        self.assertEqual(result["download_error_code"], "HTTP_403")
        self.assertIsNone(result["download_retry_at"])
        self.assertEqual(calls, [True])

    def test_manual_retry_runs_once_per_user_action(self):
        self.ready_youtube_profile()
        calls = []

        def fail(_job, _directory):
            calls.append(True)
            raise RuntimeError("authentication required")

        first = self.auth_attempt(fail)
        job_runner.retry_job(first["id"])
        second = job_runner._download_attempt(
            first["id"], downloader=fail, config=self.config,
            clock=self.clock, cooldown=lambda _minimum, _maximum: 55,
        )
        self.assertEqual(second["stage"], "auth_required")
        self.assertEqual(calls, [True, True])

    def test_expired_session_requests_login_without_retry(self):
        self.ready_youtube_profile()
        calls = []

        def fail(_job, _directory):
            calls.append(True)
            raise RuntimeError("LOGIN_REQUIRED: session expired")

        result = self.auth_attempt(fail)
        self.assertEqual(result["stage"], "auth_required")
        self.assertEqual(result["download_error_code"], "AUTH_REQUIRED")
        self.assertIsNone(result["download_retry_at"])
        self.assertEqual(calls, [True])

    def test_bot_challenge_requests_login_without_retry(self):
        self.ready_youtube_profile()
        result = self.auth_attempt(
            lambda *_args: (_ for _ in ()).throw(
                RuntimeError("Sign in to confirm you're not a bot")
            )
        )
        self.assertEqual(result["stage"], "auth_required")
        self.assertEqual(result["download_error_code"], "BOT_CHALLENGE_OR_TOKEN")

    def test_no_profile_reports_login_required_at_persistent_path(self):
        result = job_runner.youtube_login_status()
        self.assertEqual(result["status"], "LOGIN REQUIRED")
        self.assertEqual(Path(result["youtube_profile_path"]).name, "youtube_profile")

    @patch("backend.job_runner._yt_dlp_command", return_value=["yt-dlp"])
    def test_every_download_command_uses_browser_profile(self, _yt_dlp):
        commands = []

        def run(command, job, directory, *, on_line=None, **_options):
            commands.append(command)
            if "--dump-single-json" in command:
                on_line(json.dumps({"title": "Video", "duration": 1}))
            else:
                (directory / "source.mp4").write_bytes(b"media")

        profile_job = job_runner.create_jobs(["https://youtube.com/watch?v=profile"])[0]
        profile_dir = job_runner._job_path(profile_job["id"]).parent
        with patch.object(job_runner, "_run_process", side_effect=run):
            job_runner._download(profile_job, profile_dir)
        self.assertTrue(all("--cookies-from-browser" in command for command in commands))
        self.assertTrue(all(
            f"chrome:{job_runner.youtube_profile_dir()}" in command for command in commands
        ))

    def test_browser_profile_lock_has_actionable_status(self):
        self.ready_youtube_profile()
        result = self.auth_attempt(
            lambda *_args: (_ for _ in ()).throw(
                RuntimeError("Could not copy Chrome cookie database: database is locked")
            ),
        )
        self.assertEqual(result["stage"], "profile_locked")
        self.assertEqual(result["download_error_code"], "BROWSER_PROFILE_LOCKED")
        self.assertEqual(result["error"], "Close the YouTube login browser and retry.")

    @patch("backend.job_runner._yt_dlp_command", return_value=["yt-dlp"])
    @patch("backend.job_runner.subprocess.run")
    def test_session_test_persists_valid_state(self, run, _yt_dlp):
        self.ready_youtube_profile()
        run.return_value = Mock(returncode=0, stderr="")
        result = job_runner.test_youtube_access()
        reloaded = job_runner.load_settings()
        self.assertTrue(result["accessible"])
        self.assertEqual(result["status"], "PROFILE READY")
        self.assertEqual(reloaded["last_session_status"], "PROFILE READY")
        self.assertIsNotNone(reloaded["last_session_test"])

    @patch("backend.job_runner._yt_dlp_command", return_value=["yt-dlp"])
    @patch("backend.job_runner.subprocess.run")
    def test_session_test_classifies_locked_profile(self, run, _yt_dlp):
        self.ready_youtube_profile()
        run.return_value = Mock(
            returncode=1,
            stderr="ERROR: Could not copy Chrome cookie database: database is locked",
        )
        result = job_runner.test_youtube_access()
        self.assertEqual(result["status"], "PROFILE LOCKED")
        self.assertEqual(result["error"], "Close the YouTube login browser and retry.")

    def test_saved_profile_path_cannot_redirect_to_normal_chrome(self):
        settings = job_runner.default_settings()
        settings["youtube_profile_path"] = str(self.root / "normal-chrome-profile")
        job_runner._atomic_json(job_runner.SETTINGS_PATH, settings)
        self.assertEqual(
            job_runner.youtube_profile_dir(),
            (job_runner.SETTINGS_PATH.parent / "youtube_profile").resolve(),
        )

    def test_reset_profile_requires_confirmation_and_returns_to_login_required(self):
        self.ready_youtube_profile()
        with self.assertRaisesRegex(ValueError, "explicit confirmation"):
            job_runner.reset_youtube_profile()
        self.assertTrue(job_runner.youtube_profile_dir().is_dir())
        result = job_runner.reset_youtube_profile(confirmed=True)
        self.assertFalse(job_runner.youtube_profile_dir().exists())
        self.assertEqual(result["status"], "LOGIN REQUIRED")

    def test_restart_reuses_existing_profile(self):
        self.ready_youtube_profile()
        job_runner._atomic_json(job_runner.SETTINGS_PATH, job_runner.load_settings())
        self.assertTrue(job_runner.youtube_profile_ready())
        self.assertEqual(job_runner.youtube_login_status()["status"], "PROFILE READY")

    @patch("backend.job_runner.subprocess.Popen")
    @patch("backend.job_runner._chrome_executable", return_value=Path("chrome.exe"))
    def test_login_browser_uses_only_dedicated_profile(self, _chrome, popen):
        result = job_runner.open_youtube_login()
        command = popen.call_args.args[0]
        self.assertTrue(result["browser_opened"])
        self.assertIn(
            f"--user-data-dir={job_runner.youtube_profile_dir()}", command
        )
        self.assertEqual(command[-1], "https://www.youtube.com/")

    def test_command_log_redacts_credentials_and_headers(self):
        directory = self.root / "log-test"
        (directory / "logs").mkdir(parents=True)
        job_runner._command_log(directory, [
            "yt-dlp", "--add-header", "Authorization: secret",
            "--password", "secret-password", "https://youtube.com/",
        ])
        logged = (directory / "logs" / "commands.log").read_text(encoding="utf-8")
        self.assertNotIn("Authorization: secret", logged)
        self.assertNotIn("secret-password", logged)
        self.assertEqual(logged.count("<redacted>"), 2)

    def test_browser_session_path_and_cookie_values_never_enter_logs_or_jobs(self):
        directory = self.root / "log-session"
        (directory / "logs").mkdir(parents=True)
        profile = job_runner.youtube_profile_dir()
        job_runner._command_log(directory, [
            "yt-dlp", "--cookies-from-browser", f"chrome:{profile}",
            "https://youtube.com/watch?v=test",
        ])
        logged = (directory / "logs" / "commands.log").read_text(encoding="utf-8")
        self.assertNotIn(str(profile), logged)
        self.assertNotIn("browser-owned-cookie-db", logged)
        job = job_runner.create_jobs(["https://youtube.com/watch?v=test"])[0]
        serialized = json.dumps(job)
        self.assertNotIn(str(profile), serialized)
        self.assertNotIn("cookie", serialized.lower())

    def test_profile_icon_and_auth_actions_are_present_in_desktop_ui(self):
        root = Path(__file__).parents[1] / "desktop"
        html = (root / "index.html").read_text(encoding="utf-8")
        script = (root / "src" / "main.js").read_text(encoding="utf-8")
        self.assertIn('id="youtubeProfileButton"', html)
        self.assertIn('id="youtubeProfileDialog"', html)
        self.assertIn("Open Profile", html)
        self.assertNotIn("Use Anonymous", html)
        self.assertNotIn("youtubeUseAnonymousButton", script)
        self.assertIn('button("Open Profile", "profile"', script)
        self.assertIn('button("Retry", "retry"', script)

    def test_only_one_download_runs_at_once_for_five_jobs(self):
        job_runner.create_jobs([f"https://example.com/{index}" for index in range(5)])
        release = threading.Event()
        started = threading.Event()
        active = 0
        maximum = 0
        lock = threading.Lock()

        def download(job, directory):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            started.set()
            release.wait(2)
            with lock:
                active -= 1
            return self.source(job, directory)

        manager = self.manager(download)
        manager.tick()
        self.assertTrue(started.wait(1))
        for _ in range(10):
            manager.tick()
        self.assertEqual(maximum, 1)
        self.assertEqual(self.statuses().count("DOWNLOADING"), 1)
        release.set()

    def test_next_download_waits_for_virtual_cooldown(self):
        job_runner.create_jobs(["https://example.com/1", "https://example.com/2"])
        starts = []

        def download(job, directory):
            starts.append(self.clock())
            return self.source(job, directory)

        manager = self.manager(download)
        self.pump(
            manager,
            lambda: len(starts) == 1
            and bool(job_runner.list_jobs()[0].get("downloaded_at")),
        )
        self.clock.advance(54)
        for _ in range(5):
            manager.tick()
        self.assertEqual(len(starts), 1)
        self.clock.advance(1)
        self.pump(manager, lambda: len(starts) == 2)
        self.assertEqual(starts, [1000.0, 1055.0])

    def test_processing_continues_while_downloader_cools_and_prefetches_one(self):
        job_runner.create_jobs([
            "https://example.com/1", "https://example.com/2", "https://example.com/3"
        ])
        processing = threading.Event()
        release = threading.Event()
        downloads = []

        def download(job, directory):
            downloads.append(job["url"])
            return self.source(job, directory)

        def pipeline(job, directory, source):
            processing.set()
            release.wait(2)
            return self.pipeline(job, directory, source)

        manager = self.manager(download, pipeline)
        self.pump(manager, processing.is_set)
        self.assertEqual(len(downloads), 1)
        self.clock.advance(55)
        self.pump(manager, lambda: len(downloads) == 2 and "READY" in self.statuses())
        self.assertTrue(processing.is_set())
        self.clock.advance(55)
        for _ in range(10):
            manager.tick()
        self.assertEqual(len(downloads), 2)
        self.assertEqual(self.statuses().count("READY"), 1)
        release.set()

    def _assert_retry_schedule(self, message, expected):
        job_runner.create_jobs(["https://example.com/retry"])
        starts = []

        def download(_job, _directory):
            starts.append(self.clock())
            raise RuntimeError(message)

        manager = self.manager(download)
        for delay in expected:
            self.pump(
                manager,
                lambda: job_runner.list_jobs()[0].get("stage") == "retry_wait"
                and len(starts) == expected.index(delay) + 1,
            )
            self.clock.advance(delay)
            manager.tick()
            time.sleep(0.01)
        self.pump(manager, lambda: job_runner.list_jobs()[0]["status"] == "FAILED")
        expected_starts = [1000.0]
        for delay in expected:
            expected_starts.append(expected_starts[-1] + delay)
        self.assertEqual(starts, expected_starts)

    def test_transient_retry_uses_30_60_120_backoff(self):
        self._assert_retry_schedule("connection timed out", [30, 60, 120])

    def test_http_429_retry_uses_60_120_300_backoff(self):
        self._assert_retry_schedule("HTTP Error 429: Too Many Requests", [60, 120, 300])
        self.assertEqual(job_runner.list_jobs()[0]["download_error_code"], "HTTP_429")

    def test_final_failure_allows_next_job(self):
        job_runner.create_jobs(["https://example.com/fail", "https://example.com/next"])
        calls = []

        def download(job, directory):
            calls.append(job["url"])
            if job["url"].endswith("fail"):
                raise RuntimeError("unsupported URL")
            return self.source(job, directory)

        manager = self.manager(download)
        self.pump(manager, lambda: calls == ["https://example.com/fail", "https://example.com/next"])
        self.assertEqual(job_runner.list_jobs()[0]["status"], "FAILED")

    def test_restart_ready_source_is_processed_without_redownload(self):
        job = job_runner.create_jobs(["https://example.com/ready"])[0]
        source = job_runner._job_path(job["id"]).parent / "source.mp4"
        source.write_bytes(b"valid")
        job.update(status="INTERRUPTED", stage="interrupted")
        job_runner._write_job(job)
        with patch.object(job_runner, "_valid_existing_source", return_value=source):
            self.assertEqual(job_runner.retry_job(job["id"])["status"], "READY")
        downloads = []
        manager = self.manager(lambda *_args: downloads.append(True))
        self.pump(manager, lambda: job_runner.list_jobs()[0]["status"] == "DONE")
        self.assertEqual(downloads, [])

    def test_cancel_during_cooldown_is_immediate(self):
        jobs = job_runner.create_jobs(["https://example.com/1", "https://example.com/2"])
        starts = []

        def download(job, directory):
            starts.append(job["url"])
            return self.source(job, directory)

        manager = self.manager(download)
        self.pump(
            manager,
            lambda: len(starts) == 1
            and bool(job_runner.list_jobs()[0].get("downloaded_at")),
        )
        cancelled = job_runner.cancel_job(jobs[1]["id"])
        self.assertEqual(cancelled["status"], "CANCELLED")
        self.assertEqual(starts, ["https://example.com/1"])

    def test_forty_url_batch_has_no_burst_and_never_more_than_one_ready(self):
        urls = [f"https://example.com/{index}" for index in range(40)]
        job_runner.create_jobs(urls)
        starts = []
        max_ready = 0

        def download(job, directory):
            starts.append(self.clock())
            return self.source(job, directory)

        manager = self.manager(download)
        for downloaded_count in range(1, 41):
            self.pump(
                manager,
                lambda: sum(bool(job.get("downloaded_at")) for job in job_runner.list_jobs())
                >= downloaded_count,
            )
            max_ready = max(max_ready, self.statuses().count("READY"))
            if downloaded_count < 40:
                self.clock.advance(55)
        self.pump(manager, lambda: all(status == "DONE" for status in self.statuses()))
        self.assertEqual(len(starts), 40)
        self.assertTrue(all(right - left >= 55 for left, right in zip(starts, starts[1:])))
        self.assertLessEqual(max_ready, 1)

    def test_error_classification(self):
        cases = {
            "connection timed out": "NETWORK_TRANSIENT",
            "HTTP Error 429": "HTTP_429",
            "HTTP Error 403": "HTTP_403",
            "Sign in to confirm your age": "AUTH_REQUIRED",
            "Sign in to confirm you're not a bot": "BOT_CHALLENGE_OR_TOKEN",
            "PO Token required": "BOT_CHALLENGE_OR_TOKEN",
            "Video unavailable": "UNAVAILABLE",
            "Private video. Sign in if you have permission": "UNAVAILABLE",
            "Unsupported URL": "INVALID_URL",
            "something else": "UNKNOWN",
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(job_runner.classify_download_error(message), expected)


if __name__ == "__main__":
    unittest.main()
