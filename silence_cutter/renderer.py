from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .audio import MediaProcessError, _require_executable, _run


def _filter_graph(keep: list[dict[str, float]]) -> str:
    if not keep:
        raise ValueError("keep timeline must not be empty")
    filters: list[str] = []
    inputs: list[str] = []
    for index, segment in enumerate(keep):
        start, end = segment["start"], segment["end"]
        filters.extend(
            [
                f"[0:v:0]setpts=PTS-STARTPTS,trim=start={start:.9f}:end={end:.9f},setpts=PTS-STARTPTS[v{index}]",
                f"[0:a:0]asetpts=PTS-STARTPTS,atrim=start={start:.9f}:end={end:.9f},asetpts=PTS-STARTPTS[a{index}]",
            ]
        )
        inputs.append(f"[v{index}][a{index}]")
    filters.append(
        f"{''.join(inputs)}concat=n={len(keep)}:v=1:a=1[vout][aout]"
    )
    return ";".join(filters)


def _has_nvenc(ffmpeg: str) -> bool:
    completed = _run([ffmpeg, "-hide_banner", "-encoders"], "encoder discovery")
    return "h264_nvenc" in completed.stdout


def _render_command(
    ffmpeg: str,
    input_path: Path,
    output_path: Path,
    keep: list[dict[str, float]],
    codec: str,
) -> list[str]:
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(input_path),
        "-filter_complex",
        _filter_graph(keep),
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-c:v",
        codec,
    ]
    if codec == "h264_nvenc":
        command += ["-preset", "p4", "-cq", "23"]
    else:
        command += ["-preset", "medium", "-crf", "23"]
    command += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output_path)]
    return command


def _render_mapping(keep: list[dict[str, float]]) -> list[dict[str, float]]:
    cursor = 0.0
    mapping = []
    for segment in keep:
        duration = segment["end"] - segment["start"]
        mapping.append({
            "output_start": cursor,
            "output_end": cursor + duration,
            "source_start": segment["start"],
            "source_end": segment["end"],
        })
        cursor += duration
    return mapping


def render_video(
    input_path: Path,
    output_path: Path,
    keep: list[dict[str, float]],
    diagnostics: dict[str, Any] | None = None,
) -> Path:
    ffmpeg = _require_executable("ffmpeg")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{output_path.stem}-", suffix=output_path.suffix, dir=output_path.parent, delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        codec = "h264_nvenc" if _has_nvenc(ffmpeg) else "libx264"
        command = _render_command(ffmpeg, input_path, temporary_path, keep, codec)
        try:
            _run(command, f"video render with {codec}")
        except MediaProcessError:
            if codec != "h264_nvenc":
                raise
            codec = "libx264"
            command = _render_command(ffmpeg, input_path, temporary_path, keep, codec)
            _run(command, "video render with libx264 fallback")
        if diagnostics is not None:
            diagnostics.update({
                "codec": codec,
                "ffmpeg_command": subprocess.list2cmdline(command),
                "ffmpeg_argv": command,
                "segments": _render_mapping(keep),
            })
        os.replace(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return output_path
