from __future__ import annotations

import argparse
import json
import os
import queue
import random
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from silence_cutter.runtime_paths import find_executable


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(os.environ.get("SILENCE_CUTTER_DATA_DIR", ROOT)).expanduser().resolve()
SETTINGS_PATH = DATA_ROOT / "desktop-settings.json"
ACTIVE = {"DOWNLOADING", "ANALYZING", "RENDERING"}
TERMINAL = {"DONE", "FAILED", "CANCELLED", "INTERRUPTED"}


@dataclass(frozen=True, slots=True)
class DownloaderManagerConfig:
    download_concurrency: int = 1
    process_concurrency: int = 1
    prefetch_depth: int = 1
    download_cooldown_min_seconds: float = 55.0
    download_cooldown_max_seconds: float = 70.0
    max_download_retries: int = 3

    def __post_init__(self) -> None:
        if self.download_concurrency != 1 or self.process_concurrency != 1:
            raise ValueError("desktop downloader and processor concurrency must remain 1")
        if self.prefetch_depth != 1:
            raise ValueError("desktop prefetch depth must remain 1")
        if not 0 <= self.download_cooldown_min_seconds <= self.download_cooldown_max_seconds:
            raise ValueError("invalid download cooldown range")
        if self.max_download_retries < 0:
            raise ValueError("max download retries must be non-negative")


TRANSIENT_BACKOFF = (30.0, 60.0, 120.0)
HTTP_429_BACKOFF = (60.0, 120.0, 300.0)


def _utf8_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    return environment


def _configure_utf8_stdio() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for attempt in range(20):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.005)


def default_settings() -> dict[str, Any]:
    data_root = Path(os.environ.get("SILENCE_CUTTER_DATA_DIR", ROOT)).expanduser().resolve()
    return {
        "workspace_folder": str((data_root / "workspace").resolve()),
        "output_folder": str((data_root / "outputs").resolve()),
        "max_concurrent_jobs": 1,
        "download_concurrency": 1,
        "process_concurrency": 1,
        "prefetch_depth": 1,
        "download_cooldown_min_seconds": 55,
        "download_cooldown_max_seconds": 70,
        "max_download_retries": 3,
    }


def load_settings() -> dict[str, Any]:
    settings = default_settings()
    if SETTINGS_PATH.is_file():
        try:
            saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            settings.update({key: saved[key] for key in settings if key in saved})
        except (OSError, ValueError, TypeError):
            pass
    settings["max_concurrent_jobs"] = 1
    settings.update(download_concurrency=1, process_concurrency=1, prefetch_depth=1)
    return settings


def save_settings(payload: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings()
    for key in ("workspace_folder", "output_folder"):
        value = str(payload.get(key, "")).strip()
        if value:
            settings[key] = str(Path(value).expanduser().resolve())
    settings["max_concurrent_jobs"] = 1
    settings.update(download_concurrency=1, process_concurrency=1, prefetch_depth=1)
    Path(settings["workspace_folder"]).mkdir(parents=True, exist_ok=True)
    Path(settings["output_folder"]).mkdir(parents=True, exist_ok=True)
    _atomic_json(SETTINGS_PATH, settings)
    return settings


def _jobs_dir(settings: dict[str, Any] | None = None) -> Path:
    return Path((settings or load_settings())["workspace_folder"]) / "jobs"


def _job_path(job_id: str, settings: dict[str, Any] | None = None) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise ValueError("invalid job id")
    return _jobs_dir(settings) / job_id / "job.json"


def _read_job(path: Path) -> dict[str, Any]:
    for attempt in range(20):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.005)
    raise AssertionError("unreachable")


def _write_job(job: dict[str, Any], settings: dict[str, Any] | None = None) -> None:
    _atomic_json(_job_path(job["id"], settings), job)


