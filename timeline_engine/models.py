from __future__ import annotations

import math
from dataclasses import dataclass

from caption_engine.models import CaptionSegment, WordTimestamp


def _finite(value: float, name: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


@dataclass(frozen=True, slots=True)
class TimeRange:
    start: float
    end: float

    def __post_init__(self) -> None:
        start = _finite(self.start, "start")
        end = _finite(self.end, "end")
        if start < 0 or end <= start:
            raise ValueError("time range requires 0 <= start < end")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class TimelineSegment:
    source_start: float
    source_end: float
    output_start: float
    output_end: float

    @property
    def source_duration(self) -> float:
        return self.source_end - self.source_start

    def to_dict(self) -> dict[str, float]:
        return {
            "source_start": self.source_start,
            "source_end": self.source_end,
            "output_start": self.output_start,
            "output_end": self.output_end,
        }


@dataclass(frozen=True, slots=True)
class TimelineConfig:
    min_surviving_word_duration: float = 0.03
    epsilon: float = 1e-9
    render_duration_tolerance: float = 0.25

    def __post_init__(self) -> None:
        if self.min_surviving_word_duration < 0:
            raise ValueError("min_surviving_word_duration must be non-negative")
        if self.epsilon <= 0 or self.render_duration_tolerance < 0:
            raise ValueError("timeline tolerances must be positive")


@dataclass(frozen=True, slots=True)
class MappedWord:
    source_index: int
    timeline_segment_index: int
    source_start: float
    source_end: float
    word: WordTimestamp
    clipped: bool
    multi_keep: bool


@dataclass(frozen=True, slots=True)
class WordMappingResult:
    words: tuple[MappedWord, ...]
    words_before: int
    words_after: int
    boundary_word_clipped_count: int
    boundary_word_multi_keep_count: int


@dataclass(frozen=True, slots=True)
class CaptionMappingResult:
    captions: tuple[CaptionSegment, ...]
    word_mapping: WordMappingResult
