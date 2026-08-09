from __future__ import annotations

from collections.abc import Sequence

from caption_engine.config import CaptionConfig
from caption_engine.models import CaptionSegment
from caption_engine.segmenter import balance_lines, join_words

from .mapper import remap_words
from .models import CaptionMappingResult, MappedWord, TimelineConfig, TimelineSegment


def remap_captions(
    captions: Sequence[CaptionSegment],
    timeline: Sequence[TimelineSegment],
    *,
    timeline_config: TimelineConfig | None = None,
    caption_config: CaptionConfig | None = None,
) -> CaptionMappingResult:
    timeline_config = timeline_config or TimelineConfig()
    caption_config = caption_config or CaptionConfig()
    flat_words = [word for caption in captions for word in caption.words]
    mapping = remap_words(flat_words, timeline, timeline_config)
    mapped_by_source = {item.source_index: item for item in mapping.words}
    output: list[CaptionSegment] = []
    source_offset = 0

    for caption in captions:
        source_indices = range(source_offset, source_offset + len(caption.words))
        surviving = [mapped_by_source[index] for index in source_indices if index in mapped_by_source]
        source_offset += len(caption.words)
        if not surviving:
            continue
        groups: list[list[MappedWord]] = []
        for item in surviving:
            if groups and item.timeline_segment_index == groups[-1][-1].timeline_segment_index:
                groups[-1].append(item)
            else:
                groups.append([item])
        preserve_text = len(surviving) == len(caption.words) and len(groups) == 1
        for group in groups:
            words = [item.word for item in group]
            text = caption.text if preserve_text else balance_lines(
                join_words(words),
                caption_config.max_chars_per_line,
                caption_config.max_lines,
                [word.text for word in words],
            )
            output.append(
                CaptionSegment(
                    start=words[0].start,
                    end=words[-1].end,
                    text=text,
                    words=words,
                )
            )
    validate_remapped_captions(output, timeline, timeline_config)
    return CaptionMappingResult(tuple(output), mapping)


def validate_remapped_captions(
    captions: Sequence[CaptionSegment],
    timeline: Sequence[TimelineSegment],
    config: TimelineConfig,
) -> None:
    output_duration = timeline[-1].output_end if timeline else 0.0
    previous_start = -1.0
    previous_end = 0.0
    for caption in captions:
        if caption.start < 0 or caption.end <= caption.start:
            raise ValueError("caption timestamps must satisfy 0 <= start < end")
        if caption.start < previous_start - config.epsilon:
            raise ValueError("caption timestamps must be monotonic")
        if caption.start < previous_end - config.epsilon:
            raise ValueError("remapped captions must not overlap")
        if caption.end > output_duration + config.epsilon:
            raise ValueError("caption timestamp exceeds output timeline")
        if len(caption.text.splitlines()) > 2:
            raise ValueError("remapped captions must not exceed two lines")
        previous_start = caption.start
        previous_end = caption.end