def _valid_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def create_jobs(urls: list[str]) -> list[dict[str, Any]]:
    settings = load_settings()
    jobs = []
    batch_order = time.time_ns()
    for index, raw in enumerate(urls):
        url = raw.strip()
        if not url:
            continue
        job_id = uuid.uuid4().hex
        job_dir = _jobs_dir(settings) / job_id
        job_dir.mkdir(parents=True, exist_ok=False)
        (job_dir / "logs").mkdir()
        job = {
            "id": job_id,
            "url": url,
            "title": urlparse(url).netloc or url,
            "duration": None,
            "status": "QUEUED" if _valid_url(url) else "FAILED",
            "progress": 0 if _valid_url(url) else None,
            "stage": "waiting_to_download" if _valid_url(url) else "failed",
            "created_at": _now(),
            "queue_order": batch_order + index,
            "started_at": None,
            "finished_at": _now() if not _valid_url(url) else None,
            "source_path": None,
            "output_path": None,
            "report_path": None,
            "error": None if _valid_url(url) else "URL must use http or https",
            "cancel_requested": False,
            "pid": None,
            "download_retry_count": 0,
            "download_error_code": None,
            "download_retry_at": None,
            "download_cooldown_until": None,
        }
        _write_job(job, settings)
        jobs.append(job)
    return jobs


def list_jobs() -> list[dict[str, Any]]:
    jobs = []
    for path in _jobs_dir().glob("*/job.json"):
        try:
            jobs.append(_read_job(path))
        except (OSError, ValueError, TypeError):
            continue
    return sorted(
        jobs,
        key=lambda item: (
            item.get("created_at", ""), item.get("queue_order", 0), item.get("id", "")
        ),
    )


def _terminate_tree(pid: int | None) -> None:
    if not pid:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def cancel_job(job_id: str) -> dict[str, Any]:
    path = _job_path(job_id)
    job = _read_job(path)
    if job["status"] in {"DONE", "FAILED", "CANCELLED"}:
        return job
    job["cancel_requested"] = True
    _write_job(job)
    _terminate_tree(job.get("pid"))
    job = _read_job(path)
    job.update(
        status="CANCELLED", stage="cancelled", progress=None,
        finished_at=_now(), pid=None,
    )
    _write_job(job)
    return job


def retry_job(job_id: str) -> dict[str, Any]:
    job = _read_job(_job_path(job_id))
    if job["status"] not in TERMINAL:
        raise ValueError("only finished, failed, cancelled or interrupted jobs can retry")
    source = _valid_existing_source(_job_path(job_id).parent)
    job.update(
        status="READY" if source else "QUEUED",
        stage="ready" if source else "waiting_to_download", progress=100 if source else 0,
        source_path=str(source.resolve()) if source else None, started_at=None,
        finished_at=None, error=None, cancel_requested=False, pid=None,
        download_retry_count=0, download_error_code=None, download_retry_at=None,
    )
    _write_job(job)
    return job


def remove_job(job_id: str) -> dict[str, bool]:
    path = _job_path(job_id)
    job = _read_job(path)
    if job["status"] in ACTIVE or job["status"] == "READY":
        raise ValueError("cancel an active job before removing it")
    shutil.rmtree(path.parent)
    return {"removed": True}


def recover_interrupted() -> int:
    count = 0
    for job in list_jobs():
        if job["status"] not in ACTIVE:
            continue
        _terminate_tree(job.get("pid"))
        job.update(
            status="INTERRUPTED", stage="interrupted", progress=None,
            finished_at=_now(), error="Application stopped before the job completed",
            cancel_requested=False, pid=None,
        )
        _write_job(job)
        count += 1
    return count


def _yt_dlp_command() -> list[str] | None:
    executable = shutil.which("yt-dlp")
    if executable:
        return [executable]
    try:
        if subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--version"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=_utf8_environment(),
        ).returncode == 0:
            return [sys.executable, "-m", "yt_dlp"]
    except OSError:
        pass
    return None


