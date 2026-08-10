from __future__ import annotations

import json
from pathlib import Path

from silence_cutter.audio import _require_executable, _run


def export_review_clips(
    input_path: Path,
    disagreement_path: Path,
    output_dir: Path,
    *,
    limit: int = 30,
    max_duration: float = 6.0,
) -> list[Path]:
    ffmpeg = _require_executable("ffmpeg")
    disagreements = json.loads(disagreement_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    for index, item in enumerate(disagreements[:limit], start=1):
        start = float(item["start"])
        duration = min(float(item["duration"]), max_duration)
        target = output_dir / (
            f"{index:04d}_{item['detector']}_{start:.2f}_{start + duration:.2f}.wav"
        )
        _run([
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-ss", str(start), "-t", str(duration), "-i", str(input_path),
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target),
        ], "disagreement review extraction")
        outputs.append(target)
    return outputs
