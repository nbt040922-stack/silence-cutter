from __future__ import annotations

import statistics
import unicodedata

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

_JP_CONTINUATION_PREFIXES = (
    "て", "って", "ので", "から", "けど", "けれど", "けれども", "です", "ます",
    "ました", "ない", "なく", "たり", "ながら", "よう", "かな", "ず",
)
_JP_TE_AUXILIARIES = ("いる", "いく", "いた", "みる", "みました")
_JP_HONORIFICS = ("\u541b", "\u3055\u3093", "\u3061\u3083\u3093")
_JP_SMALL_KANA = frozenset("\u3063\u3083\u3085\u3087\u3041\u3043\u3045\u3047\u3049")
_JP_PHRASE_ENDINGS = (
    "ので", "から", "けど", "けれど", "けれども", "と思ってて", "思ってて",
    "です", "ます", "ました", "でした", "ですね", "ますね", "かな",
)

# Positive evidence adds; integrity penalties subtract. Kept together for inspection/tuning.
_BOUNDARY_WEIGHTS = {
    "weak_pause": 0.75,
    "medium_pause": 2.5,
    "strong_pause": 6.0,
    "strong_punctuation": 8.0,
    "medium_punctuation": 3.0,
    "segment_boundary": 2.0,
    "phrase_boundary": 2.5,
    "grammatical_split": 9.0,
    "connected_token": 7.0,
    "orphan_tail": 7.0,
    "lexical_split": 9.0,
}


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


def visible_character_count(text: str) -> int:
    return sum(
        not character.isspace()
        and not unicodedata.category(character).startswith("P")
        for character in text
    )


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
    captions: list[CaptionSegment],
    config: CaptionConfig,
    boundary_diagnostics: dict[tuple[float, float], dict[str, object]] | None = None,
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
                result[index - 1], tiny, config, tiny_on_right=True,
                boundary_diagnostics=(boundary_diagnostics or {}).get(
                    (result[index - 1].words[-1].end, tiny.words[0].start)
                ),
            )
            if candidate:
                candidates.append((candidate[0], index - 1, candidate[1]))
        if index + 1 < len(result):
            candidate = _tiny_merge_candidate(
                tiny, result[index + 1], config, tiny_on_right=False,
                boundary_diagnostics=(boundary_diagnostics or {}).get(
                    (tiny.words[-1].end, result[index + 1].words[0].start)
                ),
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
    boundary_diagnostics: dict[str, object] | None = None,
) -> tuple[float, CaptionSegment] | None:
    tiny = right if tiny_on_right else left
    boundary_gap = max(0.0, right.words[0].start - left.words[-1].end)
    boundary_score = float((boundary_diagnostics or {}).get("score", 0.0))
    components = (boundary_diagnostics or {}).get("components", {})
    capacity_boundary = (
        isinstance(components, dict)
        and float(components.get("visual_capacity", 0.0)) >= 2.5
        and boundary_score >= 4.0
    )
    natural_phrase_boundary = (
        isinstance(components, dict)
        and float(components.get("phrase", 0.0))
        >= _BOUNDARY_WEIGHTS["phrase_boundary"]
        and not components.get("lexical_penalty")
        and not components.get("grammatical_penalty")
    )
    if (
        config.boundary_scoring_enabled
        and config.adaptive_segmentation
        and (
            boundary_score >= config.boundary_preserve_score
            or capacity_boundary
            or natural_phrase_boundary
            or (
                boundary_gap >= config.strong_pause_seconds
                and boundary_score >= _BOUNDARY_WEIGHTS["strong_pause"]
            )
        )
        and tiny.end - tiny.start >= config.absolute_min_caption_duration
        and not _is_orphan_japanese_text(tiny.text)
    ):
        return None
    if not _can_merge(left, right, config, soft_limits=True):
        return None
    words = left.words + right.words
    if (
        config.boundary_scoring_enabled
        and config.adaptive_segmentation
        and not _absolute_boundary_fits(words, config)
    ):
        return None
    merged = _caption(words, config, soft_limits=True)
    if config.adaptive_segmentation:
        duration = merged.end - merged.start
        reading_load = visible_character_count(merged.text) / duration
        tiny_duration = tiny.end - tiny.start
        if (
            reading_load > config.max_reading_cps
            and tiny_duration >= config.absolute_min_caption_duration
        ):
            return None
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


