from pathlib import Path

from audio_worker import AudioAnalysisWorker


class FakeDetector:
    def __init__(self):
        self.loaded = False
        self.active_device = "cpu"
        self.loads = 0

    def _load(self):
        self.loads += 1
        self.loaded = True
        return object()


class FakeRuntime:
    def __init__(self):
        self.detector = FakeDetector()
        self.process_calls = []

    def process(self, source, output, **options):
        self.process_calls.append((Path(source), Path(output), options))
        Path(options["report_path"]).write_text("{}", encoding="utf-8")
        return {"status": "DONE"}


def test_audio_worker_warms_once_and_reuses_runtime(tmp_path):
    runtime = FakeRuntime()
    silero_loads = []
    worker = AudioAnalysisWorker(
        runtime_factory=lambda: runtime,
        silero_loader=lambda: silero_loads.append(True),
    )

    assert worker.health()["status"] == "LOADING"
    assert worker.warm()["status"] == "READY"
    assert worker.warm()["status"] == "READY"
    assert runtime.detector.loads == 1
    assert silero_loads == [True]

    worker.process(
        tmp_path / "source.mp4", tmp_path / "report.json", tmp_path / "rendered.mp4",
    )
    assert runtime.process_calls[0][2]["analysis_only"] is True
    assert worker.health()["runtime_reuse_count"] == 1


def test_audio_worker_reports_warmup_error_without_qwen():
    def broken_runtime():
        raise RuntimeError("audio model unavailable")

    worker = AudioAnalysisWorker(runtime_factory=broken_runtime, silero_loader=lambda: None)
    health = worker.warm()
    assert health["status"] == "ERROR"
    assert "audio model unavailable" in health["error"]
