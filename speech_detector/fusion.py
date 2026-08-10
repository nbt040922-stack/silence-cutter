from __future__ import annotations

from collections.abc import Iterable

from silence_cutter.timeline import build_timeline

from .config import HighRecallConfig
from .models import SpeechInterval

_EPSILON = 1e-9


def normalize_intervals(
    intervals: Iterable[SpeechInterval], duration: float, source: str
) -> list[SpeechInterval]:
    normalized = [
        (max(0.0, item.start), min(duration, item.end)) for item in intervals
        if item.end > 0 and item.start < duration
    ]
    merged: list[list[float]] = []
    for start, end in sorted(normalized):
        if end <= start:
            continue
        if merged and start <= merged[-1][1] + _EPSILON:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [SpeechInterval(start, end, source) for start, end in merged]


def union_intervals(
    silero: Iterable[SpeechInterval],
    sensevoice: Iterable[SpeechInterval],
    duration: float,
) -> list[SpeechInterval]:
    return normalize_intervals([*silero, *sensevoice], duration, "union")


def subtract_intervals(
    source: Iterable[SpeechInterval], covered: Iterable[SpeechInterval], label: str
) -> list[SpeechInterval]:
    covered_items = list(covered)
    result: list[SpeechInterval] = []
    for item in source:
        cursor = item.start
        for other in covered_items:
            if other.end <= cursor:
                continue
            if other.start >= item.end:
                break
            if other.start > cursor:
                result.append(SpeechInterval(cursor, min(item.end, other.start), label))
            cursor = max(cursor, other.end)
            if cursor >= item.end:
                break
        if cursor < item.end:
            result.append(SpeechInterval(cursor, item.end, label))
    return result


def overlap_duration(
    left: Iterable[SpeechInterval], right: Iterable[SpeechInterval]
) -> float:
    first, second = list(left), list(right)
    total = 0.0
    left_index = right_index = 0
    while left_index < len(first) and right_index < len(second):
        total += max(
            0.0,
            min(first[left_index].end, second[right_index].end)
            - max(first[left_index].start, second[right_index].start),
        )
        if first[left_index].end <= second[right_index].end:
            left_index += 1
        else:
            right_index += 1
    return total


def interval_duration(intervals: Iterable[SpeechInterval]) -> float:
    return sum(item.end - item.start for item in intervals)


def build_keep_cut(
    union: Iterable[SpeechInterval], duration: float, config: HighRecallConfig
) -> dict[str, list[dict[str, float]]]:
    return build_timeline(
        (item.to_dict() for item in union), duration, config.silence_config()
    )


def fully_covered(
    start: float, end: float, intervals: Iterable[SpeechInterval]
) -> bool:
    cursor = start
    for item in intervals:
        if item.end <= cursor:
            continue
        if item.start > cursor + _EPSILON:
            return False
        cursor = max(cursor, item.end)
        if cursor >= end - _EPSILON:
            return True
    return cursor >= end - _EPSILON
