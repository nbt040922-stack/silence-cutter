from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _model():
    from silero_vad import load_silero_vad

    return load_silero_vad()


def detect_speech(
    wav_path: Path, *, sample_rate: int = 16_000, threshold: float = 0.5
) -> list[dict[str, float]]:
    from silero_vad import get_speech_timestamps, read_audio

    audio = read_audio(str(wav_path), sampling_rate=sample_rate)
    timestamps = get_speech_timestamps(
        audio,
        _model(),
        threshold=threshold,
        sampling_rate=sample_rate,
        return_seconds=True,
    )
    return [
        {"start": float(segment["start"]), "end": float(segment["end"])}
        for segment in timestamps
    ]
