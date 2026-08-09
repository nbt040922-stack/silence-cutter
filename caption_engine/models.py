from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field


def normalize_text(text: object) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _time(value: object, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return result if math.isfinite(result) else default


@dataclass(slots=True)
class WordTimestamp:
    text: str
    start: float
    end: float
    probability: float | None = None
    space_before: bool | None = None

    def __post_init__(self) -> None:
        self.text = normalize_text(self.text)
        self.start = max(0.0, _time(self.start))
        self.end = max(self.start, _time(self.end, self.start))
        if self.probability is not None:
            probability = _time(self.probability, -1.0)
            self.probability = probability if 0.0 <= probability <= 1.0 else None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class TranscriptSegment:
    start: float
    end: float
    text: str
    words: list[WordTimestamp] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.text = normalize_text(self.text)
        self.start = max(0.0, _time(self.start))
        self.end = max(self.start, _time(self.end, self.start))

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class CaptionSegment:
    start: float
    end: float
    text: str
    words: list[WordTimestamp] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.text = str(self.text).strip()
        self.start = max(0.0, _time(self.start))
        self.end = max(self.start, _time(self.end, self.start))

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class TranscriptionResult:
    segments: list[TranscriptSegment]
    language: str | None
    language_probability: float | None
    audio_duration: float
    requested_device: str | None = None
    requested_compute_type: str | None = None
    actual_device: str | None = None
    actual_compute_type: str | None = None
    batch_enabled: bool = False
    batch_size: int = 1
    cpu_fallback_used: bool = False
    model_initialization_time: float = 0.0
    model_initialization_cached: bool = False
    transcription_inference_time: float = 0.0
    manual_clip_timestamps_used: bool = False
    cuda_runtime: dict[str, object] | None = None