def _local_speech_rates(
    words: list[WordTimestamp], config: CaptionConfig
) -> list[dict[str, float]]:
    if not words:
        return []
    half_window = config.speech_rate_window_seconds / 2
    midpoints = [(word.start + word.end) / 2 for word in words]
    visible = [visible_character_count(word.text) for word in words]
    lexical = [1 if visible[index] and not _has_cjk(word.text) else 0 for index, word in enumerate(words)]
    rates: list[dict[str, float]] = []
    left = 0
    right = 0
    visible_total = 0
    word_total = 0
    for index, midpoint in enumerate(midpoints):
        lower = midpoint - half_window
        upper = midpoint + half_window
        while right < len(words) and midpoints[right] <= upper:
            visible_total += visible[right]
            word_total += lexical[right]
            right += 1
        while left < right and midpoints[left] < lower:
            visible_total -= visible[left]
            word_total -= lexical[left]
            left += 1
        span = max(words[right - 1].end - words[left].start, 0.1)
        rates.append(
            {
                "visible_chars_per_second": visible_total / span,
                "words_per_second": word_total / span,
            }
        )
    return rates


def _adaptive_target_duration(
    cps: float,
    config: CaptionConfig,
    *,
    words_per_second: float = 0.0,
    cjk: bool = True,
) -> float:
    rate = cps if cjk else words_per_second
    reference = (
        config.adaptive_reference_cps
        if cjk
        else config.adaptive_reference_wps
    )
    if rate <= 0:
        return config.adaptive_max_target_duration
    target = (
        config.adaptive_base_target_duration
        * reference
        / rate
    )
    return min(
        config.adaptive_max_target_duration,
        max(config.adaptive_min_target_duration, target),
    )


def _adaptive_group_floor(config: CaptionConfig) -> float:
    return min(
        config.min_caption_duration,
        max(0.5, config.adaptive_min_target_duration * 0.4),
    )


def _adaptive_limits(
    words: list[WordTimestamp],
    rates_by_word: dict[int, dict[str, float]],
    config: CaptionConfig,
) -> tuple[float, int, int, float]:
    rates = [rates_by_word[id(word)] for word in words]
    cps = statistics.fmean(item["visible_chars_per_second"] for item in rates)
    words_per_second = statistics.fmean(
        item["words_per_second"] for item in rates
    )
    target = _adaptive_target_duration(
        cps,
        config,
        words_per_second=words_per_second,
        cjk=_has_cjk(_join_words(words)),
    )
    character_capacity = min(
        config.max_chars_per_line * config.max_lines,
        max(
            config.max_chars_per_line // 2,
            int(round(config.target_reading_cps * target)),
        ),
    )
    word_capacity = min(
        config.max_words_per_caption,
        max(
            4,
            int(
                round(
                    config.max_words_per_caption
                    * target
                    / config.adaptive_max_target_duration
                )
            ),
        ),
    )
    return target, character_capacity, word_capacity, cps


def _absolute_adaptive_fits(
    words: list[WordTimestamp], config: CaptionConfig
) -> bool:
    longest_token = max(word.end - word.start for word in words)
    duration_limit = max(config.absolute_max_caption_duration, longest_token)
    if words[-1].end - words[0].start > duration_limit:
        return False
    lines = balance_lines(
        _join_words(words),
        config.max_chars_per_line,
        config.max_lines,
        [word.text for word in words],
    ).splitlines()
    return len(lines) <= config.max_lines and all(
        len(line) <= config.max_chars_per_line for line in lines
    )


def _adaptive_fits(
    words: list[WordTimestamp],
    rates_by_word: dict[int, dict[str, float]],
    config: CaptionConfig,
) -> bool:
    if not _absolute_adaptive_fits(words, config):
        return False
    target, character_capacity, word_capacity, _ = _adaptive_limits(
        words, rates_by_word, config
    )
    del target
    text = _join_words(words)
    if visible_character_count(text) > character_capacity:
        return False
    return _has_cjk(text) or len(words) <= word_capacity


