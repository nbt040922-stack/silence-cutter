from __future__ import annotations

from .config import CaptionConfig
from .models import CaptionSegment, TranscriptSegment, WordTimestamp

_SENTENCE_END = (".", "?", "!", "。", "？", "！")
_SOFT_END = (",", ";", ":", "、", "，", "；", "：")
_CLOSE_PUNCTUATION = set(",.;:!?%)]}、。，；：！？）］｝」』】》〉")
_OPEN_PUNCTUATION = set("([{（［｛「『【《〈")


def _is_cjk_char(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x1100 <= codepoint <= 0x11FF
        or 0x2E80 <= codepoint <= 0x2FFF
        or 0x3040 <= codepoint <= 0x30FF
        or 0x3130 <= codepoint <= 0x318F
        or 0x31F0 <= codepoint <= 0x31FF
        or 0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xAC00 <= codepoint <= 0xD7AF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x2FA1F
    )


def _has_cjk(text: str) -> bool:
    return any(_is_cjk_char(character) for character in text)


def _join_texts(
    texts: list[str], space_before: list[bool | None] | None = None
) -> str:
    result = ""
    items = [
        (text.strip(), index) for index, text in enumerate(texts) if text.strip()
    ]
    for text, index in items:
        if not result:
            result = text
        elif text[0] in _CLOSE_PUNCTUATION or result[-1] in _OPEN_PUNCTUATION:
            result += text
        elif space_before is not None and space_before[index] is True:
            result += " " + text
        elif space_before is not None and space_before[index] is False:
            result += text
        elif (
            _is_cjk_char(result[-1])
            or _is_cjk_char(text[0])
        ):
            result += text
        else:
            result += " " + text
    return result


def _join_words(words: list[WordTimestamp]) -> str:
    return _join_texts(
        [word.text for word in words], [word.space_before for word in words]
    )


def balance_lines(
    text: str,
    max_chars: int = 42,
    max_lines: int = 2,
    tokens: list[str] | None = None,
) -> str:
    spaced_cjk = _has_cjk(text) and any(character.isspace() for character in text)
    source_units = text.split() if spaced_cjk else tokens or text.split()
    units = [token for token in source_units if token]
    join = (
        (lambda parts: " ".join(parts))
        if spaced_cjk or not _has_cjk(text)
        else _join_texts
    )
    if max_lines <= 1 or len(text) <= max_chars or len(units) < 2:
        return text
    candidates: list[tuple[int, int, str]] = []
    for index in range(1, len(units)):
        left = join(units[:index])
        right = join(units[index:])
        if (
            len(left) <= max_chars
            and len(right) <= max_chars
            and right[0] not in _CLOSE_PUNCTUATION
            and left[-1] not in _OPEN_PUNCTUATION
        ):
            candidates.append(
                (
                    abs(len(left) - len(right)),
                    max(len(left), len(right)),
                    f"{left}\n{right}",
                )
            )
    if candidates:
        return min(candidates)[2]
    if _has_cjk(text) and len(text) <= max_chars * max_lines:
        split = min(max_chars, max(1, len(text) // 2))
        return f"{text[:split]}\n{text[split:]}"
    return text


def _caption(words: list[WordTimestamp], config: CaptionConfig) -> CaptionSegment:
    text = balance_lines(
        _join_words(words),
        config.max_chars_per_line,
        config.max_lines,
        [word.text for word in words],
    )
    return CaptionSegment(
        start=words[0].start,
        end=words[-1].end,
        text=text,
        words=list(words),
    )


def _fits(words: list[WordTimestamp], config: CaptionConfig) -> bool:
    text = _join_words(words)
    if not _has_cjk(text) and len(words) > config.max_words_per_caption:
        return False
    if words[-1].end - words[0].start > config.max_caption_duration:
        return False
    lines = balance_lines(
        text,
        config.max_chars_per_line,
        config.max_lines,
        [word.text for word in words],
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
        not _has_cjk(caption.text)
        and len(caption.words) == 1
        and len(caption.words[0].text) <= 6
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
            or (
                not _has_cjk(text)
                and len(current) >= max(2, config.max_words_per_caption // 2)
            )
        ):
            flush()
    flush()
    return _merge_overlaps(_merge_tiny(captions, config), config)
