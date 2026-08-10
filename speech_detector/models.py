from __future__ import annotations

import math
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class SpeechInterval:
    start: float
    end: float
    source: str
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.start) or not math.isfinite(self.end):
            raise ValueError("speech interval times must be finite")
        if self.start < 0 or self.end <= self.start:
            raise ValueError("speech interval must have positive non-negative duration")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