def _remaining_phrase_duration(
    words: list[WordTimestamp], start_index: int, config: CaptionConfig
) -> float:
    end_index = start_index
    while end_index + 1 < len(words):
        if words[end_index].text.endswith(_SENTENCE_END):
            break
        gap = words[end_index + 1].start - words[end_index].end
        if gap >= config.adaptive_pause_boundary:
            break
        end_index += 1
    return words[end_index].end - words[start_index].start


def _next_words_are_grammatical_tail(
    words: list[WordTimestamp], index: int, config: CaptionConfig
) -> bool:
    tail: list[str] = []
    start = index + 1
    for next_index in range(start, min(len(words), start + 3)):
        if next_index > start:
            gap = words[next_index].start - words[next_index - 1].end
            if gap >= config.adaptive_pause_boundary:
                break
        tail.append(words[next_index].text)
        text = _join_texts(tail)
        if text.endswith(_CJK_PHRASE_ENDINGS):
            return words[next_index].end - words[start].start < 1.0
    return False


def _is_japanese(text: str) -> bool:
    return any(0x3040 <= ord(character) <= 0x30FF for character in text)


def _is_katakana_text(text: str) -> bool:
    characters = [character for character in text if not character.isspace()]
    return bool(characters) and all(
        0x30A0 <= ord(character) <= 0x30FF for character in characters
    )


def _is_kanji_char(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
    )


def _is_hiragana_char(character: str) -> bool:
    return 0x3040 <= ord(character) <= 0x309F


def _is_hiragana_text(text: str) -> bool:
    return bool(text) and all(_is_hiragana_char(character) for character in text)


def _clean_boundary_text(text: str) -> str:
    return text.strip().strip("".join(_CLOSE_PUNCTUATION | _OPEN_PUNCTUATION))


def classify_boundary(
    left: WordTimestamp,
    right: WordTimestamp,
    previous_word: WordTimestamp | None = None,
    next_word: WordTimestamp | None = None,
) -> dict[str, object]:
    del next_word
    left_text = _clean_boundary_text(left.text)
    right_text = _clean_boundary_text(right.text)
    penalty = 0.0
    if left_text and right_text and (
        _is_japanese(left_text + right_text)
        or _is_kanji_char(left_text[-1])
        or _is_kanji_char(right_text[0])
    ):
        left_visible = visible_character_count(left_text)
        right_visible = visible_character_count(right_text)
        if _is_katakana_text(left_text) and _is_katakana_text(right_text):
            penalty = _BOUNDARY_WEIGHTS["lexical_split"]
        elif right_text.startswith(_JP_HONORIFICS) and (
            _is_katakana_text(left_text) or _is_kanji_char(left_text[-1])
        ):
            penalty = _BOUNDARY_WEIGHTS["lexical_split"]
        elif (
            _is_kanji_char(left_text[-1])
            and _is_kanji_char(right_text[0])
            and (left_visible == 1 or right_visible == 1)
        ):
            penalty = _BOUNDARY_WEIGHTS["lexical_split"] * 0.9
        elif (
            _is_kanji_char(left_text[-1])
            and _is_hiragana_char(right_text[0])
            and (left_visible == 1 or right_visible <= 2)
        ):
            penalty = _BOUNDARY_WEIGHTS["lexical_split"] * 0.8
        elif (
            _is_hiragana_text(left_text)
            and _is_hiragana_text(right_text)
            and bool(_JP_SMALL_KANA.intersection(left_text + right_text))
            and (left_visible <= 3 or right_visible <= 3)
        ):
            penalty = _BOUNDARY_WEIGHTS["lexical_split"]
        elif (
            left_visible == 1
            and right_visible <= 2
            and _is_hiragana_text(left_text + right_text)
            and previous_word is not None
        ):
            previous_text = _clean_boundary_text(previous_word.text)
            if previous_text and (
                previous_text[-1].isdigit()
                or _is_kanji_char(previous_text[-1])
            ):
                penalty = _BOUNDARY_WEIGHTS["lexical_split"] * 0.8
        elif (
            left_visible == 1
            and right_visible == 1
            and _is_hiragana_char(left_text[-1])
            and _is_hiragana_char(right_text[0])
        ):
            penalty = _BOUNDARY_WEIGHTS["lexical_split"] * 0.8
    return {
        "classification": (
            "protected" if penalty >= 7.0 else "weak" if penalty else "safe"
        ),
        "lexical_penalty": penalty,
    }


