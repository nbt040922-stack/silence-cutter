from __future__ import annotations

from .config import CaptionConfig
from .models import CaptionSegment, TranscriptSegment, WordTimestamp

_SENTENCE_END = (".", "?", "!", "。", "？", "！")
_SOFT_END = (",", ";", ":", "、", "，", "；", "：")
_CLOSE_PUNCTUATION = set(",.;:!?%)]}、。，；：！？）］｝」』】》〉")
_OPEN_PUNCTUATION = set("([{（［｛「『【《〈")
_CJK_PHRASE_ENDINGS = (
    "たので", "かった", "ので", "けれど", "けど", "ながら", "なら",
    "から", "ため", "ものの", "のに", "ても", "では", "には", "とは",
)


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


join_words = _join_words


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


def _soft_line_limit(config: CaptionConfig) -> int:
    return config.max_chars_per_line + max(
        2, min(6, (config.max_chars_per_line + 9) // 10)
    )


def _soft_duration_overrun(config: CaptionConfig) -> float:
    return min(
        config.min_caption_duration,
        max(0.25, config.max_caption_duration * 0.15),
    )


def _caption(
    words: list[WordTimestamp], config: CaptionConfig, *, soft_limits: bool = False
) -> CaptionSegment:
    line_limit = _soft_line_limit(config) if soft_limits else config.max_chars_per_line
    text = balance_lines(
        _join_words(words),
        line_limit,
        config.max_lines,
        [word.text for word in words],
    )
    return CaptionSegment(
        start=words[0].start,
        end=words[-1].end,
        text=text,
        words=list(words),
    )


def _fits(
    words: list[WordTimestamp], config: CaptionConfig, *, soft_limits: bool = False
) -> bool:
    text = _join_words(words)
    extra_words = 1 if soft_limits else 0
    if (
        not _has_cjk(text)
        and len(words) > config.max_words_per_caption + extra_words
    ):
        return False
    longest_token = max(word.end - word.start for word in words)
    duration_limit = max(config.max_caption_duration, longest_token)
    if soft_limits and len(words) > 1:
        duration_limit += _soft_duration_overrun(config)
    if words[-1].end - words[0].start > duration_limit:
        return False
    line_limit = _soft_line_limit(config) if soft_limits else config.max_chars_per_line
    lines = balance_lines(
        text,
        line_limit,
        config.max_lines,
        [word.text for word in words],
    ).splitlines()
    return len(lines) <= config.max_lines and all(
        len(line) <= line_limit for line in lines
    )


def _can_merge(
    left: CaptionSegment,
    right: CaptionSegment,
    config: CaptionConfig,
    *,
    soft_limits: bool = False,
) -> bool:
    gap = right.words[0].start - left.words[-1].end
    return gap <= config.max_gap_between_words and _fits(
        left.words + right.words, config, soft_limits=soft_limits
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
    result = list(captions)
    index = 0
    while index < len(result):
        tiny = result[index]
        if not _is_tiny(tiny, config):
            index += 1
            continue

        candidates: list[tuple[float, int, CaptionSegment]] = []
        if index > 0:
            candidate = _tiny_merge_candidate(
                result[index - 1], tiny, config, tiny_on_right=True
            )
            if candidate:
                candidates.append((candidate[0], index - 1, candidate[1]))
        if index + 1 < len(result):
            candidate = _tiny_merge_candidate(
                tiny, result[index + 1], config, tiny_on_right=False
            )
            if candidate:
                candidates.append((candidate[0], index, candidate[1]))
        if not candidates:
            index += 1
            continue

        _, merge_index, merged = min(candidates, key=lambda item: item[0])
        result[merge_index : merge_index + 2] = [merged]
        index = max(0, merge_index - 1)
    return result


def _tiny_merge_candidate(
    left: CaptionSegment,
    right: CaptionSegment,
    config: CaptionConfig,
    *,
    tiny_on_right: bool,
) -> tuple[float, CaptionSegment] | None:
    if not _can_merge(left, right, config, soft_limits=True):
        return None
    words = left.words + right.words
    merged = _caption(words, config, soft_limits=True)
    gap = max(0.0, right.words[0].start - left.words[-1].end)
    duration_overrun = max(
        0.0, merged.end - merged.start - config.max_caption_duration
    )
    line_overrun = sum(
        max(0, len(line) - config.max_chars_per_line)
        for line in merged.text.splitlines()
    )
    boundary_penalty = 2.0 if left.text.rstrip().endswith(_SENTENCE_END) else 0.0
    natural_tail_bonus = 0.0
    if tiny_on_right:
        tail = right.text.replace("\n", "")
        if tail.endswith(_CJK_PHRASE_ENDINGS):
            natural_tail_bonus = 1.0
        elif tail.endswith(_SENTENCE_END):
            natural_tail_bonus = 0.5
    direction_penalty = 0.05 if not tiny_on_right else 0.0
    score = (
        boundary_penalty
        + gap
        + duration_overrun
        + line_overrun / max(1, config.max_chars_per_line)
        + direction_penalty
        - natural_tail_bonus
    )
    return score, merged


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
