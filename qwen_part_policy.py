from __future__ import annotations

import math


QWEN_SOURCE_THRESHOLD_SECONDS = 1500.0
PART_QWEN_TRIGGER_SECONDS = 600.0
PART_QWEN_MAX_SECONDS = 480.0


def should_inspect_with_qwen(
    source_duration: float, threshold: float = QWEN_SOURCE_THRESHOLD_SECONDS,
) -> bool:
    duration = float(source_duration)
    if not math.isfinite(duration) or duration < 0:
        raise ValueError("source duration must be finite and non-negative")
    return duration > float(threshold)


def part_role(part_index: int) -> str | None:
    return {1: "INTRO", 2: "AD", 3: "OUTTRO"}.get(int(part_index))


def cap_part_range(
    start: float,
    end: float,
    source_duration: float,
    max_part_seconds: float = PART_QWEN_MAX_SECONDS,
    trigger_seconds: float = PART_QWEN_TRIGGER_SECONDS,
) -> dict[str, float]:
    source_duration = float(source_duration)
    start, end = float(start), float(end)
    if not all(math.isfinite(value) for value in (start, end, source_duration)):
        raise ValueError("part range values must be finite")
    if source_duration < 0 or not 0 <= start < end <= source_duration:
        raise ValueError("part range must be inside source duration")
    length = end - start
    if length <= float(trigger_seconds):
        return {"start": start, "end": end}
    capped_end = min(end, start + float(max_part_seconds))
    if capped_end - start >= float(max_part_seconds):
        return {"start": start, "end": capped_end}
    capped_start = max(0.0, min(start, source_duration - float(max_part_seconds)))
    capped_end = min(source_duration, capped_start + float(max_part_seconds))
    return {"start": capped_start, "end": capped_end}


def subtract_source_ranges(
    segments: list[dict[str, float]], removals: list[dict[str, float]],
) -> list[dict[str, float]]:
    current = [{"start": float(item["start"]), "end": float(item["end"])} for item in segments]
    for removal in removals:
        cut_start, cut_end = float(removal["start"]), float(removal["end"])
        next_segments: list[dict[str, float]] = []
        for item in current:
            if cut_end <= item["start"] or cut_start >= item["end"]:
                next_segments.append(item)
                continue
            if item["start"] < cut_start:
                next_segments.append({"start": item["start"], "end": min(cut_start, item["end"])})
            if cut_end < item["end"]:
                next_segments.append({"start": max(cut_end, item["start"]), "end": item["end"]})
        current = [item for item in next_segments if item["end"] > item["start"]]
    return current


def cap_source_segments(
    segments: list[dict[str, float]], max_seconds: float = PART_QWEN_MAX_SECONDS,
) -> list[dict[str, float]]:
    result: list[dict[str, float]] = []
    remaining = float(max_seconds)
    for item in segments:
        if remaining <= 0:
            break
        start, end = float(item["start"]), float(item["end"])
        length = min(end - start, remaining)
        if length > 0:
            result.append({"start": start, "end": start + length})
            remaining -= length
    return result
