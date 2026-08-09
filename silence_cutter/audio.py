from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


class MediaProcessError(RuntimeError):
    pass


def _require_executable(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise MediaProcessError(f"{name} was not found on PATH")
    return executable


def _run(command: list[str], operation: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "unknown error").strip()
        raise MediaProcessError(f"{operation} failed: {detail}") from exc


def extract_analysis_audio(input_path: Path, wav_path: Path, sample_rate: int) -> Path:
    ffmpeg = _require_executable("ffmpeg")
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(input_path),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            str(wav_path),
        ],
        "analysis audio extraction",
    )
    return wav_path


def probe_media(input_path: Path) -> dict[str, float]:
    ffprobe = _require_executable("ffprobe")
    completed = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(input_path),
        ],
        "media probe",
    )
    try:
        duration = float(json.loads(completed.stdout)["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MediaProcessError("media probe returned no valid duration") from exc
    if duration <= 0:
        raise MediaProcessError("input media duration must be positive")
    return {"duration": duration}
