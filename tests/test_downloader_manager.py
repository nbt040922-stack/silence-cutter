import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

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
            "Unsupported URL": "INVALID_URL",
            "something else": "UNKNOWN",
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(job_runner.classify_download_error(message), expected)


if __name__ == "__main__":
    unittest.main()
