from pathlib import Path

from silence_cutter.vad import detect_speech

from .models import SpeechInterval


def detect_with_silero(
    audio_path: Path, *, sample_rate: int, threshold: float
) -> list[SpeechInterval]:
    return [
        SpeechInterval(item["start"], item["end"], "silero")
        for item in detect_speech(
            audio_path, sample_rate=sample_rate, threshold=threshold
        )
    ]
