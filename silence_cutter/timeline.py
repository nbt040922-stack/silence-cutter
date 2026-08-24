from __future__ import annotations

import math
import random
from collections.abc import Iterable, Mapping

from .config import SilenceCutterConfig

Segment = dict[str, float]
_EPSILON = 1e-9


def _merge(segments: list[Segment], max_gap: float = 0.0) -> list[Segment]:
    merged: list[Segment] = []
    for segment in segments:
        if merged and segment["start"] <= merged[-1]["end"] + max_gap + _EPSILON:
            merged[-1]["end"] = max(merged[-1]["end"], segment["end"])
        else:
            merged.append(segment.copy())
    return merged


def _normalize(
    speech: Iterable[Mapping[str, object]], duration: float
) -> list[Segment]:
    normalized: list[Segment] = []
    for raw in speech:
        try:
            start = float(raw["start"])
            end = float(raw["end"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(start) or not math.isfinite(end):
            continue
        start = min(duration, max(0.0, start))
        end = min(duration, max(0.0, end))
        if end - start > _EPSILON:
            normalized.append({"start": start, "end": end})
    normalized.sort(key=lambda item: (item["start"], item["end"]))
    return normalized


def _complement(segments: list[Segment], duration: float) -> list[Segment]:
    result: list[Segment] = []
    cursor = 0.0
    for segment in segments:
        if segment["start"] - cursor > _EPSILON:
            result.append({"start": cursor, "end": segment["start"]})
        cursor = max(cursor, segment["end"])
    if duration - cursor > _EPSILON:
        result.append({"start": cursor, "end": duration})
    return result


def _expand_short_keeps(
    keeps: list[Segment], duration: float, minimum: float
) -> list[Segment]:
    if minimum <= 0:
        return keeps
    expanded: list[Segment] = []
    for keep in keeps:
        missing = minimum - (keep["end"] - keep["start"])
        if missing > _EPSILON:
            left = min(keep["start"], missing / 2)
            right = min(duration - keep["end"], missing - left)
            left += min(keep["start"] - left, missing - left - right)
            keep = {"start": keep["start"] - left, "end": keep["end"] + right}
        expanded.append(keep)
    return _merge(expanded)


def _naturalize_gaps(
    gaps: list[Segment], duration: float, config: SilenceCutterConfig,
) -> list[Segment]:
    """Leave a small, bounded silence tail/lead around each real cut."""
    if not gaps or config.natural_silence_max <= 0:
        return gaps
    rng = random.Random(config.natural_silence_seed)
    result: list[Segment] = []
    for gap in gaps:
        length = gap["end"] - gap["start"]
        maximum = min(config.natural_silence_max, max(0.0, length - _EPSILON))
        minimum = min(config.natural_silence_min, maximum)
        retained = rng.uniform(minimum, maximum)
        if retained <= _EPSILON:
            result.append(gap)
            continue
        if gap["start"] <= _EPSILON:
            result.append({"start": gap["start"], "end": gap["end"] - retained})
        elif gap["end"] >= duration - _EPSILON:
            result.append({"start": gap["start"] + retained, "end": gap["end"]})
        elif rng.random() < 0.5:
            result.append({"start": gap["start"], "end": gap["end"] - retained})
        else:
            result.append({"start": gap["start"] + retained, "end": gap["end"]})
    return result


def build_timeline(
    speech_timestamps: Iterable[Mapping[str, object]],
    total_duration: float,
    config: SilenceCutterConfig,
) -> dict[str, list[Segment]]:
    if not math.isfinite(total_duration) or total_duration < 0:
        raise ValueError("total_duration must be a finite non-negative number")
    if total_duration == 0:
        return {"keep": [], "cut": []}

    speech = _merge(_normalize(speech_timestamps, total_duration), config.merge_gap)
    if not speech:
        return {"keep": [], "cut": [{"start": 0.0, "end": total_duration}]}
    padded = [
        {
            "start": max(0.0, item["start"] - config.speech_pad_before),
            "end": min(total_duration, item["end"] + config.speech_pad_after),
        }
        for item in speech
    ]
    protected = _merge(padded)

    long_gaps = [
        gap
        for gap in _complement(protected, total_duration)
        if gap["end"] - gap["start"] + _EPSILON >= config.min_silence_duration
    ]
    long_gaps = _naturalize_gaps(long_gaps, total_duration, config)
    keeps = _complement(long_gaps, total_duration)
    keeps = _expand_short_keeps(keeps, total_duration, config.min_keep_duration)
    cuts = _complement(keeps, total_duration)
    return {"keep": keeps, "cut": cuts}
