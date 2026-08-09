from __future__ import annotations

import re

from .config import CaptionConfig
from .models import CaptionSegment, TranscriptSegment, WordTimestamp

_SENTENCE_END = (".", "?", "!")
_SOFT_END = (",", ";", ":")


def _join_words(words: list[WordTimestamp]) -> str:
    text = " ".join(word.text for word in words if word.text)
    text = re.sub(r"\s+([,.;:!?%\]\)])", r"\1", text)
    return re.sub(r"([\[(])\s+", r"\1", text).strip()


def balance_lines(text: str, max_chars: int = 42, max_lines: int = 2) -> str:
    words = text.split()
    if max_lines <= 1 or len(text) <= max_chars or len(words) < 2:
        return text
    candidates: list[tuple[int, int, str]] = []
    for index in range(1, len(words)):
        left = " ".join(words[:index])
        right = " ".join(words[index:])
        if len(left) <= max_chars and len(right) <= max_chars:
            candidates.append((abs(len(left) - len(right)), max(len(left), len(right)), f"{left}\n{right}"))
    if candidates:
        return min(candidates)[2]
    return text


def _caption(words: list[WordTimestamp], config: CaptionConfig) -> CaptionSegment:
    text = balance_lines(
        _join_words(words), config.max_chars_per_line, config.max_lines
    )
    return CaptionSegment(
        start=words[0].start,
        end=words[-1].end,
        text=text,
        words=list(words),
    )


def _fits(words: list[WordTimestamp], config: CaptionConfig) -> bool:
    if len(words) > config.max_words_per_caption:
        return False
    if words[-1].end - words[0].start > config.max_caption_duration:
        return False
    lines = balance_lines(
        _join_words(words), config.max_chars_per_line, config.max_lines
    ).splitlines()
    return len(lines) <= config.max_lines and all(
        len(line) <= config.max_chars_per_line for line in lines
    )


def _can_merge(
    left: CaptionSegment, right: CaptionSegment, config: CaptionConfig
) -> bool:
    gap = right.words[0].start - left.words[-1].end
    return gap <= config.max_gap_between_words and _fits(
        left.words + right.words, config
    )


def _is_tiny(caption: CaptionSegment, config: CaptionConfig) -> bool:
    duration = caption.end - caption.start
    return duration < config.min_caption_duration or (
        len(caption.words) == 1 and len(caption.words[0].text) <= 6
    )


def _merge_tiny(
    captions: list[CaptionSegment], config: CaptionConfig
) -> list[CaptionSegment]:
    merged: list[CaptionSegment] = []
    for index, caption in enumerate(captions):
        if _is_tiny(caption, config) and merged and _can_merge(
            merged[-1], caption, config
        ):
            merged[-1] = _caption(merged[-1].words + caption.words, config)
        elif (
            _is_tiny(caption, config)
            and index + 1 < len(captions)
            and _can_merge(caption, captions[index + 1], config)
        ):
            captions[index + 1] = _caption(
                caption.words + captions[index + 1].words, config
            )
        else:
            merged.append(caption)
    return merged


def _merge_overlaps(
    captions: list[CaptionSegment], config: CaptionConfig
) -> list[CaptionSegment]:
    merged: list[CaptionSegment] = []
    for caption in captions:
        if merged and caption.start < merged[-1].end:
            merged[-1] = _caption(merged[-1].words + caption.words, config)
        else:
            merged.append(caption)
    return merged


def segment_transcript(
    segments: list[TranscriptSegment], config: CaptionConfig
) -> list[CaptionSegment]:
    words = sorted(
        (word for segment in segments for word in segment.words if word.text),
        key=lambda word: (word.start, word.end),
    )
    if not words:
        return []

    captions: list[CaptionSegment] = []
    current: list[WordTimestamp] = []

    def flush() -> None:
        if current:
            captions.append(_caption(current, config))
            current.clear()

    for word in words:
        if current and word.start - current[-1].end > config.max_gap_between_words:
            flush()
        if current and not _fits(current + [word], config):
            flush()
        current.append(word)
        text = _join_words(current)
        if word.text.endswith(_SENTENCE_END):
            flush()
        elif word.text.endswith(_SOFT_END) and (
            len(text) >= config.max_chars_per_line
            or len(current) >= max(2, config.max_words_per_caption // 2)
        ):
            flush()
    flush()
    return _merge_overlaps(_merge_tiny(captions, config), config)