def _japanese_grammatical_penalty(
    left: WordTimestamp, right: WordTimestamp
) -> float:
    left_text = _clean_boundary_text(left.text)
    right_text = _clean_boundary_text(right.text)
    if not _is_japanese(left_text + right_text):
        return 0.0
    if right_text.startswith(_JP_CONTINUATION_PREFIXES):
        return _BOUNDARY_WEIGHTS["grammatical_split"]
    if left_text.endswith(("て", "で")) and right_text.startswith(_JP_TE_AUXILIARIES):
        return _BOUNDARY_WEIGHTS["grammatical_split"]
    if left_text.endswith("けれ") and right_text.startswith("ども"):
        return _BOUNDARY_WEIGHTS["grammatical_split"]
    if left_text and _is_cjk_char(left_text[-1]) and right_text.startswith(("く", "かった")):
        return _BOUNDARY_WEIGHTS["grammatical_split"]
    return 0.0


def _is_orphan_japanese_text(text: str) -> bool:
    compact = _clean_boundary_text(text.replace("\n", ""))
    return (
        _is_japanese(compact)
        and visible_character_count(compact) <= 6
        and (
            compact.startswith(_JP_CONTINUATION_PREFIXES)
            or compact in _CJK_PHRASE_ENDINGS
            or compact in ("かった", "って", "ので", "から")
        )
    )


def _orphan_tail_penalty(words: list[WordTimestamp], boundary: int) -> float:
    if boundary >= len(words):
        return 0.0
    tail: list[WordTimestamp] = []
    for index in range(boundary, min(len(words), boundary + 3)):
        if tail and words[index].start - tail[-1].end >= 0.45:
            break
        tail.append(words[index])
        if words[index].text.endswith(_SENTENCE_END):
            break
    return (
        _BOUNDARY_WEIGHTS["orphan_tail"]
        if _is_orphan_japanese_text(_join_words(tail))
        else 0.0
    )


def _pause_score(gap: float, config: CaptionConfig) -> float:
    if gap >= config.strong_pause_seconds:
        return _BOUNDARY_WEIGHTS["strong_pause"] + min(
            2.0, gap - config.strong_pause_seconds
        )
    if gap >= config.medium_pause_seconds:
        return _BOUNDARY_WEIGHTS["medium_pause"]
    if gap >= config.weak_pause_seconds:
        return _BOUNDARY_WEIGHTS["weak_pause"]
    return 0.0


def _punctuation_score(text: str) -> float:
    if text.rstrip().endswith(_SENTENCE_END):
        return _BOUNDARY_WEIGHTS["strong_punctuation"]
    if text.rstrip().endswith(_SOFT_END):
        return _BOUNDARY_WEIGHTS["medium_punctuation"]
    return 0.0


def _visual_capacity_score(text: str, config: CaptionConfig) -> float:
    visible = visible_character_count(text)
    if _has_cjk(text):
        pressure_start = max(8, int(round(config.preferred_cjk_chars * 0.5)))
        if visible <= pressure_start:
            return 0.0
        if visible <= config.preferred_cjk_chars:
            return 1.5 * (
                (visible - pressure_start)
                / max(1, config.preferred_cjk_chars - pressure_start)
            )
        if visible <= config.soft_max_cjk_chars:
            return 1.5 + 1.5 * (
                (visible - config.preferred_cjk_chars)
                / max(1, config.soft_max_cjk_chars - config.preferred_cjk_chars)
            )
        return 3.0 + 3.0 * (
            (visible - config.soft_max_cjk_chars)
            / max(1, config.absolute_max_cjk_chars - config.soft_max_cjk_chars)
        )
    capacity = config.max_chars_per_line * config.max_lines
    return max(0.0, 4.0 * (visible / capacity - 0.65))


def _duration_pressure(
    duration: float, target: float, config: CaptionConfig
) -> float:
    absolute_floor = min(
        config.absolute_min_caption_duration, config.min_caption_duration
    )
    if duration < absolute_floor:
        return -4.0
    if duration < config.min_caption_duration:
        return -2.0 * (config.min_caption_duration - duration) / max(
            0.01, config.min_caption_duration - absolute_floor
        )
    if duration < target * 0.7:
        return 0.0
    return min(
        5.0,
        1.0
        + 4.0
        * (duration - target * 0.7)
        / max(0.1, config.absolute_max_caption_duration - target * 0.7),
    )


