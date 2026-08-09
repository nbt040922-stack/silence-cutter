from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any
import time
import wave

from .config import CaptionConfig
from .models import TranscriptSegment, TranscriptionResult, WordTimestamp, normalize_text


@lru_cache(maxsize=4)
def _get_model(
    model_size: str,
    device: str,
    compute_type: str,
):
    from faster_whisper import WhisperModel

    return WhisperModel(model_size, device=device, compute_type=compute_type)


@lru_cache(maxsize=4)
def _get_batch_pipeline(model):
    from faster_whisper import BatchedInferencePipeline

    return BatchedInferencePipeline(model=model)


def _word(raw: Any) -> WordTimestamp:
    text = str(getattr(raw, "word", getattr(raw, "text", "")) or "")
    return WordTimestamp(
        text=text,
        start=getattr(raw, "start", 0.0),
        end=getattr(raw, "end", 0.0),
        probability=getattr(raw, "probability", None),
        space_before=True if text[:1].isspace() else None,
    )


def _clip_timestamps(duration: float, chunk_length: float) -> list[dict[str, float]]:
    clips: list[dict[str, float]] = []
    start = 0.0
    while start < duration:
        end = min(start + chunk_length, duration)
        clips.append({"start": start, "end": end})
        start = end
    return clips


def _wav_duration(audio_path: Path) -> float:
    with wave.open(str(audio_path), "rb") as audio:
        return audio.getnframes() / audio.getframerate()


def _is_cuda_error(error: Exception) -> bool:
    message = str(error).lower()
    return any(name in message for name in ("cuda", "cublas", "cudnn"))


def _load_model(model_size: str, device: str, compute_type: str):
    cache_info = getattr(_get_model, "cache_info", None)
    hits_before = getattr(cache_info(), "hits", None) if cache_info else None
    if not isinstance(hits_before, int):
        hits_before = None
    started = time.perf_counter()
    try:
        model = _get_model(model_size, device, compute_type)
    except Exception as exc:
        raise RuntimeError(
            f"failed to initialize faster-whisper on {device} with {compute_type}"
        ) from exc
    elapsed = time.perf_counter() - started
    hits_after = getattr(cache_info(), "hits", None) if cache_info else None
    cached = bool(
        hits_before is not None
        and isinstance(hits_after, int)
        and hits_after > hits_before
    )
    return model, elapsed, cached


def _runtime_backend(model, selected_device: str) -> tuple[str, str | None]:
    backend = getattr(model, "model", None)
    actual_device = getattr(backend, "device", None)
    actual_compute_type = getattr(backend, "compute_type", None)
    if not isinstance(actual_device, str):
        actual_device = selected_device
    if not isinstance(actual_compute_type, str):
        actual_compute_type = None
    return actual_device, actual_compute_type


def _transcribe(
    model,
    audio_path: Path,
    config: CaptionConfig,
    audio_duration: float | None,
):
    options = {
        "language": config.language,
        "beam_size": config.beam_size,
        "word_timestamps": True,
        "vad_filter": False,
    }
    manual_clip_timestamps_used = False
    if config.batch_enabled:
        duration = audio_duration if audio_duration is not None else _wav_duration(audio_path)
        chunk_length = float(model.feature_extractor.chunk_length)
        batch_options: dict[str, Any] = {"batch_size": config.batch_size}
        if duration >= chunk_length:
            # Upstream batch mode rejects long audio without clips when VAD is off.
            batch_options["clip_timestamps"] = _clip_timestamps(duration, chunk_length)
            manual_clip_timestamps_used = True
        raw_segments, info = _get_batch_pipeline(model).transcribe(
            str(audio_path),
            **batch_options,
            **options,
        )
    else:
        raw_segments, info = model.transcribe(str(audio_path), **options)

    segments: list[TranscriptSegment] = []
    for raw in raw_segments:
        words = [_word(item) for item in (getattr(raw, "words", None) or [])]
        words = [word for word in words if word.text]
        segments.append(
            TranscriptSegment(
                start=words[0].start if words else getattr(raw, "start", 0.0),
                end=words[-1].end if words else getattr(raw, "end", 0.0),
                text=normalize_text(getattr(raw, "text", "")),
                words=words,
            )
        )
    return segments, info, manual_clip_timestamps_used


def transcribe_audio(
    audio_path: Path,
    config: CaptionConfig,
    *,
    audio_duration: float | None = None,
) -> TranscriptionResult:
    cpu_fallback_used = False
    cuda_runtime = None
    model_initialization_time = 0.0
    inference_time = 0.0
    selected_device = config.device
    selected_compute_type = config.compute_type
    if config.device.casefold() == "cuda":
        from .cuda_runtime import cuda_runtime_error, prepare_windows_cuda_runtime

        cuda_status = prepare_windows_cuda_runtime()
        cuda_runtime = cuda_status.to_dict()
        if cuda_status.applicable and not cuda_status.available:
            if not config.allow_cpu_fallback:
                raise cuda_runtime_error()
            cpu_fallback_used = True
            selected_device = "cpu"
            selected_compute_type = config.fallback_compute_type

    initialization_started = time.perf_counter()
    try:
        model, elapsed, model_cached = _load_model(
            config.model_size, selected_device, selected_compute_type
        )
    except Exception as exc:
        model_initialization_time += time.perf_counter() - initialization_started
        if selected_device == "cuda" and config.allow_cpu_fallback:
            cpu_fallback_used = True
            selected_device = "cpu"
            selected_compute_type = config.fallback_compute_type
            model, elapsed, model_cached = _load_model(
                config.model_size, selected_device, selected_compute_type
            )
            model_initialization_time += elapsed
        else:
            raise
    else:
        model_initialization_time += elapsed

    inference_started = time.perf_counter()
    try:
        segments, info, manual_clips = _transcribe(
            model, audio_path, config, audio_duration
        )
    except Exception as exc:
        inference_time += time.perf_counter() - inference_started
        if (
            config.device == "cuda"
            and not cpu_fallback_used
            and config.allow_cpu_fallback
            and _is_cuda_error(exc)
        ):
            cpu_fallback_used = True
            selected_device = "cpu"
            selected_compute_type = config.fallback_compute_type
            model, elapsed, model_cached = _load_model(
                config.model_size, selected_device, selected_compute_type
            )
            model_initialization_time += elapsed
            inference_started = time.perf_counter()
            segments, info, manual_clips = _transcribe(
                model, audio_path, config, audio_duration
            )
        elif config.device == "cuda" and _is_cuda_error(exc):
            raise RuntimeError(
                "faster-whisper CUDA inference failed; install CUDA 12 with "
                "cuBLAS and cuDNN 9, or explicitly request CPU/fallback"
            ) from exc
        else:
            raise
    inference_time += time.perf_counter() - inference_started

    actual_device, actual_compute_type = _runtime_backend(model, selected_device)
    probability = getattr(info, "language_probability", None)
    return TranscriptionResult(
        segments=segments,
        language=getattr(info, "language", config.language),
        language_probability=float(probability) if probability is not None else None,
        audio_duration=float(getattr(info, "duration", 0.0)),
        requested_device=config.device,
        requested_compute_type=config.compute_type,
        actual_device=actual_device,
        actual_compute_type=actual_compute_type,
        batch_enabled=config.batch_enabled,
        batch_size=config.batch_size if config.batch_enabled else 1,
        cpu_fallback_used=cpu_fallback_used,
        model_initialization_time=model_initialization_time,
        model_initialization_cached=model_cached,
        transcription_inference_time=inference_time,
        manual_clip_timestamps_used=manual_clips,
        cuda_runtime=cuda_runtime,
    )