def health() -> dict[str, Any]:
    from backend.hardware import data_dir

    probe_path = data_dir() / "hardware_probe.json"
    try:
        probe = json.loads(probe_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        probe = {}
    nvidia = bool(probe.get("nvidia_detected"))
    nvenc = bool((probe.get("nvenc") or {}).get("initializes"))
    sensevoice = probe.get("sensevoice") or {}
    return {
        "gpu": nvidia,
        "gpu_model": ((probe.get("gpus") or [{}])[0]).get("model") if nvidia else None,
        "vram_mib": ((probe.get("gpus") or [{}])[0]).get("vram_mib") if nvidia else None,
        "cuda": bool((probe.get("cuda") or {}).get("available")),
        "nvenc": nvenc,
        "ffmpeg": find_executable("ffmpeg") is not None and find_executable("ffprobe") is not None,
        "yt_dlp": _yt_dlp_command() is not None,
        "python": Path(sys.executable).is_file(),
        "pipeline": (ROOT / "production" / "__main__.py").is_file(),
        "sensevoice_model": bool(sensevoice.get("active_device")),
        "sensevoice_device": sensevoice.get("active_device"),
        "hardware_probe_pending": not probe_path.is_file(),
        "hardware_probe_path": str(probe_path),
        "python_path": sys.executable,
    }


def _log(job_dir: Path, message: str) -> None:
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with (job_dir / "logs" / "job.log").open("a", encoding="utf-8") as stream:
        stream.write(f"{stamp} {message}\n")


def _command_log(job_dir: Path, command: list[str]) -> None:
    with (job_dir / "logs" / "commands.log").open("a", encoding="utf-8") as stream:
        stream.write(subprocess.list2cmdline(command) + "\n")


def _refresh_cancelled(job: dict[str, Any]) -> bool:
    try:
        current = _read_job(_job_path(job["id"]))
        return bool(current.get("cancel_requested")) or current["status"] == "CANCELLED"
    except (OSError, ValueError):
        return True


def _run_process(
    command: list[str], job: dict[str, Any], job_dir: Path,
    *, on_line: Callable[[str], None] | None = None,
    detect_render: bool = False,
) -> None:
    _command_log(job_dir, command)
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        env=_utf8_environment(),
        creationflags=creationflags,
        start_new_session=os.name != "nt",
    )
    job["pid"] = process.pid
    _write_job(job)
    lines: queue.Queue[str | None] = queue.Queue()

    def read_output() -> None:
        if process.stdout:
            for output_line in process.stdout:
                lines.put(output_line)
        lines.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    output_log = job_dir / "logs" / "process.log"
    with output_log.open("a", encoding="utf-8") as log:
        while process.poll() is None:
            try:
                line = lines.get(timeout=0.2)
            except queue.Empty:
                line = ""
            if line:
                log.write(line)
                log.flush()
                if on_line:
                    on_line(line.rstrip())
            if detect_render and job["status"] == "ANALYZING" and any(
                job_dir.glob(".rendered-*.mp4")
            ):
                job.update(status="RENDERING", stage="rendering", progress=None)
                _write_job(job)
                _log(job_dir, "Rendering started")
            if _refresh_cancelled(job):
                _terminate_tree(process.pid)
                raise RuntimeError("Job cancelled")
        reader.join(timeout=2)
        while not lines.empty():
            line = lines.get_nowait()
            if line:
                log.write(line)
                if on_line:
                    on_line(line.rstrip())
        job["pid"] = None
        _write_job(job)
        if process.returncode:
            raise RuntimeError(f"Command failed with exit code {process.returncode}; see process.log")


def _download(job: dict[str, Any], job_dir: Path) -> Path:
    yt = _yt_dlp_command()
    if not yt:
        raise RuntimeError("yt-dlp was not found; install the desktop optional dependencies")
    metadata_command = [
        *yt, "--ignore-config", "--dump-single-json", "--skip-download",
        "--no-playlist", job["url"],
    ]
    metadata_lines: list[str] = []
    _run_process(metadata_command, job, job_dir, on_line=metadata_lines.append)
    info = json.loads("\n".join(metadata_lines))
    job["title"] = str(info.get("title") or job["title"])
    job["duration"] = float(info["duration"]) if info.get("duration") else None
    _write_job(job)
    output_template = str(job_dir / "source.%(ext)s")
    download_command = [
        *yt, "--ignore-config", "--newline", "--no-playlist", "--progress",
        "--progress-template", "download:%(progress._percent_str)s",
        "-f", "bv*+ba/b", "--merge-output-format", "mp4",
        "-o", output_template, job["url"],
    ]
    percent = re.compile(r"download:\s*([0-9]+(?:\.[0-9]+)?)%")

    def update(line: str) -> None:
        match = percent.search(line)
        if match:
            job["progress"] = min(100.0, float(match.group(1)))
            _write_job(job)

    _run_process(download_command, job, job_dir, on_line=update)
    sources = sorted(
        path for path in job_dir.glob("source.*")
        if path.is_file() and not path.name.endswith((".part", ".ytdl"))
    )
    if not sources:
        raise RuntimeError("yt-dlp completed without a source media file")
    return sources[0]


