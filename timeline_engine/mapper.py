from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence

from caption_engine.models import WordTimestamp

from .models import MappedWord, TimeRange, TimelineConfig, TimelineSegment, WordMappingResult


def build_timeline_segments(
    keep: Iterable[TimeRange | Mapping[str, float]], *, epsilon: float = 1e-9
) -> tuple[TimelineSegment, ...]:
    ranges = tuple(
        item
        if isinstance(item, TimeRange)
        else TimeRange(float(item["start"]), float(item["end"]))
        for item in keep
    )
    segments: list[TimelineSegment] = []
    output_cursor = 0.0
    previous_end = -math.inf
    for item in ranges:
        if item.start < previous_end - epsilon:
            raise ValueError("KEEP intervals must be sorted and non-overlapping")
        output_end = output_cursor + item.duration
        segments.append(
            TimelineSegment(item.start, item.end, output_cursor, output_end)
        )
        previous_end = item.end
        output_cursor = output_end
    validate_timeline_segments(segments, epsilon=epsilon)
    return tuple(segments)


def validate_timeline_segments(
    segments: Sequence[TimelineSegment], *, epsilon: float = 1e-9
) -> None:
    expected_output = 0.0
    previous_source_end = -math.inf
    for segment in segments:
        values = (
            segment.source_start,
            segment.source_end,
            segment.output_start,
            segment.output_end,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("timeline values must be finite")
        if segment.source_start < 0 or segment.output_start < 0:
            raise ValueError("timeline timestamps must be non-negative")
        if segment.source_end <= segment.source_start:
            raise ValueError("source_start must be before source_end")
        if segment.output_end <= segment.output_start:
            raise ValueError("output_start must be before output_end")
        if segment.source_start < previous_source_end - epsilon:
            raise ValueError("source timeline must not overlap")
        if abs(segment.output_start - expected_output) > epsilon:
            raise ValueError("output timeline must be contiguous")
        if abs(segment.source_duration - (segment.output_end - segment.output_start)) > epsilon:
            raise ValueError("timeline segment durations must match")
        previous_source_end = segment.source_end
        expected_output = segment.output_end
    expected_duration = math.fsum(segment.source_duration for segment in segments)
    if segments and abs(segments[-1].output_end - expected_duration) > epsilon:
        raise ValueError("output duration must equal summed KEEP duration")


def map_source_time_to_output_time(
    timestamp: float,
    segments: Sequence[TimelineSegment],
    *,
    epsilon: float = 1e-9,
) -> float | None:
    if not math.isfinite(timestamp) or timestamp < -epsilon or not segments:
        return None
    starts = [segment.source_start for segment in segments]
    index = bisect_right(starts, timestamp + epsilon) - 1
    if index < 0:
        return None
    segment = segments[index]
    if timestamp < segment.source_start - epsilon or timestamp > segment.source_end + epsilon:
        return None
    source_time = min(segment.source_end, max(segment.source_start, timestamp))
    return segment.output_start + source_time - segment.source_start


def remap_words(
    words: Sequence[WordTimestamp],
    segments: Sequence[TimelineSegment],
    config: TimelineConfig | None = None,
) -> WordMappingResult:
    config = config or TimelineConfig()
    mapped: list[MappedWord] = []
    clipped_count = 0
    multi_keep_count = 0
    keep_index = 0
    previous_word_start = -math.inf
    previous_output_start = -math.inf

    for source_index, word in enumerate(words):
        if word.start < previous_word_start - config.epsilon:
            raise ValueError("word timestamps must be sorted")
        previous_word_start = word.start
        while (
            keep_index < len(segments)
            and segments[keep_index].source_end <= word.start + config.epsilon
        ):
            keep_index += 1

        surviving: list[tuple[float, int, float, float]] = []
        candidate_index = keep_index
        while (
            candidate_index < len(segments)
            and segments[candidate_index].source_start < word.end - config.epsilon
        ):
            segment = segments[candidate_index]
            start = max(word.start, segment.source_start)
            end = min(word.end, segment.source_end)
            duration = end - start
            if duration + config.epsilon >= config.min_surviving_word_duration:
                surviving.append((duration, candidate_index, start, end))
            candidate_index += 1
        if not surviving:
            continue

        multi_keep = len(surviving) > 1
        selected = surviving[0]
        for candidate in surviving[1:]:
            if candidate[0] > selected[0] + config.epsilon:
                selected = candidate
        _, segment_index, source_start, source_end = selected
        segment = segments[segment_index]
        output_start = segment.output_start + source_start - segment.source_start
        output_end = segment.output_start + source_end - segment.source_start
        clipped = (
            abs(source_start - word.start) > config.epsilon
            or abs(source_end - word.end) > config.epsilon
        )
        if output_start < previous_output_start - config.epsilon:
            raise ValueError("mapped word timestamps must be monotonic")
        previous_output_start = output_start
        if clipped:
            clipped_count += 1
        if multi_keep:
            multi_keep_count += 1
        mapped.append(
            MappedWord(
                source_index=source_index,
                timeline_segment_index=segment_index,
                source_start=source_start,
                source_end=source_end,
                word=WordTimestamp(
                    text=word.text,
                    start=output_start,
                    end=output_end,
                    probability=word.probability,
                    space_before=word.space_before,
                ),
                clipped=clipped,
                multi_keep=multi_keep,
            )
        )
    return WordMappingResult(
        words=tuple(mapped),
        words_before=len(words),
        words_after=len(mapped),
        boundary_word_clipped_count=clipped_count,
        boundary_word_multi_keep_count=multi_keep_count,
    )
