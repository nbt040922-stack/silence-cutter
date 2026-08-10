from __future__ import annotations

import wave
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

    try:
        audio = read_audio(str(wav_path), sampling_rate=sample_rate)
    except (ImportError, RuntimeError):
        audio = _read_pcm_wav(wav_path, sample_rate)
    timestamps = get_speech_timestamps(
        audio,
        _model(),
        threshold=threshold,
        sampling_rate=sample_rate,
        speech_pad_ms=0,
        return_seconds=True,
    )
    return [
        {"start": float(segment["start"]), "end": float(segment["end"])}
        for segment in timestamps
    ]


def _read_pcm_wav(wav_path: Path, sample_rate: int):
    import torch

    with wave.open(str(wav_path), "rb") as wav:
        if (
            wav.getnchannels() != 1
            or wav.getsampwidth() != 2
            or wav.getframerate() != sample_rate
            or wav.getcomptype() != "NONE"
        ):
            raise RuntimeError("analysis audio must be mono 16-bit PCM at configured sample rate")
        frames = bytearray(wav.readframes(wav.getnframes()))
    return torch.frombuffer(frames, dtype=torch.int16).to(torch.float32).div_(32768.0)