def _boundary_score(
    words: list[WordTimestamp],
    start: int,
    boundary: int,
    rates_by_word: dict[int, dict[str, float]],
    segment_by_word: dict[int, int],
    config: CaptionConfig,
) -> dict[str, object]:
    caption_words = words[start:boundary]
    left = words[boundary - 1]
    right = words[boundary] if boundary < len(words) else None
    text = _join_words(caption_words)
    duration = left.end - caption_words[0].start
    target, character_capacity, word_capacity, _ = _adaptive_limits(
        caption_words, rates_by_word, config
    )
    gap = max(0.0, right.start - left.end) if right else 0.0
    pause = _pause_score(gap, config)
    punctuation = _punctuation_score(left.text)
    segment_boundary = (
        _BOUNDARY_WEIGHTS["segment_boundary"]
        if right and segment_by_word[id(left)] != segment_by_word[id(right)]
        else 0.0
    )
    phrase = (
        _BOUNDARY_WEIGHTS["phrase_boundary"]
        if _is_japanese(text)
        and _clean_boundary_text(text).endswith(_JP_PHRASE_ENDINGS)
        and (
            visible_character_count(text) >= 8
            or gap >= config.weak_pause_seconds
        )
        else 0.0
    )
    visual = _visual_capacity_score(text, config)
    size_overrun = 0.0
    if not _has_cjk(text):
        if len(caption_words) >= word_capacity:
            visual += 2.5
        size_overrun = max(0, len(caption_words) - word_capacity) * 1.0
    else:
        adaptive_cjk_capacity = min(
            config.preferred_cjk_chars, character_capacity
        )
        if visible_character_count(text) >= adaptive_cjk_capacity:
            visual += 2.5
        size_overrun = max(
            0, visible_character_count(text) - adaptive_cjk_capacity
        ) * 0.75
    duration_score = _duration_pressure(duration, target, config)
    reading_load = visible_character_count(text) / max(duration, 0.01)
    reading = max(
        0.0,
        min(3.0, 3.0 * (reading_load - config.target_reading_cps) / config.target_reading_cps),
    )
    grammatical = _japanese_grammatical_penalty(left, right) if right else 0.0
    lexical = (
        float(classify_boundary(
            left,
            right,
            words[boundary - 2] if boundary - 2 >= 0 else None,
            words[boundary + 1] if boundary + 1 < len(words) else None,
        )["lexical_penalty"])
        if right
        else 0.0
    )
    if right and right.space_before is False and not _has_cjk(left.text + right.text):
        grammatical = max(grammatical, _BOUNDARY_WEIGHTS["connected_token"])
    orphan = _orphan_tail_penalty(words, boundary)
    components = {
        "pause": pause,
        "punctuation": punctuation,
        "segment_boundary": segment_boundary,
        "phrase": phrase,
        "visual_capacity": visual,
        "duration_pressure": duration_score,
        "reading_load": reading,
        "grammatical_penalty": grammatical,
        "lexical_penalty": lexical,
        "orphan_penalty": orphan,
        "size_overrun_penalty": size_overrun,
    }
    boundary_quality = (
        pause + punctuation + segment_boundary + phrase
        - grammatical - lexical - orphan
    )
    split_urgency = visual + duration_score + reading - size_overrun
    score = boundary_quality + split_urgency * 0.35
    return {
        "time": left.end,
        "score": score,
        "gap": gap,
        "caption_duration": duration,
        "visible_characters": visible_character_count(text),
        "adaptive_target_duration": target,
        "boundary_quality": boundary_quality,
        "split_urgency": split_urgency,
        "lexical_penalty": lexical,
        "forced": False,
        "components": components,
    }


