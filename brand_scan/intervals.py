from __future__ import annotations

from collections.abc import Iterable, Mapping

from .models import Detection


def merge_intervals(
    intervals: Iterable[Mapping[str, float]], padding: float, duration: float,
) -> list[dict[str, float]]:
    bounded = []
    for item in intervals:
        start = max(0.0, float(item["start"]) - padding)
        end = min(float(duration), float(item["end"]) + padding)
        if end > start:
            bounded.append((start, end))
    merged: list[list[float]] = []
    for start, end in sorted(bounded):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [{"start": start, "end": end} for start, end in merged]


def expand_temporal_evidence(
    detections: Iterable[Detection], duration: float, before: float, after: float,
) -> list[Detection]:
    result = []
    for item in detections:
        result.append(Detection(
            item.type, max(0.0, item.start - before), min(duration, item.end + after),
            item.confidence, item.detectors, item.reason,
        ))
    return result
