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
from statistics import median
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
AUTH_FAILURE_CODES = {"HTTP_403", "AUTH_REQUIRED", "BOT_CHALLENGE_OR_TOKEN"}
SUPPORTED_LOCAL_VIDEO_EXTENSIONS = {".mp4"}


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
    input_root = Path(os.environ.get("SILENCE_INPUT_DIR", "D:/Vlog/Input")).expanduser().resolve()
    return {
        "workspace_folder": str((data_root / "workspace").resolve()),
        "input_folder": str(input_root),
        "output_folder": str(Path("F:/Vlog-tool").resolve()),
        "input_mode": "LOCAL_FOLDER",
        "watch_input_folder": False,
        "local_file_stability_seconds": 7.0,
        "max_concurrent_jobs": 1,
        "download_concurrency": 1,
        "process_concurrency": 1,
        "prefetch_depth": 1,
        "download_cooldown_min_seconds": 55,
        "download_cooldown_max_seconds": 70,
        "max_download_retries": 3,
        "keep_clean_master": False,
        "youtube_profile_path": str((SETTINGS_PATH.parent / "youtube_profile").resolve()),
        "last_session_test": None,
        "last_session_status": None,
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
    settings["input_mode"] = "LOCAL_FOLDER"
    configured_input = os.environ.get("SILENCE_INPUT_DIR")
    if configured_input:
        settings["input_folder"] = str(Path(configured_input).expanduser().resolve())
    settings["youtube_profile_path"] = str(
        (SETTINGS_PATH.parent / "youtube_profile").resolve()
    )
    settings.update(download_concurrency=1, process_concurrency=1, prefetch_depth=1)
    return settings


def save_settings(payload: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings()
    configured_input = os.environ.get("SILENCE_INPUT_DIR")
    for key in ("workspace_folder", "input_folder", "output_folder"):
        if key == "input_folder" and configured_input:
            settings[key] = str(Path(configured_input).expanduser().resolve())
            continue
        value = str(payload.get(key, "")).strip()
        if value:
            settings[key] = str(Path(value).expanduser().resolve())
    settings["max_concurrent_jobs"] = 1
    settings["keep_clean_master"] = bool(payload.get(
        "keep_clean_master", settings["keep_clean_master"]
    ))
    settings["input_mode"] = "LOCAL_FOLDER"
    settings["watch_input_folder"] = bool(payload.get(
        "watch_input_folder", settings["watch_input_folder"]
    ))
    settings.update(download_concurrency=1, process_concurrency=1, prefetch_depth=1)
    Path(settings["workspace_folder"]).mkdir(parents=True, exist_ok=True)
    Path(settings["input_folder"]).mkdir(parents=True, exist_ok=True)
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


def _timestamp(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _seconds_between(start: str | None, end: str | None) -> float | None:
    first, last = _timestamp(start), _timestamp(end)
    return max(0.0, last - first) if first is not None and last is not None else None


def _read_jobs_raw(settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    jobs = []
    for path in _jobs_dir(settings).glob("*/job.json"):
        try:
            jobs.append(_read_job(path))
        except (OSError, ValueError, TypeError):
            continue
    return jobs


def _analysis_measurement(job: dict[str, Any]) -> float | None:
    if job.get("analysis_time") is not None:
        return float(job["analysis_time"])
    report = Path(str(job.get("report_path") or ""))
    if report.is_file():
        try:
            value = json.loads(report.read_text(encoding="utf-8")).get("analysis_time")
            return float(value) if value is not None else None
        except (OSError, TypeError, ValueError):
            pass
    return None


def _eta_history(jobs: list[dict[str, Any]], limit: int = 8) -> dict[str, float | None]:
    completed = sorted(
        (job for job in jobs if job.get("status") == "DONE" and job.get("finished_at")),
        key=lambda item: item.get("finished_at") or "", reverse=True,
    )[:limit]
    analysis_rates = []
    formatter_speeds = []
    for job in completed:
        duration = float(job.get("duration") or 0)
        analysis = _analysis_measurement(job)
        if duration > 0 and analysis is not None and analysis > 0:
            analysis_rates.append(analysis / (duration / 60.0))
        speed = float(job.get("average_render_speed") or 0)
        if speed > 0 and job.get("formatter_status") == "DONE":
            formatter_speeds.append(speed)
    return {
        "analysis_seconds_per_video_minute": (
            float(median(analysis_rates)) if analysis_rates else None
        ),
        "formatter_render_speed_x": (
            float(median(formatter_speeds)) if formatter_speeds else None
        ),
    }


def _eta_snapshot(
    job: dict[str, Any], history: dict[str, float | None], *, now: float | None = None,
) -> dict[str, Any]:
    now = time.time() if now is None else now
    started = _timestamp(job.get("started_at"))
    finished = _timestamp(job.get("finished_at"))
    elapsed = max(0.0, (finished or now) - started) if started is not None else 0.0
    formatter_status = job.get("formatter_status")
    if formatter_status == "NEEDS_REVIEW":
        eta, eta_status, progress = None, "NOT_APPLICABLE", 100.0
    elif finished is not None and job.get("status") == "DONE" and formatter_status in {None, "DONE"}:
        eta, eta_status, progress = 0.0, "DONE", 100.0
    elif finished is not None and job.get("status") in TERMINAL:
        eta, eta_status = None, "NOT_APPLICABLE"
        progress = float(job.get("overall_progress") or 0)
    else:
        eta = None
        duration = float(job.get("duration") or 0)
        clean_duration = float(job.get("clean_video_duration") or duration)
        analysis_rate = history.get("analysis_seconds_per_video_minute")
        render_speed = history.get("formatter_render_speed_x")
        analysis_estimate = (
            duration / 60.0 * analysis_rate if duration > 0 and analysis_rate else None
        )
        render_estimate = clean_duration / render_speed if clean_duration > 0 and render_speed else None
        if formatter_status == "RENDERING":
            live_eta = job.get("formatter_eta_seconds")
            if live_eta is not None:
                eta = max(0.0, float(live_eta))
            elif render_estimate is not None:
                rendered = min(1.0, max(0.0, float(job.get("formatter_progress") or 0) / 100.0))
                eta = render_estimate * (1.0 - rendered)
        elif formatter_status == "PLANNING" or job.get("stage") == "formatting":
            eta = render_estimate
        elif job.get("status") == "ANALYZING":
            analysis_started = _timestamp(job.get("analysis_started_at")) or now
            analysis_remaining = (
                max(0.0, analysis_estimate - (now - analysis_started))
                if analysis_estimate is not None else None
            )
            if analysis_remaining is not None and render_estimate is not None:
                eta = analysis_remaining + render_estimate
        elif job.get("status") == "READY":
            if analysis_estimate is not None and render_estimate is not None:
                eta = analysis_estimate + render_estimate
        elif job.get("status") == "DOWNLOADING" and job.get("input_mode") != "LOCAL_FOLDER":
            progress_value = float(job.get("progress") or 0)
            download_started = _timestamp(job.get("download_started_at")) or started
            download_elapsed = max(0.0, now - download_started) if download_started else 0.0
            if (
                progress_value >= 5.0 and download_elapsed >= 2.0
                and analysis_estimate is not None and render_estimate is not None
            ):
                download_remaining = max(
                    0.0, download_elapsed / (progress_value / 100.0) - download_elapsed
                )
                eta = download_remaining + analysis_estimate + render_estimate
        eta_status = "READY" if eta is not None else "ESTIMATING"
        calculated = elapsed / (elapsed + eta) * 100.0 if eta is not None and elapsed + eta > 0 else 0.0
        progress = min(99.9, max(float(job.get("overall_progress") or 0), calculated))
    estimated_total = elapsed + eta if eta is not None else None
    initial = job.get("estimated_total_time_at_start")
    if initial is None and estimated_total is not None and eta_status == "READY":
        initial = estimated_total
    return {
        "total_elapsed_seconds": elapsed,
        "total_eta_seconds": eta,
        "estimated_total_job_time": estimated_total,
        "estimated_total_time_at_start": initial,
        "overall_progress": progress,
        "total_job_progress": progress,
        "eta_status": eta_status,
        **history,
    }


def _write_job(job: dict[str, Any], settings: dict[str, Any] | None = None) -> None:
    settings = settings or load_settings()
    job.update(_eta_snapshot(job, _eta_history(_read_jobs_raw(settings))))
    _atomic_json(_job_path(job["id"], settings), job)


def _finalize_job(job: dict[str, Any], stage: str) -> dict[str, Any]:
    finished_at = _now()
    total_job_time = _seconds_between(job.get("started_at"), finished_at)
    initial = job.get("estimated_total_time_at_start")
    job.update(
        stage=stage,
        finished_at=finished_at,
        download_time=job.get("download_time") or _seconds_between(
            job.get("download_started_at") or job.get("started_at"),
            job.get("downloaded_at"),
        ),
        format_render_time=job.get("total_format_render_time") or job.get("formatter_render_time"),
        total_job_time=total_job_time,
        final_estimation_error=(
            abs(total_job_time - float(initial))
            if total_job_time is not None and initial is not None else None
        ),
    )
    _write_job(job)
    report_path = Path(str(job.get("report_path") or ""))
    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report.update({key: job.get(key) for key in (
                "download_time", "analysis_time", "format_render_time", "total_job_time",
                "estimated_total_time_at_start", "final_estimation_error",
            )})
            _atomic_json(report_path, report)
        except (OSError, TypeError, ValueError):
            pass
    return job


def _verified_formatter_outputs(job: dict[str, Any]) -> bool:
    """Return true only when every planned formatter output is present and non-empty."""
    outputs = job.get("formatted_outputs") or []
    plan_path = Path(str(job.get("format_plan") or ""))
    if not outputs and plan_path.is_file():
        try:
            outputs = json.loads(plan_path.read_text(encoding="utf-8")).get("formatted_outputs") or []
        except (OSError, TypeError, ValueError):
            return False
    if not outputs:
        return False
    expected = job.get("formatter_part_count")
    if expected is None and plan_path.is_file():
        try:
            expected = json.loads(plan_path.read_text(encoding="utf-8")).get("part_count")
        except (OSError, TypeError, ValueError):
            expected = None
    if expected is not None and len(outputs) != int(expected):
        return False
    base = plan_path.parent if plan_path.is_file() else Path.cwd()
    for item in outputs:
        value = item.get("path") if isinstance(item, dict) else item
        if not value:
            return False
        path = Path(str(value))
        if not path.is_absolute():
            path = base / path
        try:
            if not path.resolve().is_file() or path.stat().st_size <= 0:
                return False
        except OSError:
            return False
    return True


def _persist_cleanup_result(
    job: dict[str, Any], settings: dict[str, Any], status: str, error: str | None = None,
) -> dict[str, Any]:
    job.update(
        source_cleanup_status=status,
        source_cleanup_at=_now(),
        source_cleanup_error=error,
    )
    _write_job(job, settings)
    _log(
        _job_path(job["id"], settings).parent,
        f"Source cleanup: {status}" + (f" — {error}" if error else ""),
    )
    report_path = Path(str(job.get("report_path") or ""))
    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report.update({
                "source_cleanup_status": status,
                "source_cleanup_at": job["source_cleanup_at"],
                "source_cleanup_error": error,
            })
            _atomic_json(report_path, report)
        except (OSError, TypeError, ValueError):
            pass
    return job


def _cleanup_source_after_success(
    job: dict[str, Any], settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Delete only a verified local-input source after a successful terminal job."""
    settings = settings or load_settings()
    if job.get("source_cleanup_status"):
        return job
    if job.get("status") != "DONE" or job.get("formatter_status") != "DONE":
        return _persist_cleanup_result(job, settings, "SKIPPED_JOB_NOT_DONE")
    if not _verified_formatter_outputs(job):
        return _persist_cleanup_result(job, settings, "SKIPPED_JOB_NOT_DONE")
    source_value = job.get("source_path")
    if not source_value:
        return _persist_cleanup_result(job, settings, "SKIPPED_NOT_IN_INPUT")
    try:
        source = Path(str(source_value)).expanduser().resolve()
        input_root = Path(settings["input_folder"]).expanduser().resolve()
        source_text = os.path.normcase(os.path.normpath(str(source)))
        root_text = os.path.normcase(os.path.normpath(str(input_root)))
        if os.path.commonpath([source_text, root_text]) != root_text:
            return _persist_cleanup_result(job, settings, "SKIPPED_NOT_IN_INPUT")
    except (OSError, ValueError):
        return _persist_cleanup_result(job, settings, "SKIPPED_NOT_IN_INPUT")
    for other in _read_jobs_raw(settings):
        if other.get("id") == job.get("id") or other.get("status") in TERMINAL:
            continue
        other_source = other.get("source_path")
        if not other_source:
            continue
        try:
            if Path(str(other_source)).expanduser().resolve() == source:
                return _persist_cleanup_result(job, settings, "SKIPPED_IN_USE")
        except OSError:
            continue
    try:
        source.unlink()
    except OSError as exc:
        return _persist_cleanup_result(job, settings, "FAILED", str(exc))
    return _persist_cleanup_result(job, settings, "DELETED")


def _valid_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _local_scan_state_path() -> Path:
    return SETTINGS_PATH.parent / "local-folder-scan.json"


def _local_fingerprint(path: Path, size: int, modified_ns: int) -> str:
    import hashlib

    identity = f"{str(path.resolve()).casefold()}\0{size}\0{modified_ns}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _probe_local_media(path: Path) -> float | None:
    ffprobe = find_executable("ffprobe")
    if not ffprobe:
        return None
    completed = subprocess.run(
        [
            ffprobe, "-v", "error", "-show_entries", "format=duration,format_name",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=_utf8_environment(),
    )
    if completed.returncode:
        return None
    try:
        format_info = json.loads(completed.stdout)["format"]
        format_names = str(format_info.get("format_name") or "").split(",")
        if "mp4" not in format_names:
            return None
        duration = float(format_info["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return duration if duration > 0 else None


def _is_local_mp4_candidate(path: Path) -> bool:
    """Accept only final MP4 names, never yt-dlp fragments or temp files."""
    if path.suffix.lower() != ".mp4":
        return False
    name = path.name.casefold()
    if any(marker in name for marker in (".part.", ".ytdl.", ".tmp.", ".download.", ".fragment.")):
        return False
    return re.search(r"\.f\d+(?:-[a-z0-9]+)?\.mp4$", name) is None


def _local_job_status(job: dict[str, Any]) -> str:
    formatter_status = job.get("formatter_status")
    if formatter_status == "NEEDS_REVIEW":
        return "NEEDS_REVIEW"
    if formatter_status in {"PLANNING", "RENDERING"} or job.get("stage") == "formatting":
        return "FORMATTING"
    status = str(job.get("status") or "FAILED")
    return {
        "RENDERING": "FORMATTING",
        "CANCELLED": "FAILED",
        "INTERRUPTED": "FAILED",
    }.get(status, status)


def _create_local_job(
    source: Path, fingerprint: str, duration: float, settings: dict[str, Any],
) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    job_dir = _jobs_dir(settings) / job_id
    job_dir.mkdir(parents=True, exist_ok=False)
    (job_dir / "logs").mkdir()
    job = {
        "id": job_id,
        "input_mode": "LOCAL_FOLDER",
        "url": None,
        "title": source.stem,
        "display_name": source.stem,
        "duration": duration,
        "status": "READY",
        "progress": 100,
        "stage": "ready",
        "created_at": _now(),
        "queue_order": time.time_ns(),
        "started_at": None,
        "finished_at": None,
        "source_path": str(source.resolve()),
        "original_source_path": str(source.resolve()),
        "source_fingerprint": fingerprint,
        "output_path": None,
        "report_path": None,
        "formatter_status": None,
        "formatter_part_count": None,
        "formatted_outputs": [],
        "formatter_error": None,
        "keep_clean_master": bool(settings["keep_clean_master"]),
        "clean_master_required": None,
        "clean_master_rendered": False,
        "error": None,
        "cancel_requested": False,
        "pid": None,
        "download_retry_count": 0,
        "download_error_code": None,
        "download_retry_at": None,
        "download_cooldown_until": None,
        "download_time": 0.0,
        "downloaded_at": None,
        "analysis_time": None,
        "format_render_time": None,
        "total_job_time": None,
        "estimated_total_time_at_start": None,
        "final_estimation_error": None,
    }
    _write_job(job, settings)
    _log(job_dir, f"Local source queued: {source}")
    return job


def scan_local_folder(
    *, enqueue: bool = False, now: float | None = None,
    probe: Callable[[Path], float | None] | None = None,
) -> dict[str, Any]:
    settings = load_settings()
    folder = Path(settings["input_folder"])
    folder.mkdir(parents=True, exist_ok=True)
    current_time = time.time() if now is None else now
    probe = probe or _probe_local_media
    state_path = _local_scan_state_path()
    try:
        observations = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        observations = {}
    jobs_by_fingerprint = {
        str(job.get("source_fingerprint")): job
        for job in _read_jobs_raw(settings)
        if job.get("source_fingerprint")
    }
    files: list[dict[str, Any]] = []
    created_count = 0
    updated_observations: dict[str, Any] = {}
    for source in sorted(folder.iterdir(), key=lambda item: item.name.casefold()):
        if not source.is_file() or not _is_local_mp4_candidate(source):
            continue
        try:
            stat = source.stat()
        except OSError:
            continue
        key = str(source.resolve()).casefold()
        fingerprint = _local_fingerprint(source, stat.st_size, stat.st_mtime_ns)
        previous = observations.get(key) if isinstance(observations, dict) else None
        unchanged = bool(
            isinstance(previous, dict)
            and previous.get("size") == stat.st_size
            and previous.get("modified_ns") == stat.st_mtime_ns
        )
        stable_since = (
            float(previous["stable_since"])
            if unchanged and previous.get("stable_since") is not None
            else current_time
        )
        stable_checks = int(previous.get("stable_checks") or 0) + 1 if unchanged else 1
        stable = (
            unchanged and stable_checks >= 2
            and current_time - stable_since >= float(settings["local_file_stability_seconds"])
        )
        duration = previous.get("duration") if unchanged else None
        probe_ok = bool(previous.get("probe_ok")) if unchanged else False
        error = None
        job = jobs_by_fingerprint.get(fingerprint)
        if job:
            status = _local_job_status(job)
            duration = job.get("duration") or duration
        elif not stable:
            status = "STABILIZING"
        else:
            if not probe_ok:
                duration = probe(source)
                probe_ok = duration is not None
            status = "READY" if probe_ok else "SKIPPED"
            error = None if probe_ok else "ffprobe could not validate this media file"
            if enqueue and probe_ok:
                job = _create_local_job(source, fingerprint, float(duration), settings)
                jobs_by_fingerprint[fingerprint] = job
                created_count += 1
                status = "READY"
        updated_observations[key] = {
            "path": str(source.resolve()),
            "size": stat.st_size,
            "modified_ns": stat.st_mtime_ns,
            "stable_since": stable_since,
            "stable_checks": stable_checks,
            "probe_ok": probe_ok,
            "duration": duration,
            "last_seen": current_time,
        }
        files.append({
            "path": str(source.resolve()),
            "filename": source.name,
            "size": stat.st_size,
            "modified_time": stat.st_mtime,
            "fingerprint": fingerprint,
            "duration": duration,
            "status": status,
            "job_id": job.get("id") if job else None,
            "error": error,
        })
    _atomic_json(state_path, updated_observations)
    counts = {
        "total_files": len(files),
        "READY": sum(item["status"] == "READY" for item in files),
        "PROCESSING": sum(
            item["status"] in {"ANALYZING", "FORMATTING"} for item in files
        ),
        "DONE": sum(item["status"] == "DONE" for item in files),
        "NEEDS_REVIEW": sum(item["status"] == "NEEDS_REVIEW" for item in files),
        "FAILED": sum(item["status"] == "FAILED" for item in files),
    }
    return {
        "input_folder": str(folder.resolve()),
        "output_folder": settings["output_folder"],
        "watch_input_folder": bool(settings["watch_input_folder"]),
        "files": files,
        "counts": counts,
        "enqueued": created_count,
    }


def start_local_processing() -> dict[str, Any]:
    return scan_local_folder(enqueue=True)


def browse_folder(initial_path: str | None = None) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("folder browser is available on Windows only")
    initial = str(Path(initial_path or "D:/Vlog").expanduser()).replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$dialog=New-Object System.Windows.Forms.FolderBrowserDialog;"
        f"$dialog.SelectedPath='{initial}';"
        "if($dialog.ShowDialog() -eq 'OK'){[Console]::OutputEncoding="
        "[System.Text.Encoding]::UTF8;Write-Output $dialog.SelectedPath}"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=0x08000000,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or "folder browser failed")
    selected = completed.stdout.strip()
    return {"path": str(Path(selected).resolve()) if selected else None}


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
            "input_mode": "YOUTUBE",
            "url": url,
            "title": urlparse(url).netloc or url,
            "display_name": urlparse(url).netloc or url,
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
            "formatter_status": None,
            "formatter_part_count": None,
            "formatted_outputs": [],
            "formatter_error": None,
            "keep_clean_master": bool(settings["keep_clean_master"]),
            "clean_master_required": None,
            "clean_master_rendered": False,
            "error": None if _valid_url(url) else "URL must use http or https",
            "cancel_requested": False,
            "pid": None,
            "download_retry_count": 0,
            "download_error_code": None,
            "download_retry_at": None,
            "download_cooldown_until": None,
            "download_time": None,
            "analysis_time": None,
            "format_render_time": None,
            "total_job_time": None,
            "estimated_total_time_at_start": None,
            "final_estimation_error": None,
        }
        _write_job(job, settings)
        jobs.append(job)
    return jobs


def list_jobs() -> list[dict[str, Any]]:
    settings = load_settings()
    jobs = _read_jobs_raw(settings)
    for job in jobs:
        _reconcile_contentops_bridge_job(job, settings)
    history = _eta_history(jobs)
    jobs = [job | _eta_snapshot(job, history) for job in jobs]
    return sorted(
        jobs,
        key=lambda item: (
            item.get("created_at") or "",
            int(item.get("queue_order") or 0),
            item.get("id") or "",
        ),
    )


def _reconcile_contentops_bridge_job(
    job: dict[str, Any], settings: dict[str, Any]
) -> bool:
    """Reflect a completed bridge run in its corresponding LAN job record."""
    if job.get("origin") == "MANUAL_LAN":
        return False
    if job.get("status") not in ACTIVE or job.get("pid") or not job.get("source_path"):
        return False
    reports_root = Path(settings["workspace_folder"]) / "contentops-process-reports"
    if not reports_root.is_dir():
        return False
    try:
        source = Path(str(job["source_path"])).expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    for bridge_path in reports_root.glob("*/job.json"):
        try:
            bridge = json.loads(bridge_path.read_text(encoding="utf-8"))
            bridge_source = Path(str(bridge.get("source_path") or "")).expanduser().resolve()
        except (OSError, RuntimeError, TypeError, ValueError):
            continue
        if bridge.get("status") != "DONE" or bridge_source != source:
            continue
        outputs = bridge.get("formatted_outputs") or []
        output_values = [
            item.get("path") if isinstance(item, dict) else item for item in outputs
        ]
        if bridge.get("output_path"):
            output_values.append(bridge["output_path"])
        if not output_values or not any(Path(str(value)).is_file() for value in output_values if value):
            continue
        preserved = {key: job.get(key) for key in ("id", "created_at", "queue_order", "url")}
        job.update({key: value for key, value in bridge.items() if key not in preserved})
        job.update(
            preserved,
            status="DONE", stage="done", progress=100, pid=None,
            finished_at=bridge.get("finished_at") or _now(),
            external_process_id=bridge.get("id"),
            error=None, cancel_requested=False,
        )
        _write_job(job, settings)
        return True
    return False


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
    if job.get("formatter_status") == "FAILED" or job.get("stage") == "formatter_failed":
        if job.get("status") != "DONE":
            raise ValueError("formatter retry requires a completed analysis job")
        _format_done_job(_job_path(job_id), format_anyway=False)
        return _read_job(_job_path(job_id))
    if job["status"] not in TERMINAL:
        raise ValueError("only finished, failed, cancelled or interrupted jobs can retry")
    local_source = Path(str(job.get("source_path") or ""))
    source = (
        local_source if job.get("input_mode") == "LOCAL_FOLDER" and local_source.is_file()
        else _valid_existing_source(_job_path(job_id).parent)
    )
    job.update(
        status="READY" if source else "QUEUED",
        stage="ready" if source else "waiting_to_download", progress=100 if source else 0,
        source_path=str(source.resolve()) if source else None, started_at=None,
        finished_at=None, error=None, cancel_requested=False, pid=None,
        download_retry_count=0, download_error_code=None, download_retry_at=None,
        download_time=None, analysis_time=None, format_render_time=None,
        total_job_time=None, estimated_total_time_at_start=None,
        final_estimation_error=None, overall_progress=0.0,
        download_started_at=None, analysis_started_at=None,
        source_cleanup_status=None, source_cleanup_at=None, source_cleanup_error=None,
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


def clear_history() -> dict[str, int]:
    """Remove only terminal job folders; active and ready jobs are preserved."""
    removed = 0
    for job in _read_jobs_raw():
        if job.get("status") not in TERMINAL:
            continue
        path = _job_path(str(job["id"]))
        if path.parent.is_dir():
            shutil.rmtree(path.parent)
            removed += 1
    return {"removed": removed}


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
    # Prefer an application-bundled binary so the desktop/worker does not
    # depend on a separately configured PATH or Python package.
    bundled = ROOT / "tools" / ("yt-dlp.exe" if os.name == "nt" else "yt-dlp")
    if bundled.is_file():
        return [str(bundled)]
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


def youtube_profile_dir() -> Path:
    return Path(load_settings()["youtube_profile_path"])


def youtube_profile_ready() -> bool:
    profile = youtube_profile_dir()
    return profile.is_dir() and any(path.is_file() for path in profile.rglob("*"))


def _persist_youtube_settings(**values: Any) -> dict[str, Any]:
    settings = load_settings()
    settings.update(values)
    _atomic_json(SETTINGS_PATH, settings)
    return settings


def youtube_login_status() -> dict[str, Any]:
    settings = load_settings()
    jobs = list_jobs()
    ready = youtube_profile_ready()
    if not ready:
        status = "LOGIN REQUIRED"
    elif any(job.get("stage") == "profile_locked" for job in jobs):
        status = "PROFILE LOCKED"
    elif any(job.get("stage") in {"auth_required", "auth_failed"} for job in jobs):
        status = "LOGIN REQUIRED"
    elif any(job.get("stage") == "profile_error" for job in jobs):
        status = "PROFILE ERROR"
    elif ready and settings.get("last_session_status") in {
        "PROFILE READY", "LOGIN REQUIRED", "PROFILE ERROR", "PROFILE LOCKED",
    }:
        status = settings["last_session_status"]
    else:
        status = "PROFILE READY"
    return {
        "status": status,
        "profile_ready": ready,
        "youtube_profile_path": settings["youtube_profile_path"],
        "last_session_test": settings.get("last_session_test"),
        "last_session_status": settings.get("last_session_status"),
    }


def _chrome_executable() -> Path | None:
    for name in ("chrome", "chrome.exe", "chromium", "chromium.exe"):
        found = shutil.which(name)
        if found:
            return Path(found)
    roots = [
        os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("LOCALAPPDATA"),
    ]
    relative = Path("Google") / "Chrome" / "Application" / "chrome.exe"
    for root in filter(None, roots):
        candidate = Path(root) / relative
        if candidate.is_file():
            return candidate
    return None


def open_youtube_login() -> dict[str, Any]:
    chrome = _chrome_executable()
    if chrome is None:
        raise RuntimeError("Chrome or Chromium was not found")
    profile = youtube_profile_dir()
    profile.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [str(chrome), f"--user-data-dir={profile}", "https://www.youtube.com/"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )
    _persist_youtube_settings(last_session_test=None, last_session_status=None)
    return {**youtube_login_status(), "browser_opened": True}


def test_youtube_access(url: str | None = None) -> dict[str, Any]:
    yt = _yt_dlp_command()
    if not youtube_profile_ready():
        return {**youtube_login_status(), "status": "LOGIN REQUIRED", "accessible": False}
    target = (url or "https://www.youtube.com/watch?v=jNQXAC9IVRw").strip()
    if not _valid_url(target):
        raise ValueError("session test URL must use http or https")
    if not yt:
        status, detail = "YT-DLP ERROR", "yt-dlp was not found"
        _persist_youtube_settings(last_session_test=_now(), last_session_status=status)
        return {**youtube_login_status(), "status": status, "accessible": False, "error": detail}
    try:
        completed = subprocess.run(
            [
                *yt, "--ignore-config", "--cookies-from-browser",
                f"chrome:{youtube_profile_dir()}", "--simulate", "--no-playlist",
                "--quiet", target,
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", timeout=90,
            env=_utf8_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        status, detail = "YT-DLP ERROR", str(exc)
        _persist_youtube_settings(last_session_test=_now(), last_session_status=status)
        return {**youtube_login_status(), "status": status, "accessible": False, "error": detail}
    if completed.returncode == 0:
        status = "PROFILE READY"
        _persist_youtube_settings(last_session_test=_now(), last_session_status=status)
        return {**youtube_login_status(), "status": status, "accessible": True}
    code = classify_download_error(completed.stderr)
    status = (
        "PROFILE LOCKED" if code == "BROWSER_PROFILE_LOCKED"
        else "LOGIN REQUIRED" if code in AUTH_FAILURE_CODES
        else "PROFILE ERROR"
    )
    detail = _download_error_text(code)
    _persist_youtube_settings(last_session_test=_now(), last_session_status=status)
    return {
        **youtube_login_status(), "status": status, "accessible": False,
        "error": detail,
    }


def reset_youtube_profile(*, confirmed: bool = False) -> dict[str, Any]:
    if not confirmed:
        raise ValueError("explicit confirmation is required to reset the YouTube profile")
    profile = youtube_profile_dir().resolve()
    allowed = (SETTINGS_PATH.parent / "youtube_profile").resolve()
    if profile != allowed:
        raise RuntimeError("refusing to remove an unexpected browser profile path")
    if profile.exists():
        discarded = profile.with_name(f".youtube_profile-reset-{uuid.uuid4().hex}")
        try:
            os.replace(profile, discarded)
        except OSError as exc:
            raise RuntimeError("Close the YouTube login browser and retry.") from exc
        shutil.rmtree(discarded)
    _persist_youtube_settings(last_session_test=None, last_session_status=None)
    return youtube_login_status()


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
    log_dir = job_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "job.log").open("a", encoding="utf-8") as stream:
        stream.write(f"{stamp} {message}\n")


def _command_log(job_dir: Path, command: list[str]) -> None:
    redacted = []
    hide_next = False
    for argument in command:
        if hide_next:
            redacted.append("<redacted>")
            hide_next = False
        elif argument.lower() == "--cookies-from-browser":
            redacted.append("<browser-session>")
            hide_next = True
        else:
            redacted.append(argument)
            hide_next = argument.lower() in {
                "--add-header", "--cookies", "--password", "--username",
            }
    with (job_dir / "logs" / "commands.log").open("a", encoding="utf-8") as stream:
        stream.write(subprocess.list2cmdline(redacted) + "\n")


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
    authentication = [
        "--cookies-from-browser", f"chrome:{youtube_profile_dir()}"
    ]
    metadata_command = [
        *yt, "--ignore-config", *authentication, "--dump-single-json", "--skip-download",
        "--no-playlist", job["url"],
    ]
    metadata_lines: list[str] = []
    _run_process(metadata_command, job, job_dir, on_line=metadata_lines.append)
    info = json.loads("\n".join(metadata_lines))
    job["title"] = str(info.get("title") or job["title"])
    job["display_name"] = job["title"]
    job["duration"] = float(info["duration"]) if info.get("duration") else None
    _write_job(job)
    output_template = str(job_dir / "source.%(ext)s")
    download_command = [
        *yt, "--ignore-config", *authentication, "--newline", "--no-playlist", "--progress",
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
    if any(item in value for item in (
        "could not copy chrome cookie database",
        "could not copy chromium cookie database",
        "failed to copy the cookie database",
        "cookie database is locked",
        "database is locked",
    )):
        return "BROWSER_PROFILE_LOCKED"
    if re.search(r"(?:http error\s*)?429|too many requests", value):
        return "HTTP_429"
    if any(item in value for item in (
        "private video", "video unavailable", "has been removed", "deleted video",
        "not available", "geo-restricted",
    )):
        return "UNAVAILABLE"
    if re.search(r"(?:http error\s*)?403|forbidden", value):
        return "HTTP_403"
    if any(item in value for item in (
        "not a bot", "bot challenge", "po token", "token challenge",
    )):
        return "BOT_CHALLENGE_OR_TOKEN"
    if any(item in value for item in (
        "sign in", "login required", "login_required", "authentication required",
        "members-only", "confirm your age",
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
        "BROWSER_PROFILE_LOCKED": "Close the YouTube login browser and retry.",
        "NETWORK_TRANSIENT": "Temporary network failure while downloading",
        "HTTP_429": "YouTube rate limited this machine (HTTP 429)",
        "HTTP_403": "YouTube refused the profile download (HTTP 403)",
        "AUTH_REQUIRED": "YouTube login is required",
        "BOT_CHALLENGE_OR_TOKEN": "YouTube requires a logged-in browser session",
        "UNAVAILABLE": "Video is unavailable",
        "INVALID_URL": "URL is invalid or unsupported by yt-dlp",
        "UNKNOWN": "yt-dlp failed for an unknown reason",
    }[code]


def _sanitize_title(title: str, max_utf16_units: int = 110) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).rstrip(" .")
    shortened: list[str] = []
    utf16_units = 0
    for character in value:
        character_units = len(character.encode("utf-16-le")) // 2
        if utf16_units + character_units > max_utf16_units:
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


def _user_output_folder(folder: Path, title: str, job_id: str) -> Path:
    destination = folder / f"{_sanitize_title(title)}_{job_id[:8]}"
    destination.mkdir(parents=True, exist_ok=True)
    return destination


def _pipeline(job: dict[str, Any], job_dir: Path, source: Path) -> tuple[Path | None, Path]:
    rendered = job_dir / "rendered.mp4"
    report = job_dir / "pipeline_report.json"
    rendered.unlink(missing_ok=True)
    command = [
        sys.executable, "-m", "production", str(source), "-o", str(rendered),
        "--report", str(report), "--debug", "--analysis-only",
    ]
    selection = job_dir / "long_video_selection.json"
    if selection.is_file():
        try:
            if json.loads(selection.read_text(encoding="utf-8")).get("status") == "APPLIED":
                command.extend(["--allowed-ranges-json", str(selection)])
        except (OSError, json.JSONDecodeError):
            pass
    _run_process(command, job, job_dir)
    if not report.is_file():
        raise RuntimeError("production analysis completed without report")
    return None, report


def _run_long_video_stage(job: dict[str, Any], job_dir: Path, source: Path) -> dict[str, Any]:
    from long_video_selector.selector import LongVideoSelectorConfig
    from silence_cutter.audio import probe_media

    artifact = job_dir / "long_video_selection.json"
    config = LongVideoSelectorConfig.from_environment()
    try:
        duration = float(probe_media(source)["duration"])
        if duration <= config.threshold:
            result = {"status": "NOT_APPLICABLE", "source_duration": duration,
                      "threshold": config.threshold, "selected_ranges": []}
            _atomic_json(artifact, result)
            return result
        command = [
            sys.executable, "-m", "long_video_selector", str(source),
            "--output", str(artifact),
        ]
        timeout = float(os.environ.get("LONG_VIDEO_SELECTOR_TIMEOUT", "90"))
        completed = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=_utf8_environment(), timeout=timeout,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        if completed.returncode:
            detail = (completed.stderr or completed.stdout or "selector subprocess failed").strip()
            raise RuntimeError(detail[-2000:])
        result = json.loads(artifact.read_text(encoding="utf-8"))
    except subprocess.TimeoutExpired:
        result = {"status": "LONG_VIDEO_SELECTOR_SKIPPED", "threshold": config.threshold,
                  "selected_ranges": [], "reason": "selector timed out"}
    except Exception as exc:
        result = {"status": "LONG_VIDEO_SELECTOR_SKIPPED", "threshold": config.threshold,
                  "selected_ranges": [], "reason": f"{type(exc).__name__}: {exc}"}
    _atomic_json(artifact, result)
    _log(job_dir, f"Long video selector: {result['status']}")
    return result


def _run_semantic_stage(job: dict[str, Any], job_dir: Path, source: Path, report: Path) -> dict[str, Any]:
    from semantic_cleaner.cleaner import write_skipped_artifact

    artifact = job_dir / "semantic_segments.json"
    command = [
        sys.executable, "-m", "semantic_cleaner", str(source), str(report),
        "--output", str(artifact),
    ]
    timeout = float(os.environ.get("SEMANTIC_CLEANER_TIMEOUT", "900"))
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=_utf8_environment(), timeout=timeout,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        if completed.returncode:
            detail = (completed.stderr or completed.stdout or "semantic subprocess failed").strip()
            result = write_skipped_artifact(artifact, reason=detail[-2000:])
        else:
            result = json.loads(artifact.read_text(encoding="utf-8"))
    except subprocess.TimeoutExpired:
        result = write_skipped_artifact(
            artifact, reason=f"semantic cleaner timed out after {timeout:g} seconds",
        )
    except Exception as exc:
        result = write_skipped_artifact(
            artifact, reason=f"{type(exc).__name__}: {exc}",
        )
    result.setdefault("total_additional_processing_time", time.perf_counter() - started)
    _atomic_json(artifact, result)
    _log(job_dir, f"Semantic cleaner: {result['status']}")
    return result


def _run_brand_scan_stage(job: dict[str, Any], job_dir: Path, source: Path, report: Path) -> dict[str, Any]:
    """Run the recall-first visual brand/ad scan before clean-master rendering."""
    from brand_scan.models import BrandScanResult
    from brand_scan.detector import BrandScanDetector
    from brand_scan.pipeline import run_brand_scan
    from brand_scan.qr import detect_qr

    artifact = job_dir / "brand_ad_scan.json"
    try:
        from semantic_cleaner.qwen import QwenWorkerDetector

        detector = QwenWorkerDetector()
        result = run_brand_scan(source, report, artifact, BrandScanDetector(detector, detect_qr))
    except Exception as exc:
        result = BrandScanResult(
            "BRAND_SCAN_INCOMPLETE", [], [], reason=f"{type(exc).__name__}: {exc}",
        ).to_dict()
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report_data = json.loads(report.read_text(encoding="utf-8"))
        report_data.update(
            brand_scan_status=result["status"], brand_scan_artifact=str(artifact),
            brand_scan_reason=result["reason"], brand_cut_intervals=[],
            brand_removed_duration=0.0, brand_scan_time=0.0,
        )
        report.write_text(json.dumps(report_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _atomic_json(artifact, result)
    _log(job_dir, f"Brand/ad scan: {result['status']}")
    return result


def _render_clean_master_from_report(
    source: Path, rendered: Path, report_path: Path,
) -> Path:
    from silence_cutter.audio import probe_media
    from silence_cutter.renderer import render_video

    report = json.loads(report_path.read_text(encoding="utf-8"))
    keep = report.get("keep_intervals") or (report.get("debug") or {}).get("keep_intervals") or []
    if report.get("no_speech_detected") and not keep:
        keep = [{"start": 0.0, "end": float(report["input_duration"])}]
    if not keep:
        raise RuntimeError("analysis report contains no KEEP timeline")
    diagnostics: dict[str, Any] = {}
    started = time.perf_counter()
    render_video(source, rendered, keep, diagnostics=diagnostics)
    actual_duration = float(probe_media(rendered)["duration"])
    expected_duration = sum(float(item["end"]) - float(item["start"]) for item in keep)
    report.update(
        output_duration=actual_duration, actual_output_duration=actual_duration,
        expected_output_duration=expected_duration,
        duration_error=abs(actual_duration - expected_duration),
        render_time=time.perf_counter() - started, analysis_only=False,
        intermediate_render_skipped=False,
    )
    report.setdefault("debug", {})["render"] = diagnostics
    _atomic_json(report_path, report)
    return rendered


def _format_done_job(job_file: Path, *, format_anyway: bool = False) -> dict[str, Any]:
    job = _read_job(job_file)
    job.update(
        formatter_status="PLANNING", formatter_error=None,
        stage="formatting", finished_at=None,
    )
    _write_job(job)
    try:
        from formatter.planner import plan_done_job
        from formatter.renderer import render_format_plan

        plan_path = job_file.parent / "format_plan.json"
        preview_path = job_file.parent / "part1_preview.png"
        plan = plan_done_job(
            job_file, output_path=plan_path, preview_path=preview_path,
            format_anyway=format_anyway,
        )
        if plan["formatter_status"] == "PLANNED":
            plan = _apply_qwen_part_policy(plan, job_file.parent)
            plan_path.write_text(
                json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
            )
            plan = render_format_plan(plan_path)
            job = _read_job(job_file)
            job.update(
                formatter_status=plan["formatter_status"],
                formatter_error=plan.get("formatter_error"),
            )
            _finalize_job(
                job, "done" if plan["formatter_status"] == "DONE" else "formatter_failed"
            )
        else:
            job = _read_job(job_file)
            job.update(
                formatter_status=plan["formatter_status"],
                formatter_part_count=plan.get("part_count"),
                format_plan=str(plan_path), formatted_outputs=[],
                formatter_error=None,
            )
            _finalize_job(job, "needs_review")
        return plan
    except Exception as exc:
        job = _read_job(job_file)
        job.update(formatter_status="FAILED", formatter_error=str(exc))
        _finalize_job(job, "formatter_failed")
        _log(job_file.parent, f"Formatter failed: {exc}")
        return {
            "formatter_status": "FAILED", "formatted_outputs": [],
            "formatter_error": str(exc),
        }


def _apply_qwen_part_policy(plan: dict[str, Any], job_dir: Path) -> dict[str, Any]:
    """Inspect bounded formatter parts before FFmpeg and rebuild their mapping."""
    if not plan.get("direct_source_render"):
        plan["qwen_part_inspection"] = {
            "status": "QWEN_SKIPPED_NON_DIRECT_RENDER",
            "reason": "formatter source is already rendered",
        }
        return plan
    source = Path(str(plan.get("source_video_path") or ""))
    duration = float(plan.get("input_duration") or 0.0)
    from formatter.renderer import map_clean_range_to_source
    from enhanced_content_flow.flow import inspect_qwen_parts
    from qwen_part_policy import cap_source_segments, subtract_source_ranges

    parts = []
    for part in plan.get("parts") or []:
        source_ranges = map_clean_range_to_source(
            float(part["clean_start"]), float(part["clean_end"]),
            plan.get("render_segments") or [],
        )
        parts.append(part | {"source_ranges": source_ranges})
    inspection = inspect_qwen_parts(source, duration, parts, job_dir)
    plan["qwen_part_inspection"] = inspection
    if inspection.get("status") != "QWEN_PARTS_INSPECTED":
        return plan

    by_index = {int(item["part_index"]): item for item in inspection.get("parts") or []}
    cursor = 0.0
    mapping: list[dict[str, float]] = []
    rebuilt: list[dict[str, Any]] = []
    for part in parts:
        index = int(part["index"])
        source_ranges = list(part["source_ranges"])
        inspected = by_index.get(index) or {}
        removals = [
            {"start": float(item["start"]), "end": float(item["end"])}
            for item in inspected.get("segments") or []
            if float(item.get("end", 0)) > float(item.get("start", 0))
        ]
        filtered = subtract_source_ranges(source_ranges, removals)
        original_duration = sum(item["end"] - item["start"] for item in source_ranges)
        if original_duration > 600.0:
            filtered = cap_source_segments(filtered)
        if not filtered:
            filtered = cap_source_segments(source_ranges) if original_duration > 600.0 else source_ranges
        part_duration = sum(item["end"] - item["start"] for item in filtered)
        if part_duration <= 0:
            continue
        for segment in filtered:
            length = segment["end"] - segment["start"]
            mapping.append({
                "output_start": cursor, "output_end": cursor + length,
                "source_start": segment["start"], "source_end": segment["end"],
            })
            cursor += length
        rebuilt.append(part | {
            "clean_start": cursor - part_duration, "clean_end": cursor,
            "duration": part_duration,
        })
    if len(rebuilt) == len(parts):
        plan["parts"] = rebuilt
        plan["render_segments"] = mapping
        plan["clean_video_duration"] = cursor
    return plan


def _process_ready_job(
    job: dict[str, Any],
    *, pipeline: Callable[[dict[str, Any], Path, Path], tuple[Path | None, Path]] = _pipeline,
) -> dict[str, Any]:
    settings = load_settings()
    job_dir = _job_path(job["id"], settings).parent
    source = Path(str(job.get("source_path") or ""))
    try:
        if not source.is_file():
            raise RuntimeError("downloaded source is missing")
        analysis_started_at = _now()
        analysis_wall_started = time.perf_counter()
        job.update(
            status="ANALYZING", stage="analyzing", progress=None, error=None,
            started_at=job.get("started_at") or analysis_started_at,
            analysis_started_at=analysis_started_at,
        )
        _write_job(job, settings)
        _log(job_dir, "Production pipeline started")
        long_selection = {
            "status": "DEFERRED_TO_FORMATTER_QWEN",
            "threshold": 1500.0,
            "selected_ranges": [],
            "reason": "part timestamps are finalized by formatter before bounded Qwen inspection",
        }
        rendered, report = pipeline(job, job_dir, source)
        from semantic_cleaner.cleaner import write_skipped_artifact
        semantic = write_skipped_artifact(
            job_dir / "semantic_segments.json",
            reason="deferred_to_bounded_qwen_part_inspection_before_formatter",
        )
        brand_scan = {
            "status": "DEFERRED_TO_QWEN_PART_INSPECTION",
            "removed_duration": 0.0,
        }
        _atomic_json(job_dir / "brand_ad_scan.json", brand_scan)
        analysis_time = time.perf_counter() - analysis_wall_started
        report_data = json.loads(report.read_text(encoding="utf-8"))
        clean_duration = report_data.get("expected_output_duration")
        outputs = Path(settings["output_folder"])
        outputs.mkdir(parents=True, exist_ok=True)
        output_folder = _user_output_folder(
            outputs, str(job.get("display_name") or job["title"]), job["id"]
        )
        from formatter.title_rewrite import rewrite_title_once
        rewrite = rewrite_title_once(
            job_dir, str(job["title"]), output_folder,
            source_id=str(job.get("video_id") or job["id"]),
            part_count=3,
            allow_qwen=False,
        )
        report_data.update(
            original_title=str(job["title"]),
            rewritten_title=rewrite["rewritten_title"],
            title_rewrite_status=rewrite["status"],
            title_rewrite_seconds=rewrite["total_seconds"],
            title_rewrite_queue_wait=rewrite["queue_wait_seconds"],
            title_rewrite_generation=rewrite["generation_seconds"],
            title_rewrite_total=rewrite["total_seconds"],
            title_rewrite_model_loads=rewrite["model_load_count"],
        )
        _atomic_json(report, report_data)
        clean_master_required = bool(rendered) or bool(job.get("keep_clean_master"))
        destination: Path | None = None
        if clean_master_required:
            if rendered is None:
                clean_master_started = time.perf_counter()
                job.update(
                    status="RENDERING", stage="rendering_clean_master",
                    clean_master_render_started_at=_now(),
                )
                _write_job(job, settings)
                rendered = _render_clean_master_from_report(
                    source, job_dir / "rendered.mp4", report
                )
                job["clean_master_render_time"] = time.perf_counter() - clean_master_started
            destination = output_folder / "clean_master.mp4"
            shutil.copy2(rendered, destination)
        job.update(
            status="DONE", stage="formatting", progress=100, finished_at=None,
            display_name=str(job.get("display_name") or job["title"]),
            output_folder=str(output_folder.resolve()),
            output_path=str(destination.resolve()) if destination else None,
            report_path=str(report.resolve()), pid=None,
            clean_master_required=clean_master_required,
            clean_master_rendered=bool(destination),
            intermediate_render_skipped=not clean_master_required,
            analysis_time=analysis_time,
            pipeline_analysis_time=report_data.get("analysis_time"),
            semantic_cleaner_status=semantic.get("status"),
            semantic_segments_path=str((job_dir / "semantic_segments.json").resolve()),
            semantic_processing_time=semantic.get("total_additional_processing_time"),
            brand_scan_status=brand_scan.get("status"),
            brand_scan_artifact=str((job_dir / "brand_ad_scan.json").resolve()),
            brand_removed_duration=brand_scan.get("removed_duration", 0.0),
            long_video_selector_status=long_selection.get("status"),
            long_video_selection_path=str((job_dir / "long_video_selection.json").resolve()),
            long_video_selected_ranges=long_selection.get("selected_ranges") or [],
            long_video_selector_time=long_selection.get("total_processing_time"),
            clean_video_duration=clean_duration,
            original_title=str(job["title"]),
            rewritten_title=rewrite["rewritten_title"],
            filename_base=rewrite["filename_base"],
            title_rewrite_status=rewrite["status"],
            title_rewrite_seconds=rewrite["total_seconds"],
            title_rewrite_queue_wait=rewrite["queue_wait_seconds"],
            title_rewrite_generation=rewrite["generation_seconds"],
        )
        _write_job(job, settings)
        _log(job_dir, f"Analysis done; clean master {'rendered' if destination else 'skipped'}")
        _log(job_dir, "TikTok formatter planning started")
        _format_done_job(_job_path(job["id"], settings))
        job = _read_job(_job_path(job["id"], settings))
        _cleanup_source_after_success(job, settings)
        job = _read_job(_job_path(job["id"], settings))
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


def _pid_is_alive(pid: int | None) -> bool:
    if not pid or pid == os.getpid():
        return pid == os.getpid()
    try:
        os.kill(int(pid), 0)
    except PermissionError:
        # The process exists but this user cannot probe it.
        return True
    except (OSError, ProcessLookupError, ValueError):
        return False
    return True


def reconcile_orphaned_jobs(
    *, now: float | None = None, timeout_seconds: float | None = None,
) -> int:
    """Fail active jobs whose child process disappeared and stopped updating.

    A short grace period avoids racing the normal hand-off between download,
    analysis, and rendering.  This is deliberately fail-safe: it records a
    retryable failure instead of silently leaving the UI stuck forever.
    """
    now = time.time() if now is None else float(now)
    timeout = float(
        timeout_seconds
        if timeout_seconds is not None
        else os.environ.get("SILENCE_ORPHAN_JOB_TIMEOUT", "30")
    )
    recovered = 0
    for job in _read_jobs_raw():
        if job.get("status") not in ACTIVE:
            continue
        pid = job.get("pid")
        if pid and _pid_is_alive(int(pid)):
            continue
        try:
            path = _job_path(str(job["id"]))
            age = max(0.0, now - path.stat().st_mtime)
        except (OSError, KeyError, ValueError, TypeError):
            continue
        if age < timeout:
            continue
        job.update(
            status="FAILED", stage="worker_crashed", progress=None,
            finished_at=_now(), pid=None,
            error="Worker stopped unexpectedly; processing worker will restart automatically.",
            cancel_requested=False,
        )
        _write_job(job)
        _log(path.parent, "Worker watchdog: orphaned job marked FAILED")
        recovered += 1
    return recovered


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
        download_started_at=job.get("download_started_at") or job.get("started_at") or _now(),
        error=None, cancel_requested=False, download_retry_at=None,
    )
    _write_job(job, settings)
    attempt = int(job.get("download_retry_count") or 0) + 1
    process_log = job_dir / "logs" / "process.log"
    log_start = process_log.stat().st_size if process_log.is_file() else 0
    if not youtube_profile_ready():
        job.update(
            status="FAILED", stage="auth_required", progress=None, pid=None,
            finished_at=_now(), download_error_code="AUTH_REQUIRED",
            download_retry_at=None,
            error=(
                "YouTube login required. Open Profile, sign in, then retry this job."
            ),
        )
        _write_job(job, settings)
        _log(job_dir, "YouTube profile is not configured")
        return job
    try:
        _log(job_dir, f"Download attempt {attempt} started (YouTube profile)")
        source = downloader(job, job_dir)
        current = _read_job(path)
        if current.get("cancel_requested") or current["status"] == "CANCELLED":
            return current
        cooldown_seconds = cooldown(
            config.download_cooldown_min_seconds,
            config.download_cooldown_max_seconds,
        )
        downloaded_at = _now()
        job.update(
            source_path=str(source.resolve()), status="READY", stage="ready", progress=100,
            pid=None, error=None, download_error_code=None, download_retry_at=None,
            downloaded_at=downloaded_at,
            download_time=_seconds_between(job.get("download_started_at"), downloaded_at),
            download_cooldown_until=clock() + cooldown_seconds,
        )
        _write_job(job, settings)
        _log(job_dir, f"Download complete; downloader cooldown {cooldown_seconds:.0f}s")
    except Exception as exc:
        current = _read_job(path)
        if current.get("cancel_requested") or current["status"] == "CANCELLED":
            return current
        detail = f"{exc}\n{_process_log_tail(job_dir, log_start)}"
        code = classify_download_error(detail)
        if code == "BROWSER_PROFILE_LOCKED":
            job.update(
                status="FAILED", stage="profile_locked",
                progress=None, pid=None,
                finished_at=_now(), download_error_code=code,
                download_retry_at=None,
                error=_download_error_text(code),
            )
            _write_job(job, settings)
            _log(job_dir, "YouTube profile is locked")
            return job
        if code in {"AUTH_REQUIRED", "BOT_CHALLENGE_OR_TOKEN"}:
            job.update(
                status="FAILED", stage="auth_required", progress=None, pid=None,
                finished_at=_now(), download_error_code=code,
                download_retry_at=None,
                error="YouTube login required. Open Profile, sign in, then retry.",
            )
            _write_job(job, settings)
            _log(job_dir, f"YouTube login required [{code}]")
            return job
        if code == "HTTP_403":
            job.update(
                status="FAILED", stage="profile_error", progress=None, pid=None,
                finished_at=_now(), download_error_code=code,
                download_retry_at=None, error=_download_error_text(code),
            )
            _write_job(job, settings)
            _log(job_dir, "YouTube profile download refused [HTTP_403]")
            return job
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
        pipeline: Callable[[dict[str, Any], Path, Path], tuple[Path | None, Path]] = _pipeline,
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
        self.next_watch_scan = 0.0

    @staticmethod
    def _reap(future: Future[dict[str, Any]] | None) -> Future[dict[str, Any]] | None:
        if future is not None and future.done():
            # A worker task must never take down the supervisor.  The job
            # watchdog below records orphaned active jobs and the outer
            # PowerShell supervisor can restart this process if needed.
            try:
                future.result()
            except Exception:
                pass
            return None
        return future

    def tick(self) -> None:
        reconcile_orphaned_jobs()
        self.download_future = self._reap(self.download_future)
        self.process_future = self._reap(self.process_future)
        settings = load_settings()
        if settings["watch_input_folder"] and self.clock() >= self.next_watch_scan:
            scan_local_folder(enqueue=True, now=self.clock())
            self.next_watch_scan = self.clock() + 1.0
        jobs = list_jobs()
        if self.process_future is None:
            ready = next((job for job in jobs if job["status"] == "READY" and job.get("origin") != "MANUAL_LAN"), None)
            if ready:
                self.process_future = self.process_pool.submit(
                    _process_ready_job, ready, pipeline=self.pipeline
                )
        if self.download_future is not None:
            return
        retrying = next((
            job for job in jobs
            if job["status"] == "DOWNLOADING" and job.get("stage") == "retry_wait" and job.get("origin") != "MANUAL_LAN"
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
        queued = next((job for job in jobs if job["status"] == "QUEUED" and job.get("origin") != "MANUAL_LAN"), None)
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
    pipeline: Callable[[dict[str, Any], Path, Path], tuple[Path | None, Path]] = _pipeline,
) -> dict[str, Any]:
    settings = load_settings()
    job_dir = _job_path(job["id"], settings).parent
    try:
        started_at = job.get("started_at") or _now()
        job.update(
            status="DOWNLOADING", stage="downloading", progress=0,
            started_at=started_at, download_started_at=started_at, finished_at=None,
            error=None, cancel_requested=False,
        )
        _write_job(job, settings)
        _log(job_dir, "Download started")
        source = downloader(job, job_dir)
        downloaded_at = _now()
        job.update(
            source_path=str(source.resolve()), status="READY", stage="ready", progress=100,
            downloaded_at=downloaded_at,
            download_time=_seconds_between(started_at, downloaded_at),
        )
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
    pipeline: Callable[[dict[str, Any], Path, Path], tuple[Path | None, Path]] = _pipeline,
) -> dict[str, Any] | None:
    queued = next(
        (
            job for job in list_jobs()
            if job["status"] == "QUEUED" and job.get("origin") != "MANUAL_LAN"
        ),
        None,
    )
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
    if operation == "scan_local_folder":
        return scan_local_folder()
    if operation == "start_local_processing":
        return start_local_processing()
    if operation == "browse_folder":
        return browse_folder(payload.get("initial_path"))
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
    if operation == "youtube_login_status":
        return youtube_login_status()
    if operation == "open_youtube_login":
        return open_youtube_login()
    if operation == "test_youtube_access":
        return test_youtube_access(payload.get("url"))
    if operation == "reset_youtube_profile":
        return reset_youtube_profile(confirmed=payload.get("confirmed") is True)
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
        plan_path = job_file.parent / "format_plan.json"
        plan = _format_done_job(
            job_file, format_anyway=bool(payload.get("format_anyway", False))
        )
        return {
            "formatter_status": plan["formatter_status"],
            "format_plan": str(plan_path),
            "preview": plan.get("preview_path"),
            "parts": plan.get("parts", []),
            "part_count": plan.get("part_count"),
            "formatted_outputs": plan.get("formatted_outputs", []),
            "formatter_error": plan.get("formatter_error"),
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
