from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from .audio import MediaProcessError, extract_analysis_audio, probe_media
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

    media = probe_media(source)
    if not media["has_audio"]:
        raise MediaProcessError("input media contains no audio stream")
    input_duration = float(media["duration"])
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
    no_speech_detected = not speech or not timeline["keep"]
    if no_speech_detected:
        timeline = {
            "keep": [{"start": 0.0, "end": input_duration}],
            "cut": [],
        }
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    else:
        render_video(source, destination, timeline["keep"])

    timeline_removed_duration = sum(
        segment["end"] - segment["start"] for segment in timeline["cut"]
    )
    expected_output_duration = sum(
        segment["end"] - segment["start"] for segment in timeline["keep"]
    )
    actual_output_duration = float(probe_media(destination)["duration"])
    duration_error = abs(actual_output_duration - expected_output_duration)
    removed_duration = timeline_removed_duration
    report_path = destination.with_suffix(destination.suffix + ".json")
    report = {
        "input_duration": input_duration,
        "output_duration": actual_output_duration,
        "timeline_removed_duration": timeline_removed_duration,
        "expected_output_duration": expected_output_duration,
        "actual_output_duration": actual_output_duration,
        "duration_error": duration_error,
        "removed_duration": removed_duration,
        "removed_percentage": removed_duration / input_duration * 100,
        "speech_segment_count": len(speech),
        "cut_count": len(timeline["cut"]),
        "no_speech_detected": no_speech_detected,
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
        "output_duration": actual_output_duration,
        "timeline_removed_duration": timeline_removed_duration,
        "expected_output_duration": expected_output_duration,
        "actual_output_duration": actual_output_duration,
        "duration_error": duration_error,
        "removed_duration": removed_duration,
        "no_speech_detected": no_speech_detected,
        "processing_time": time.perf_counter() - started,
        "keep": timeline["keep"],
        "cut": timeline["cut"],
    }