def _valid_existing_source(job_dir: Path) -> Path | None:
    ffprobe = find_executable("ffprobe")
    if not ffprobe:
        return None
    sources = sorted(
        path for path in job_dir.glob("source.*")
        if path.is_file() and path.stat().st_size > 0
        and not path.name.endswith((".part", ".ytdl"))
    )
    for source in sources:
        if subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", source],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=_utf8_environment(),
        ).returncode == 0:
            return source
    return None


def classify_download_error(message: str) -> str:
    value = message.lower()
    if re.search(r"(?:http error\s*)?429|too many requests", value):
        return "HTTP_429"
    if re.search(r"(?:http error\s*)?403|forbidden", value):
        return "HTTP_403"
    if any(item in value for item in (
        "not a bot", "bot challenge", "po token", "token challenge",
    )):
        return "BOT_CHALLENGE_OR_TOKEN"
    if any(item in value for item in (
        "sign in", "login required", "private video", "members-only", "confirm your age",
    )):
        return "AUTH_REQUIRED"
    if any(item in value for item in (
        "video unavailable", "has been removed", "not available", "geo-restricted",
    )):
        return "UNAVAILABLE"
    if any(item in value for item in ("unsupported url", "invalid url")):
        return "INVALID_URL"
    if any(item in value for item in (
        "timed out", "timeout", "connection reset", "temporary failure",
        "network is unreachable", "unable to download webpage",
    )):
        return "NETWORK_TRANSIENT"
    return "UNKNOWN"


def _process_log_tail(job_dir: Path, start: int = 0, limit: int = 64_000) -> str:
    path = job_dir / "logs" / "process.log"
    if not path.is_file():
        return ""
    with path.open("rb") as stream:
        stream.seek(max(start, path.stat().st_size - limit))
        return stream.read().decode("utf-8", "replace")


def _download_error_text(code: str) -> str:
    return {
        "NETWORK_TRANSIENT": "Temporary network failure while downloading",
        "HTTP_429": "YouTube rate limited this machine (HTTP 429)",
        "HTTP_403": "YouTube refused the anonymous download (HTTP 403)",
        "AUTH_REQUIRED": "This video requires authentication; anonymous download only in V1",
        "BOT_CHALLENGE_OR_TOKEN": "YouTube bot/token challenge; automatic retry disabled",
        "UNAVAILABLE": "Video is unavailable",
        "INVALID_URL": "URL is invalid or unsupported by yt-dlp",
        "UNKNOWN": "yt-dlp failed for an unknown reason",
    }[code]


