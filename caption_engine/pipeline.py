from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any

from silence_cutter.audio import MediaProcessError, extract_analysis_audio, probe_media

from .config import CaptionConfig
from .report import write_caption_report
from .segmenter import segment_transcript
from .srt import write_srt
from .transcriber import transcribe_audio


def generate_captions(
    input_path: str | Path,
    output_srt: str | Path | None = None,
    *,
    config: CaptionConfig | None = None,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    config = config or CaptionConfig()
    source = Path(input_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"input media does not exist: {source}")
    media = probe_media(source)
    if not media["has_audio"]:
        raise MediaProcessError("input media contains no audio stream")

    with tempfile.TemporaryDirectory(prefix="caption-engine-") as directory:
        audio_path = extract_analysis_audio(
            source, Path(directory) / "analysis.wav", 16_000
        )
        audio_duration = float(probe_media(audio_path)["duration"])
        transcription_started = time.perf_counter()
        transcription = transcribe_audio(
            audio_path, config, audio_duration=audio_duration
        )
        transcription_time = time.perf_counter() - transcription_started

    caption_started = time.perf_counter()
    captions = segment_transcript(transcription.segments, config)
    caption_processing_time = time.perf_counter() - caption_started

    srt_path = (
        Path(output_srt).expanduser().resolve()
        if output_srt is not None
        else source.with_suffix(".srt")
    )
    json_path = (
        Path(report_path).expanduser().resolve()
        if report_path is not None
        else Path(f"{source}.captions.json")
    )
    if source in (srt_path, json_path) or srt_path == json_path:
        raise ValueError("caption outputs must differ from input and each other")
    write_srt(srt_path, captions)
    total_processing_time = time.perf_counter() - started
    realtime_factor = total_processing_time / audio_duration if audio_duration else 0.0
    x_realtime = audio_duration / total_processing_time if total_processing_time else 0.0
    report = {
        "model": config.model_size,
        "language": transcription.language,
        "language_probability": transcription.language_probability,
        "audio_duration": audio_duration,
        "processing_time": total_processing_time,
        "transcription_time": transcription_time,
        "caption_processing_time": caption_processing_time,
        "total_processing_time": total_processing_time,
        "realtime_factor": realtime_factor,
        "x_realtime": x_realtime,
        "word_count": sum(len(segment.words) for segment in transcription.segments),
        "caption_count": len(captions),
        "config": config.to_dict(),
        "segments": [segment.to_dict() for segment in transcription.segments],
        "captions": [caption.to_dict() for caption in captions],
    }
    write_caption_report(json_path, report)
    return {
        "srt_path": str(srt_path),
        "report_path": str(json_path),
        **{key: report[key] for key in (
            "language",
            "language_probability",
            "audio_duration",
            "transcription_time",
            "caption_processing_time",
            "total_processing_time",
            "realtime_factor",
            "x_realtime",
            "word_count",
            "caption_count",
        )},
    }