def _absolute_boundary_fits(
    words: list[WordTimestamp], config: CaptionConfig
) -> bool:
    if len(words) == 1:
        return True
    text = _join_words(words)
    if words[-1].end - words[0].start > config.absolute_max_caption_duration:
        return False
    if _has_cjk(text) and visible_character_count(text) > config.absolute_max_cjk_chars:
        return False
    if not _has_cjk(text) and len(words) > config.max_words_per_caption:
        return False
    lines = balance_lines(
        text, config.max_chars_per_line, config.max_lines, [word.text for word in words]
    ).splitlines()
    return len(lines) <= config.max_lines and all(
        len(line) <= config.max_chars_per_line for line in lines
    )


def _choose_boundary(
    words: list[WordTimestamp],
    start: int,
    rates_by_word: dict[int, dict[str, float]],
    segment_by_word: dict[int, int],
    config: CaptionConfig,
    protected_boundaries: dict[str, set[tuple[float, float]]],
) -> tuple[int, dict[str, object]]:
    candidates: list[tuple[int, dict[str, object]]] = []
    max_tokens = config.boundary_lookahead_tokens * 2
    for boundary in range(start + 1, min(len(words), start + max_tokens) + 1):
        candidate_words = words[start:boundary]
        if not _absolute_boundary_fits(candidate_words, config):
            break
        diagnostics = _boundary_score(
            words, start, boundary, rates_by_word, segment_by_word, config
        )
        components = diagnostics["components"]
        boundary_key = (
            words[boundary - 1].end,
            words[boundary].start if boundary < len(words) else words[boundary - 1].end,
        )
        if components["lexical_penalty"]:
            protected_boundaries["lexical"].add(boundary_key)
        if components["grammatical_penalty"]:
            protected_boundaries["grammatical"].add(boundary_key)
        strong_natural_boundary = (
            components["punctuation"] >= _BOUNDARY_WEIGHTS["strong_punctuation"]
            or components["pause"] >= _BOUNDARY_WEIGHTS["strong_pause"]
        )
        below_absolute_floor = diagnostics["caption_duration"] < min(
            config.absolute_min_caption_duration, config.min_caption_duration
        )
        if not below_absolute_floor or strong_natural_boundary or boundary == len(words):
            candidates.append((boundary, diagnostics))
        if (
            strong_natural_boundary
            and diagnostics["caption_duration"] >= min(
                config.absolute_min_caption_duration, config.min_caption_duration
            )
            and not components["grammatical_penalty"]
            and not components["lexical_penalty"]
        ):
            break
        if boundary - start >= config.boundary_lookahead_tokens and (
            diagnostics["visible_characters"] >= config.soft_max_cjk_chars
            or diagnostics["caption_duration"]
            >= diagnostics["adaptive_target_duration"] * 1.2
        ) and any(
            not candidate[1]["components"]["lexical_penalty"]
            and not candidate[1]["components"]["grammatical_penalty"]
            and not candidate[1]["components"]["orphan_penalty"]
            for candidate in candidates
        ):
            break
    if not candidates:
        boundary = min(start + 1, len(words))
        diagnostics = _boundary_score(
            words, start, boundary, rates_by_word, segment_by_word, config
        )
        diagnostics["forced"] = boundary < len(words)
        return boundary, diagnostics
    safe = [
        item for item in candidates
        if not item[1]["components"]["lexical_penalty"]
        and not item[1]["components"]["grammatical_penalty"]
        and not item[1]["components"]["orphan_penalty"]
    ]
    forced = not safe
    pool = safe or candidates
    if forced:
        selected = min(
            pool,
            key=lambda item: (
                item[1]["components"]["lexical_penalty"]
                + item[1]["components"]["grammatical_penalty"]
                + item[1]["components"]["orphan_penalty"],
                abs(item[1]["split_urgency"] - 2.0),
                -item[1]["caption_duration"],
            ),
        )
        selected[1]["forced"] = True
        return selected
    natural = [item for item in pool if item[1]["boundary_quality"] > 0]
    urgent = [item for item in pool if item[1]["split_urgency"] >= 2.0]
    if natural and urgent:
        first_urgent = urgent[0]
        nearby_natural = [
            item for item in natural
            if item[0] <= first_urgent[0] + 4
            and item[1]["caption_duration"]
            <= first_urgent[1]["caption_duration"] + 0.7
        ]
    else:
        nearby_natural = natural
    if nearby_natural:
        selected = max(
            nearby_natural,
            key=lambda item: (
                item[1]["boundary_quality"],
                -abs(item[1]["split_urgency"] - 1.5),
                -item[1]["caption_duration"],
            ),
        )
    else:
        selected = (
            min(urgent, key=lambda item: item[0])
            if urgent
            else max(pool, key=lambda item: (item[1]["split_urgency"], item[0]))
        )
    selected[1]["forced"] = False
    return selected