def _sanitize_title(title: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(" .")
    value = re.sub(r"\s+", " ", value).strip()
    shortened: list[str] = []
    utf16_units = 0
    for character in value:
        character_units = len(character.encode("utf-16-le")) // 2
        if utf16_units + character_units > 160:
            break
        shortened.append(character)
        utf16_units += character_units
    value = "".join(shortened).rstrip(" .")
    reserved = {
        "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    if not value:
        value = "video"
    elif value.split(".", 1)[0].upper() in reserved:
        value = f"_{value}"
    return value


def _unique_output(folder: Path, title: str) -> Path:
    base = folder / f"{_sanitize_title(title)}_done.mp4"
    if not base.exists():
        return base
    for index in range(2, 10_000):
        candidate = folder / f"{_sanitize_title(title)}_done_{index}.mp4"
        if not candidate.exists():
            return candidate
    raise RuntimeError("could not allocate a unique output filename")


def _pipeline(job: dict[str, Any], job_dir: Path, source: Path) -> tuple[Path, Path]:
    rendered = job_dir / "rendered.mp4"
    report = job_dir / "pipeline_report.json"
    command = [
        sys.executable, "-m", "production", str(source), "-o", str(rendered),
        "--report", str(report), "--debug",
    ]
    _run_process(command, job, job_dir, detect_render=True)
    if not rendered.is_file() or not report.is_file():
        raise RuntimeError("production pipeline completed without output/report")
    return rendered, report


def _process_ready_job(
    job: dict[str, Any],
    *, pipeline: Callable[[dict[str, Any], Path, Path], tuple[Path, Path]] = _pipeline,
) -> dict[str, Any]:
    settings = load_settings()
    job_dir = _job_path(job["id"], settings).parent
    source = Path(str(job.get("source_path") or ""))
    try:
        if not source.is_file():
            raise RuntimeError("downloaded source is missing")
        job.update(status="ANALYZING", stage="analyzing", progress=None, error=None)
        _write_job(job, settings)
        _log(job_dir, "Production pipeline started")
        rendered, report = pipeline(job, job_dir, source)
        outputs = Path(settings["output_folder"])
        outputs.mkdir(parents=True, exist_ok=True)
        destination = _unique_output(outputs, job["title"])
        shutil.copy2(rendered, destination)
        job.update(
            status="DONE", stage="done", progress=100, finished_at=_now(),
            output_path=str(destination.resolve()), report_path=str(report.resolve()), pid=None,
        )
        _write_job(job, settings)
        _log(job_dir, f"Done: {destination}")
    except Exception as exc:
        current = _read_job(_job_path(job["id"], settings))
        cancelled = current.get("cancel_requested") or current["status"] == "CANCELLED"
        job.update(
            status="CANCELLED" if cancelled else "FAILED",
            stage="cancelled" if cancelled else "failed", progress=None,
            finished_at=_now(), error=None if cancelled else str(exc), pid=None,
        )
        _write_job(job, settings)
        _log(job_dir, f"{'Cancelled' if cancelled else 'Failed'}: {exc}")
    return job


def _download_attempt(
    job_id: str,
    *, downloader: Callable[[dict[str, Any], Path], Path] = _download,
    config: DownloaderManagerConfig,
    clock: Callable[[], float] = time.time,
    cooldown: Callable[[float, float], float] = random.uniform,
) -> dict[str, Any]:
    settings = load_settings()
    path = _job_path(job_id, settings)
    job = _read_job(path)
    job_dir = path.parent
    if job["status"] not in {"QUEUED", "DOWNLOADING"}:
        return job
    if job["status"] == "QUEUED":
        source = _valid_existing_source(job_dir)
        if source:
            job.update(
                status="READY", stage="ready", progress=100,
                source_path=str(source.resolve()), error=None,
            )
            _write_job(job, settings)
            _log(job_dir, f"Existing source reused: {source}")
            return job
    job.update(
        status="DOWNLOADING", stage="downloading", progress=0,
        started_at=job.get("started_at") or _now(), finished_at=None,
        error=None, cancel_requested=False, download_retry_at=None,
    )
    _write_job(job, settings)
    attempt = int(job.get("download_retry_count") or 0) + 1
    _log(job_dir, f"Download attempt {attempt} started (anonymous)")
    process_log = job_dir / "logs" / "process.log"
    log_start = process_log.stat().st_size if process_log.is_file() else 0
    try:
        source = downloader(job, job_dir)
        current = _read_job(path)
        if current.get("cancel_requested") or current["status"] == "CANCELLED":
            return current
        cooldown_seconds = cooldown(
            config.download_cooldown_min_seconds,
            config.download_cooldown_max_seconds,
        )
        job.update(
            source_path=str(source.resolve()), status="READY", stage="ready", progress=100,
            pid=None, error=None, download_error_code=None, download_retry_at=None,
            downloaded_at=_now(), download_cooldown_until=clock() + cooldown_seconds,
        )
        _write_job(job, settings)
        _log(job_dir, f"Download complete; downloader cooldown {cooldown_seconds:.0f}s")
    except Exception as exc:
        current = _read_job(path)
        if current.get("cancel_requested") or current["status"] == "CANCELLED":
            return current
        detail = f"{exc}\n{_process_log_tail(job_dir, log_start)}"
        code = classify_download_error(detail)
        retry_count = int(job.get("download_retry_count") or 0)
        backoff = HTTP_429_BACKOFF if code == "HTTP_429" else TRANSIENT_BACKOFF
        retryable = code in {"NETWORK_TRANSIENT", "HTTP_429"}
        if retryable and retry_count < config.max_download_retries:
            delay = backoff[min(retry_count, len(backoff) - 1)]
            job.update(
                status="DOWNLOADING", stage="retry_wait", progress=None, pid=None,
                download_retry_count=retry_count + 1,
                download_error_code=code, download_retry_at=clock() + delay,
                error=f"{_download_error_text(code)}; retry in {delay:.0f}s",
            )
            _write_job(job, settings)
            _log(
                job_dir,
                f"{code}; retry {retry_count + 1}/{config.max_download_retries} in {delay:.0f}s",
            )
        else:
            job.update(
                status="FAILED", stage="failed", progress=None, pid=None,
                finished_at=_now(), download_error_code=code, download_retry_at=None,
                error=_download_error_text(code),
            )
            _write_job(job, settings)
            _log(job_dir, f"Download failed [{code}]: {_download_error_text(code)}")
    return job


class DownloadManager:
    def __init__(
        self,
        *,
        config: DownloaderManagerConfig | None = None,
        downloader: Callable[[dict[str, Any], Path], Path] = _download,
        pipeline: Callable[[dict[str, Any], Path, Path], tuple[Path, Path]] = _pipeline,
        clock: Callable[[], float] = time.time,
        cooldown: Callable[[float, float], float] = random.uniform,
    ) -> None:
        if config is None:
            settings = load_settings()
            config = DownloaderManagerConfig(**{
                key: settings[key] for key in (
                    "download_concurrency", "process_concurrency", "prefetch_depth",
                    "download_cooldown_min_seconds", "download_cooldown_max_seconds",
                    "max_download_retries",
                )
            })
        self.config = config
        self.downloader = downloader
        self.pipeline = pipeline
        self.clock = clock
        self.cooldown = cooldown
        self.download_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="download")
        self.process_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="process")
        self.download_future: Future[dict[str, Any]] | None = None
        self.process_future: Future[dict[str, Any]] | None = None

    @staticmethod
    def _reap(future: Future[dict[str, Any]] | None) -> Future[dict[str, Any]] | None:
        if future is not None and future.done():
            future.result()
            return None
        return future

    def tick(self) -> None:
        self.download_future = self._reap(self.download_future)
        self.process_future = self._reap(self.process_future)
        jobs = list_jobs()
        if self.process_future is None:
            ready = next((job for job in jobs if job["status"] == "READY"), None)
            if ready:
                self.process_future = self.process_pool.submit(
                    _process_ready_job, ready, pipeline=self.pipeline
                )
        if self.download_future is not None:
            return
        retrying = next((
            job for job in jobs
            if job["status"] == "DOWNLOADING" and job.get("stage") == "retry_wait"
        ), None)
        if retrying:
            if float(retrying.get("download_retry_at") or 0) <= self.clock():
                self.download_future = self.download_pool.submit(
                    _download_attempt, retrying["id"], downloader=self.downloader,
                    config=self.config, clock=self.clock, cooldown=self.cooldown,
                )
            return
        if sum(job["status"] == "READY" for job in jobs) >= self.config.prefetch_depth:
            return
        cooldown_until = max(
            (float(job.get("download_cooldown_until") or 0) for job in jobs), default=0.0
        )
        if self.clock() < cooldown_until:
            return
        queued = next((job for job in jobs if job["status"] == "QUEUED"), None)
        if queued:
            self.download_future = self.download_pool.submit(
                _download_attempt, queued["id"], downloader=self.downloader,
                config=self.config, clock=self.clock, cooldown=self.cooldown,
            )

    def close(self) -> None:
        self.download_pool.shutdown(wait=True, cancel_futures=True)
        self.process_pool.shutdown(wait=True, cancel_futures=True)


