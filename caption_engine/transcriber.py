from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any
import wave

from .config import CaptionConfig
from .models import TranscriptSegment, TranscriptionResult, WordTimestamp, normalize_text


@lru_cache(maxsize=4)
def _get_model(
    model_size: str,
    device: str,
    compute_type: str,
    allow_cpu_fallback: bool,
    fallback_compute_type: str,
):
    from faster_whisper import WhisperModel

    try:
        return WhisperModel(model_size, device=device, compute_type=compute_type)
    except Exception as exc:
        if device == "cuda" and allow_cpu_fallback:
            return WhisperModel(
                model_size, device="cpu", compute_type=fallback_compute_type
            )
        raise RuntimeError(
            f"failed to initialize faster-whisper on {device} with {compute_type}"
        ) from exc


@lru_cache(maxsize=4)
def _get_batch_pipeline(model):
    from faster_whisper import BatchedInferencePipeline

    return BatchedInferencePipeline(model=model)


def _word(raw: Any) -> WordTimestamp:
    return WordTimestamp(
        text=getattr(raw, "word", getattr(raw, "text", "")),
        start=getattr(raw, "start", 0.0),
        end=getattr(raw, "end", 0.0),
        probability=getattr(raw, "probability", None),
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
    if config.batch_enabled:
        duration = audio_duration if audio_duration is not None else _wav_duration(audio_path)
        chunk_length = float(model.feature_extractor.chunk_length)
        raw_segments, info = _get_batch_pipeline(model).transcribe(
            str(audio_path),
            batch_size=config.batch_size,
            clip_timestamps=_clip_timestamps(duration, chunk_length),
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
    return segments, info


def transcribe_audio(
    audio_path: Path,
    config: CaptionConfig,
    *,
    audio_duration: float | None = None,
) -> TranscriptionResult:
    model = _get_model(
        config.model_size,
        config.device,
        config.compute_type,
        config.allow_cpu_fallback,
        config.fallback_compute_type,
    )
    try:
        segments, info = _transcribe(model, audio_path, config, audio_duration)
    except Exception as exc:
        if config.device == "cuda" and _is_cuda_error(exc):
            if config.allow_cpu_fallback:
                cpu_model = _get_model(
                    config.model_size,
                    "cpu",
                    config.fallback_compute_type,
                    False,
                    config.fallback_compute_type,
                )
                segments, info = _transcribe(
                    cpu_model, audio_path, config, audio_duration
                )
            else:
                raise RuntimeError(
                    "faster-whisper CUDA inference failed; install CUDA 12 with "
                    "cuBLAS and cuDNN 9, or explicitly request CPU/fallback"
                ) from exc
        else:
            raise
    probability = getattr(info, "language_probability", None)
    return TranscriptionResult(
        segments=segments,
        language=getattr(info, "language", config.language),
        language_probability=float(probability) if probability is not None else None,
        audio_duration=float(getattr(info, "duration", 0.0)),
    )
