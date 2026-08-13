from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .runtime_paths import find_executable


class MediaProcessError(RuntimeError):
    pass


def _require_executable(name: str) -> str:
    executable = find_executable(name)
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


def extract_analysis_audio_range(
    input_path: Path, wav_path: Path, sample_rate: int, start: float, end: float,
) -> Path:
    if not 0 <= start < end:
        raise ValueError("audio range must have positive duration")
    ffmpeg = _require_executable("ffmpeg")
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    _run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-ss", f"{start:.9f}", "-i", str(input_path), "-t", f"{end - start:.9f}",
        "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(sample_rate),
        "-c:a", "pcm_s16le", str(wav_path),
    ], "scoped analysis audio extraction")
    return wav_path


def probe_media(input_path: Path) -> dict[str, float | bool]:
    ffprobe = _require_executable("ffprobe")
    completed = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type",
            "-of",
            "json",
            str(input_path),
        ],
        "media probe",
    )
    try:
        probe = json.loads(completed.stdout)
        duration = float(probe["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MediaProcessError("media probe returned no valid duration") from exc
    if duration <= 0:
        raise MediaProcessError("input media duration must be positive")
    return {
        "duration": duration,
        "has_audio": any(
            stream.get("codec_type") == "audio" for stream in probe.get("streams", [])
        ),
    }
