from __future__ import annotations

from collections.abc import Iterable, Mapping

from .config import CaptionConfig
from .models import CaptionSegment, WordTimestamp

_EPSILON = 1e-9


def _intervals(items: Iterable[Mapping[str, object]]) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for item in items:
        try:
            start, end = max(0.0, float(item["start"])), float(item["end"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if end <= start:
            continue
        if result and start <= result[-1][1] + _EPSILON:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return result


def _duration(items: list[tuple[float, float]]) -> float:
    return sum(end - start for start, end in items)


def _intersection_duration(
    left: list[tuple[float, float]], right: list[tuple[float, float]]
) -> float:
    total = 0.0
    left_index = right_index = 0
    while left_index < len(left) and right_index < len(right):
        start = max(left[left_index][0], right[right_index][0])
        end = min(left[left_index][1], right[right_index][1])
        total += max(0.0, end - start)
        if left[left_index][1] <= right[right_index][1]:
            left_index += 1
        else:
            right_index += 1
    return total


def _subtract(
    source: list[tuple[float, float]], covered: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for start, end in source:
        cursor = start
        for covered_start, covered_end in covered:
            if covered_end <= cursor:
                continue
            if covered_start >= end:
                break
            if covered_start > cursor:
                result.append((cursor, min(end, covered_start)))
            cursor = max(cursor, covered_end)
            if cursor >= end:
                break
        if cursor < end:
            result.append((cursor, end))
    return result


def _word_intervals(captions: list[CaptionSegment]) -> list[tuple[float, float]]:
    return _intervals(
        {"start": word.start, "end": word.end}
        for caption in captions
        for word in caption.words
    )


def _caption_reference(
    captions: list[CaptionSegment], moment: float, *, previous: bool
) -> dict[str, object] | None:
    candidates = [
        (index, caption)
        for index, caption in enumerate(captions, start=1)
        if (caption.end <= moment if previous else caption.start >= moment)
    ]
    if not candidates:
        return None
    index, caption = candidates[-1] if previous else candidates[0]
    return {
        "index": index,
        "start": caption.start,
        "end": caption.end,
        "text": caption.text,
    }


def _gap_report(
    captions: list[CaptionSegment], start: float, end: float
) -> dict[str, object]:
    return {
        "start": start,
        "end": end,
        "duration": end - start,
        "nearest_previous_caption": _caption_reference(
            captions, start, previous=True
        ),
        "nearest_next_caption": _caption_reference(captions, end, previous=False),
    }


def resolve_caption_coverage(
    captions: list[CaptionSegment],
    speech_intervals: list[Mapping[str, object]] | None,
    config: CaptionConfig,
) -> tuple[list[CaptionSegment], dict[str, object]]:
    resolved = [
        CaptionSegment(
            min([caption.start, *(word.start for word in caption.words)]),
            max([caption.end, *(word.end for word in caption.words)]),
            caption.text,
            caption.words,
        )
        for caption in captions
    ]
    speech = _intervals(speech_intervals or [])
    vad_available = speech_intervals is not None
    bridged_count = 0
    bridged_duration = 0.0
    gap_classifications: list[dict[str, object]] = []

    for left, right in zip(resolved, resolved[1:]):
        gap = right.start - left.end
        if gap <= _EPSILON:
            continue
        speech_in_gap = _intersection_duration([(left.end, right.start)], speech)
        continuous = speech_in_gap >= gap * 0.8
        if gap <= config.caption_bridge_gap:
            classification, bridge = "short_hesitation", True
        elif continuous:
            classification, bridge = "speech_continuous", gap <= config.caption_hold_max
        elif vad_available and speech_in_gap <= _EPSILON:
            classification, bridge = "true_silence", False
        else:
            classification, bridge = "unknown", False
        gap_classifications.append({
            "start": left.end,
            "end": right.start,
            "duration": gap,
            "classification": classification,
            "bridged": bridge,
        })
        if bridge:
            left.end = right.start
            bridged_count += 1
            bridged_duration += gap

    caption_intervals = _intervals(
        {"start": caption.start, "end": caption.end} for caption in resolved
    )
    word_intervals = _word_intervals(resolved)
    if not vad_available:
        speech = word_intervals
    speech_duration = _duration(speech)
    covered_duration = _intersection_duration(speech, caption_intervals)
    uncovered = _subtract(speech, caption_intervals)
    untranscribed = [
        interval
        for interval in _subtract(speech, word_intervals)
        if interval[1] - interval[0] > config.caption_bridge_gap + _EPSILON
    ]
    return resolved, {
        "vad_available": vad_available,
        "speech_duration": speech_duration,
        "caption_covered_speech_duration": covered_duration,
        "caption_uncovered_speech_duration": max(0.0, speech_duration - covered_duration),
        "speech_coverage_percentage": (
            covered_duration / speech_duration * 100.0 if speech_duration else 100.0
        ),
        "bridged_gap_count": bridged_count,
        "bridged_gap_duration": bridged_duration,
        "untranscribed_speech_gap_count": len(untranscribed),
        "untranscribed_speech_gap_duration": _duration(untranscribed),
        "largest_uncovered_speech_gap": max(
            (end - start for start, end in uncovered), default=0.0
        ),
        "uncovered_speech_regions": [
            _gap_report(resolved, start, end) for start, end in uncovered
        ],
        "untranscribed_speech_gaps": [
            _gap_report(resolved, start, end) for start, end in untranscribed
        ],
        "gap_classifications": gap_classifications,
    }
