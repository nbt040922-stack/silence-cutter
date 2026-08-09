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

    extraction_started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="caption-engine-") as directory:
        audio_path = extract_analysis_audio(
            source, Path(directory) / "analysis.wav", 16_000
        )
        audio_duration = float(probe_media(audio_path)["duration"])
        audio_extraction_time = time.perf_counter() - extraction_started
        transcription = transcribe_audio(
            audio_path, config, audio_duration=audio_duration
        )

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
    output_started = time.perf_counter()
    write_srt(srt_path, captions)
    output_write_time = time.perf_counter() - output_started
    total_processing_time = time.perf_counter() - started
    realtime_factor = total_processing_time / audio_duration if audio_duration else 0.0
    x_realtime = audio_duration / total_processing_time if total_processing_time else 0.0
    long_single_token_caption_count = sum(
        len(caption.words) == 1
        and caption.end - caption.start > config.max_caption_duration
        for caption in captions
    )
    report = {
        "model": config.model_size,
        "language": transcription.language,
        "language_probability": transcription.language_probability,
        "audio_duration": audio_duration,
        "processing_time": total_processing_time,
        "audio_extraction_time": audio_extraction_time,
        "model_initialization_time": transcription.model_initialization_time,
        "model_initialization_cached": transcription.model_initialization_cached,
        "transcription_inference_time": transcription.transcription_inference_time,
        "transcription_time": (
            transcription.model_initialization_time
            + transcription.transcription_inference_time
        ),
        "caption_processing_time": caption_processing_time,
        "output_write_time": output_write_time,
        "total_processing_time": total_processing_time,
        "realtime_factor": realtime_factor,
        "x_realtime": x_realtime,
        "word_count": sum(len(segment.words) for segment in transcription.segments),
        "caption_count": len(captions),
        "long_single_token_caption_count": long_single_token_caption_count,
        "requested_device": transcription.requested_device,
        "requested_compute_type": transcription.requested_compute_type,
        "actual_device": transcription.actual_device,
        "actual_compute_type": transcription.actual_compute_type,
        "batch_enabled": transcription.batch_enabled,
        "batch_size": transcription.batch_size,
        "cpu_fallback_used": transcription.cpu_fallback_used,
        "manual_clip_timestamps_used": transcription.manual_clip_timestamps_used,
        "cuda_runtime": transcription.cuda_runtime,
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
            "audio_extraction_time",
            "model_initialization_time",
            "model_initialization_cached",
            "transcription_inference_time",
            "transcription_time",
            "caption_processing_time",
            "output_write_time",
            "total_processing_time",
            "realtime_factor",
            "x_realtime",
            "word_count",
            "caption_count",
            "long_single_token_caption_count",
            "requested_device",
            "requested_compute_type",
            "actual_device",
            "actual_compute_type",
            "batch_enabled",
            "batch_size",
            "cpu_fallback_used",
            "manual_clip_timestamps_used",
            "cuda_runtime",
        )},
    }