def _segment_with_boundary_scoring(
    words: list[WordTimestamp],
    rates_by_word: dict[int, dict[str, float]],
    segment_by_word: dict[int, int],
    config: CaptionConfig,
) -> tuple[
    list[CaptionSegment], list[dict[str, object]], dict[str, int]
]:
    captions: list[CaptionSegment] = []
    boundaries: list[dict[str, object]] = []
    protected_boundaries: dict[str, set[tuple[float, float]]] = {
        "lexical": set(),
        "grammatical": set(),
    }
    start = 0
    while start < len(words):
        boundary, diagnostics = _choose_boundary(
            words, start, rates_by_word, segment_by_word, config,
            protected_boundaries,
        )
        captions.append(_caption(words[start:boundary], config))
        if boundary < len(words):
            boundaries.append(diagnostics)
        start = boundary
    return captions, boundaries, {
        name: len(items) for name, items in protected_boundaries.items()
    }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _segmentation_diagnostics(
    captions: list[CaptionSegment],
    rates_by_word: dict[int, dict[str, float]],
    config: CaptionConfig,
    boundary_diagnostics: list[dict[str, object]] | None = None,
    protected_boundary_counts: dict[str, int] | None = None,
) -> dict[str, object]:
    boundary_diagnostics = boundary_diagnostics or []
    protected_boundary_counts = protected_boundary_counts or {}
    details: list[dict[str, float]] = []
    for caption in captions:
        duration = caption.end - caption.start
        visible = visible_character_count(caption.text)
        rates = [rates_by_word[id(word)] for word in caption.words]
        speech_rate = statistics.fmean(
            item["visible_chars_per_second"] for item in rates
        )
        words_per_second = statistics.fmean(
            item["words_per_second"] for item in rates
        )
        details.append(
            {
                "start": caption.start,
                "end": caption.end,
                "speech_rate": speech_rate,
                "visible_chars_per_second": speech_rate,
                "words_per_second": words_per_second,
                "caption_reading_load": visible / duration if duration else 0.0,
                "adaptive_target_duration": _adaptive_target_duration(
                    speech_rate,
                    config,
                    words_per_second=words_per_second,
                    cjk=_has_cjk(caption.text),
                ),
                "visible_characters": float(visible),
            }
        )
    durations = [item.end - item.start for item in captions]
    visible_counts = [visible_character_count(item.text) for item in captions]
    reading_loads = [item["caption_reading_load"] for item in details]
    all_rates = [item["visible_chars_per_second"] for item in rates_by_word.values()]
    all_word_rates = [item["words_per_second"] for item in rates_by_word.values()]
    return {
        "adaptive_segmentation": config.adaptive_segmentation,
        "average_speech_rate": statistics.fmean(all_rates) if all_rates else 0.0,
        "average_words_per_second": (
            statistics.fmean(all_word_rates) if all_word_rates else 0.0
        ),
        "median_caption_duration": statistics.median(durations) if durations else 0.0,
        "p10_caption_duration": _percentile(durations, 0.10),
        "p50_caption_duration": _percentile(durations, 0.50),
        "p90_caption_duration": _percentile(durations, 0.90),
        "average_visible_characters_per_caption": (
            statistics.fmean(visible_counts) if visible_counts else 0.0
        ),
        "average_reading_load": (
            statistics.fmean(reading_loads) if reading_loads else 0.0
        ),
        "maximum_reading_load": max(reading_loads, default=0.0),
        "captions": details,
        "forced_boundary_count": sum(
            bool(boundary.get("forced")) for boundary in boundary_diagnostics
        ),
        "lexical_protected_boundary_count": protected_boundary_counts.get(
            "lexical", 0
        ),
        "grammatical_protected_boundary_count": protected_boundary_counts.get(
            "grammatical", 0
        ),
        "selected_boundaries": boundary_diagnostics,
    }