def process_job(
    job: dict[str, Any],
    *, downloader: Callable[[dict[str, Any], Path], Path] = _download,
    pipeline: Callable[[dict[str, Any], Path, Path], tuple[Path, Path]] = _pipeline,
) -> dict[str, Any]:
    settings = load_settings()
    job_dir = _job_path(job["id"], settings).parent
    try:
        job.update(
            status="DOWNLOADING", stage="downloading", progress=0,
            started_at=job.get("started_at") or _now(), finished_at=None,
            error=None, cancel_requested=False,
        )
        _write_job(job, settings)
        _log(job_dir, "Download started")
        source = downloader(job, job_dir)
        job.update(source_path=str(source.resolve()), status="READY", stage="ready", progress=100)
        _write_job(job, settings)
        return _process_ready_job(job, pipeline=pipeline)
    except Exception as exc:
        current = _read_job(_job_path(job["id"], settings))
        cancelled = current.get("cancel_requested") or current["status"] == "CANCELLED"
        job.update(
            status="CANCELLED" if cancelled else "FAILED",
            stage="cancelled" if cancelled else "failed", progress=None,
            finished_at=_now(), error=None if cancelled else str(exc), pid=None,
        )
        _write_job(job, settings)
        _log(job_dir, f"{'Cancelled' if cancelled else 'Failed'}: {exc}")
    return job


