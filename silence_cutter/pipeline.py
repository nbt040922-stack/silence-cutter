from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any

from .audio import extract_analysis_audio, probe_media
from .config import SilenceCutterConfig
from .renderer import render_video
from .report import write_report
from .timeline import build_timeline
from .vad import detect_speech


def cut_silence(
    input_path: str | Path,
    output_path: str | Path,
    config: SilenceCutterConfig | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    config = config or SilenceCutterConfig()
    source = Path(input_path).expanduser().resolve()
    destination = Path(output_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"input video does not exist: {source}")
    if source == destination:
        raise ValueError("output_path must differ from input_path")

    input_duration = probe_media(source)["duration"]
    with tempfile.TemporaryDirectory(prefix="silence-cutter-") as directory:
        audio_path = extract_analysis_audio(
            source, Path(directory) / "analysis.wav", config.sample_rate
        )
        speech = detect_speech(
            audio_path,
            sample_rate=config.sample_rate,
            threshold=config.vad_threshold,
        )
    timeline = build_timeline(speech, input_duration, config)
    render_video(source, destination, timeline["keep"])

    output_duration = (
        probe_media(destination)["duration"] if timeline["keep"] else 0.0
    )
    removed_duration = max(0.0, input_duration - output_duration)
    report_path = destination.with_suffix(destination.suffix + ".json")
    report = {
        "input_duration": input_duration,
        "output_duration": output_duration,
        "removed_duration": removed_duration,
        "removed_percentage": removed_duration / input_duration * 100,
        "speech_segment_count": len(speech),
        "cut_count": len(timeline["cut"]),
        "config": config.to_dict(),
        "speech_segments": speech,
        "keep_segments": timeline["keep"],
        "cut_segments": timeline["cut"],
    }
    write_report(report_path, report)
    return {
        "output_path": str(destination),
        "report_path": str(report_path),
        "input_duration": input_duration,
        "output_duration": output_duration,
        "removed_duration": removed_duration,
        "processing_time": time.perf_counter() - started,
        "keep": timeline["keep"],
        "cut": timeline["cut"],
    }
