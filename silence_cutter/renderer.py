from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .audio import MediaProcessError, _require_executable, _run


def _filter_graph(keep: list[dict[str, float]]) -> str:
    segments = keep or [{"start": 0.0, "end": 0.0}]
    filters: list[str] = []
    inputs: list[str] = []
    for index, segment in enumerate(segments):
        start, end = segment["start"], segment["end"]
        filters.extend(
            [
                f"[0:v:0]trim=start={start:.9f}:end={end:.9f},setpts=PTS-STARTPTS[v{index}]",
                f"[0:a:0]atrim=start={start:.9f}:end={end:.9f},asetpts=PTS-STARTPTS[a{index}]",
            ]
        )
        inputs.append(f"[v{index}][a{index}]")
    filters.append(
        f"{''.join(inputs)}concat=n={len(segments)}:v=1:a=1[vout][aout]"
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
        command += ["-preset", "p5", "-cq", "23"]
    else:
        command += ["-preset", "medium", "-crf", "23"]
    command += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output_path)]
    return command


def render_video(
    input_path: Path, output_path: Path, keep: list[dict[str, float]]
) -> Path:
    ffmpeg = _require_executable("ffmpeg")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{output_path.stem}-", suffix=output_path.suffix, dir=output_path.parent, delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        codec = "h264_nvenc" if _has_nvenc(ffmpeg) else "libx264"
        try:
            _run(
                _render_command(ffmpeg, input_path, temporary_path, keep, codec),
                f"video render with {codec}",
            )
        except MediaProcessError:
            if codec != "h264_nvenc":
                raise
            _run(
                _render_command(ffmpeg, input_path, temporary_path, keep, "libx264"),
                "video render with libx264 fallback",
            )
        os.replace(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return output_path
