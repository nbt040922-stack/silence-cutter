from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .intervals import merge_intervals
from .models import Detection


def group_frame_evidence(
    frames: Iterable[Mapping[str, Any]], duration: float,
    min_consecutive: int, padding: float,
) -> list[Detection]:
    grouped: list[Detection] = []
    by_type: dict[str, list[Mapping[str, Any]]] = {}
    for frame in sorted(frames, key=lambda item: float(item["timestamp"])):
        by_type.setdefault(str(frame.get("type", "")).upper(), []).append(frame)
    for label, items in by_type.items():
        if label not in {"PERSONAL_BRAND", "SPONSOR", "QR", "ADVERTISEMENT"}:
            continue
        run: list[Mapping[str, Any]] = []
        for item in items:
            if run and float(item["timestamp"]) - float(run[-1]["timestamp"]) > 2.5:
                if len(run) >= min_consecutive:
                    grouped.append(_run_detection(label, run))
                run = []
            run.append(item)
        if len(run) >= min_consecutive:
            grouped.append(_run_detection(label, run))
    expanded = merge_intervals(
        [{"start": item.start, "end": item.end} for item in grouped], padding, duration,
    )
    result = []
    for interval in expanded:
        overlapping = [item for item in grouped if item.start <= interval["end"] and item.end >= interval["start"]]
        source = max(overlapping, key=lambda item: item.confidence)
        result.append(Detection(source.type, interval["start"], interval["end"], source.confidence, source.detectors, source.reason))
    return result


def _run_detection(label: str, run: list[Mapping[str, Any]]) -> Detection:
    confidence = max(float(item.get("confidence", 0.0)) for item in run)
    return Detection(
        label, float(run[0]["timestamp"]), float(run[-1]["timestamp"]), confidence,
        ("frame_evidence",), str(run[-1].get("reason") or "consecutive visual evidence"),
    )


def combine_detector_evidence(
    qwen: Iterable[Detection], qr: Iterable[Detection], duration: float,
) -> list[Detection]:
    all_items = list(qwen) + list(qr)
    result: list[Detection] = []
    for item in all_items:
        overlaps = [existing for existing in result if existing.type == item.type and existing.start <= item.end and existing.end >= item.start]
        if not overlaps:
            result.append(item)
            continue
        existing = overlaps[0]
        detectors = tuple(dict.fromkeys((*existing.detectors, *item.detectors)))
        result.remove(existing)
        result.append(Detection(
            existing.type, max(0.0, min(existing.start, item.start)),
            min(float(duration), max(existing.end, item.end)),
            max(existing.confidence, item.confidence), detectors,
            existing.reason or item.reason,
        ))
    return sorted(result, key=lambda item: (item.start, item.end))