def process_next_job(
    *, downloader: Callable[[dict[str, Any], Path], Path] = _download,
    pipeline: Callable[[dict[str, Any], Path, Path], tuple[Path, Path]] = _pipeline,
) -> dict[str, Any] | None:
    queued = next((job for job in list_jobs() if job["status"] == "QUEUED"), None)
    return process_job(queued, downloader=downloader, pipeline=pipeline) if queued else None


def worker(poll_seconds: float = 0.75) -> None:
    data_root = Path(os.environ.get("SILENCE_CUTTER_DATA_DIR", ROOT)).expanduser().resolve()
    lock_path = data_root / "desktop-worker.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0)
            if lock_file.read(1) == b"":
                lock_file.write(b"0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_file.close()
        raise RuntimeError("another desktop worker is already running") from None

    manager = DownloadManager()
    try:
        from backend.hardware import write_startup_probe

        write_startup_probe()
        recover_interrupted()
        while True:
            manager.tick()
            time.sleep(poll_seconds)
    finally:
        manager.close()
        lock_file.close()


def _rpc(request: dict[str, Any]) -> Any:
    operation = request.get("operation")
    payload = request.get("payload") or {}
    if operation == "create_jobs":
        return create_jobs(list(payload.get("urls") or []))
    if operation == "list_jobs":
        return list_jobs()
    if operation == "cancel_job":
        return cancel_job(str(payload["id"]))
    if operation == "retry_job":
        return retry_job(str(payload["id"]))
    if operation == "remove_job":
        return remove_job(str(payload["id"]))
    if operation == "health":
        return health()
    if operation == "hardware_benchmark":
        if any(job["status"] not in TERMINAL for job in list_jobs()):
            raise RuntimeError("hardware benchmark requires an empty, idle queue")
        from backend.hardware import run_hardware_benchmark

        return run_hardware_benchmark()
    if operation == "plan_tiktok_formatter":
        job_file = _job_path(str(payload["id"]))
        job = _read_job(job_file)
        if job["status"] != "DONE":
            raise RuntimeError("TikTok formatter requires a DONE job")
        from formatter.planner import plan_done_job

        plan_path = job_file.parent / "format_plan.json"
        preview_path = job_file.parent / "part1_preview.png"
        plan = plan_done_job(
            job_file, output_path=plan_path, preview_path=preview_path,
            format_anyway=bool(payload.get("format_anyway", False)),
        )
        return {
            "formatter_status": plan["formatter_status"],
            "format_plan": str(plan_path),
            "preview": plan["preview_path"],
            "parts": plan["parts"],
            "review_reason": plan.get("review_reason"),
        }
    if operation == "get_settings":
        return load_settings()
    if operation == "save_settings":
        return save_settings(payload)
    if operation == "read_log":
        job_dir = _job_path(str(payload["id"])).parent
        concise = (job_dir / "logs" / "job.log").read_text(encoding="utf-8") if (job_dir / "logs" / "job.log").is_file() else ""
        advanced = ""
        for name in ("commands.log", "process.log"):
            path = job_dir / "logs" / name
            if path.is_file():
                advanced += f"\n--- {name} ---\n" + path.read_text(encoding="utf-8", errors="replace")[-30_000:]
        return {"concise": concise, "advanced": advanced}
    raise ValueError(f"unsupported operation: {operation}")


def main() -> None:
    _configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Silence Cutter desktop job runner")
    parser.add_argument("mode", choices=("rpc", "worker"))
    args = parser.parse_args()
    if args.mode == "worker":
        worker()
        return
    try:
        request = json.loads(sys.stdin.read())
        print(json.dumps({"ok": True, "result": _rpc(request)}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