def segment_transcript(
    segments: list[TranscriptSegment], config: CaptionConfig
) -> list[CaptionSegment]:
    captions, _ = segment_transcript_with_diagnostics(segments, config)
    return captions


def segment_transcript_with_diagnostics(
    segments: list[TranscriptSegment], config: CaptionConfig
) -> tuple[list[CaptionSegment], dict[str, object]]:
    segment_by_word = {
        id(word): segment_index
        for segment_index, segment in enumerate(segments)
        for word in segment.words
        if word.text
    }
    words = sorted(
        (word for segment in segments for word in segment.words if word.text),
        key=lambda word: (word.start, word.end),
    )
    if not words:
        return [], _segmentation_diagnostics([], {}, config)

    rates = _local_speech_rates(words, config)
    rates_by_word = {id(word): rate for word, rate in zip(words, rates)}

    if config.boundary_scoring_enabled and config.adaptive_segmentation:
        captions, boundary_diagnostics, protected_boundary_counts = (
            _segment_with_boundary_scoring(
                words, rates_by_word, segment_by_word, config
            )
        )
        boundaries_by_pair = {
            (caption.words[-1].end, captions[index + 1].words[0].start):
                boundary_diagnostics[index]
            for index, caption in enumerate(captions[:-1])
        }
        captions = _merge_overlaps(
            _merge_tiny(captions, config, boundaries_by_pair), config
        )
        final_boundary_diagnostics = [
            boundaries_by_pair[
                (caption.words[-1].end, captions[index + 1].words[0].start)
            ]
            for index, caption in enumerate(captions[:-1])
        ]
        return captions, _segmentation_diagnostics(
            captions,
            rates_by_word,
            config,
            final_boundary_diagnostics,
            protected_boundary_counts,
        )

    captions: list[CaptionSegment] = []
    current: list[WordTimestamp] = []

    def flush() -> None:
        if current:
            captions.append(_caption(current, config))
            current.clear()

    for index, word in enumerate(words):
        gap = word.start - current[-1].end if current else 0.0
        pause_limit = (
            config.adaptive_pause_boundary
            if config.adaptive_segmentation
            else config.max_gap_between_words
        )
        if current and gap >= pause_limit:
            flush()
        if current:
            candidate = current + [word]
            if config.adaptive_segmentation:
                absolute_fits = _absolute_adaptive_fits(candidate, config)
                adaptive_fits = _adaptive_fits(candidate, rates_by_word, config)
                current_duration = current[-1].end - current[0].start
                remaining_duration = _remaining_phrase_duration(words, index, config)
                group_floor = _adaptive_group_floor(config)
                if not absolute_fits or (
                    not adaptive_fits
                    and current_duration >= group_floor
                    and remaining_duration >= group_floor
                ):
                    flush()
            elif not _fits(candidate, config):
                flush()
        current.append(word)
        text = _join_words(current)
        if word.text.endswith(_SENTENCE_END):
            flush()
        elif word.text.endswith(_SOFT_END) and (
            config.adaptive_segmentation
            and current[-1].end - current[0].start >= config.min_caption_duration
            or not config.adaptive_segmentation
            and (
                len(text) >= config.max_chars_per_line
                or (
                    not _has_cjk(text)
                    and len(current) >= max(2, config.max_words_per_caption // 2)
                )
            )
        ):
            flush()
        elif config.adaptive_segmentation and current:
            target, character_capacity, word_capacity, _ = _adaptive_limits(
                current, rates_by_word, config
            )
            duration = current[-1].end - current[0].start
            reached_target = (
                duration >= target
                or visible_character_count(text) >= character_capacity
                or (not _has_cjk(text) and len(current) >= word_capacity)
            )
            remaining_duration = (
                _remaining_phrase_duration(words, index + 1, config)
                if index + 1 < len(words)
                else 0.0
            )
            if (
                reached_target
                and duration >= _adaptive_group_floor(config)
                and remaining_duration >= _adaptive_group_floor(config)
                and not _next_words_are_grammatical_tail(words, index, config)
            ):
                flush()
    flush()
    captions = _merge_overlaps(_merge_tiny(captions, config), config)
    return captions, _segmentation_diagnostics(captions, rates_by_word, config)
