from __future__ import annotations

import json
import itertools
import math
import re
import subprocess
import unicodedata
from pathlib import Path
from typing import Any

from PIL import ImageFont

from silence_cutter.runtime_paths import find_executable


ROOT = Path(__file__).resolve().parent
FONT_DIR = ROOT / "assets" / "fonts"
CANVAS = {"width": 1080, "height": 1920, "background": "#000000"}
TITLE_BANNER = {
    "y": 100, "max_width": 918, "horizontal_padding": 36,
    "vertical_padding": 24, "radius": 36,
}
VIDEO_PLACEMENT = {"x": 0, "width": 1080, "height": 810}
PART_BANNER = {
    "max_width": 918, "horizontal_padding": 42,
    "vertical_padding": 20, "radius": 30,
}
TITLE_VIDEO_GAP = 52
VIDEO_PART_GAP = 52
DURATION_POLICY = {
    "preferred_min": 180.0, "preferred_max": 360.0,
    "soft_overrun_max": 420.0,
}

PART_LABELS = {
    "en": "PART {number}", "ja": "パート{number}", "ko": "파트 {number}",
    "vi": "PHẦN {number}", "es": "PARTE {number}", "pt": "PARTE {number}",
    "fr": "PARTIE {number}", "de": "TEIL {number}", "it": "PARTE {number}",
    "zh-Hans": "第{number}部分", "zh-Hant": "第{number}部分",
    "unknown": "PART {number}",
}

LATIN_LANGUAGE_MARKERS = {
    "en": {"and", "family", "shopping", "the", "this", "vlog", "with"},
    "vi": {"của", "đi", "một", "những", "phần", "và", "với"},
    "es": {"compras", "con", "el", "españa", "por", "viaje", "y"},
    "pt": {"compras", "com", "e", "para", "portugal", "viagem"},
    "fr": {"avec", "des", "et", "la", "le", "voyage"},
    "de": {"der", "die", "einkauf", "mit", "reise", "und"},
    "it": {"acquisti", "con", "e", "il", "viaggio"},
}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def probe_video_geometry(path: Path) -> tuple[int, int]:
    ffprobe = find_executable("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe was not found")
    completed = subprocess.run(
        [
            ffprobe, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "json", str(path),
        ],
        check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    stream = json.loads(completed.stdout)["streams"][0]
    return int(stream["width"]), int(stream["height"])


def center_crop_geometry(width: int, height: int) -> dict[str, int | str]:
    if width <= 0 or height <= 0:
        raise ValueError("video dimensions must be positive")
    crop_width = min(width, math.floor(height * 4 / 3))
    crop_height = min(height, math.floor(width * 3 / 4))
    crop_width -= crop_width % 2
    crop_height -= crop_height % 2
    return {
        "source_width": width,
        "source_height": height,
        "x": (width - crop_width) // 2,
        "y": (height - crop_height) // 2,
        "width": crop_width,
        "height": crop_height,
        "aspect_ratio": "4:3",
    }


def _contains_cjk(text: str) -> bool:
    return any(
        "\u3040" <= character <= "\u30ff"
        or "\u3400" <= character <= "\u9fff"
        or "\uac00" <= character <= "\ud7af"
        for character in text
    )


def detect_title_language(title: str) -> tuple[str, float]:
    text = unicodedata.normalize("NFKC", title).lower()
    if any("\uac00" <= character <= "\ud7af" for character in text):
        return "ko", 0.99
    if any("\u3040" <= character <= "\u30ff" for character in text):
        return "ja", 0.99
    if any("\u3400" <= character <= "\u9fff" for character in text):
        traditional = any(character in "這個們來時後裡與為買車發過購記錄" for character in text)
        return ("zh-Hant" if traditional else "zh-Hans"), 0.92
    words = set(re.findall(r"[^\W\d_]+", text, flags=re.UNICODE))
    scores = {
        language: len(words & markers)
        for language, markers in LATIN_LANGUAGE_MARKERS.items()
    }
    if any(character in text for character in "ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệịỉĩọỏốồổỗộớờởỡợụủũứừửữựỳỵỷỹ"):
        scores["vi"] += 3
    best = max(scores, key=scores.get)
    if scores[best] == 0 or list(scores.values()).count(scores[best]) > 1:
        return "unknown", 0.0
    return best, min(0.95, 0.65 + scores[best] * 0.1)


def _font_for_language(language: str) -> tuple[Path, str, bool]:
    if language == "ja":
        return FONT_DIR / "NotoSansJP-Variable.ttf", "Noto Sans JP Bold", True
    if language == "ko":
        return FONT_DIR / "NotoSansKR-Variable.ttf", "Noto Sans KR Bold", True
    if language == "zh-Hant":
        return FONT_DIR / "NotoSansTC-Variable.ttf", "Noto Sans TC Bold", True
    if language == "zh-Hans":
        return FONT_DIR / "NotoSansSC-Variable.ttf", "Noto Sans SC Bold", True
    return FONT_DIR / "Poppins-SemiBold.ttf", "Poppins SemiBold", False


def _load_font(path: Path, size: int, *, bold_variable: bool = False):
    font = ImageFont.truetype(str(path), size=size)
    if bold_variable:
        try:
            font.set_variation_by_name("Bold")
        except (OSError, ValueError):
            pass
    return font


def _is_emoji(character: str) -> bool:
    value = ord(character)
    return 0x1F000 <= value <= 0x1FAFF or 0x2600 <= value <= 0x27BF


def _text_width(font, text: str, emoji_font=None) -> int:
    if emoji_font is None or not any(_is_emoji(character) for character in text):
        return math.ceil(font.getlength(text))
    return math.ceil(sum(
        (emoji_font if _is_emoji(character) else font).getlength(character)
        for character in text
    ))


def _wrap_title(text: str, font, width: int, emoji_font=None) -> list[str]:
    if _contains_cjk(text):
        tokens, separator = list(text), ""
    else:
        tokens, separator = text.split(), " "
    lines: list[str] = []
    current = ""
    for token in tokens:
        candidate = token if not current else current + separator + token
        if current and _text_width(font, candidate, emoji_font) > width:
            lines.append(current)
            current = token
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def fit_title(title: str, banner: dict[str, int]) -> dict[str, Any]:
    if not title.strip():
        raise ValueError("source title must not be empty")
    language, confidence = detect_title_language(title)
    font_path, family, variable = _font_for_language(language)
    text_width = banner["max_width"] - banner["horizontal_padding"] * 2
    chosen: tuple[int, Any, list[str], int, int] | None = None
    for pixels in (62, 56, 51, 46):
        font = _load_font(font_path, pixels, bold_variable=variable)
        emoji_font = _load_font(FONT_DIR / "NotoEmoji-Variable.ttf", pixels, bold_variable=True)
        lines = _wrap_title(title.strip(), font, text_width, emoji_font)
        line_height = math.ceil(font.getbbox("Ag")[3] - font.getbbox("Ag")[1]) + 10
        measured = max((_text_width(font, line, emoji_font) for line in lines), default=0)
        if len(lines) <= 3 and measured <= text_width:
            chosen = pixels, font, lines, measured, line_height
            break
    if chosen is None:
        raise ValueError("source title cannot fit safely within three lines")
    pixels, _font, lines, measured, line_height = chosen
    logical_size = {62: 12, 56: 11, 51: 10, 46: 9}[pixels]
    return {
        "source_title": title,
        "language": language,
        "language_confidence": confidence,
        "selected_font": family + " + Noto Emoji",
        "font_file": f"assets/fonts/{font_path.name}",
        "emoji_font_file": "assets/fonts/NotoEmoji-Variable.ttf",
        "size": logical_size,
        "rendered_size_px": pixels,
        "wrapped_lines": lines,
        "measured_width": measured,
        "line_height": line_height,
        "max_lines": 3,
        "safe_text_width": text_width,
    }


def _banner_geometry(measured_width: int, text_height: int, style: dict[str, int], y: int) -> dict[str, int]:
    width = min(style["max_width"], measured_width + style["horizontal_padding"] * 2)
    height = text_height + style["vertical_padding"] * 2
    return {
        "x": (CANVAS["width"] - width) // 2, "y": y,
        "width": width, "height": height, "radius": style["radius"],
        "horizontal_padding": style["horizontal_padding"],
        "vertical_padding": style["vertical_padding"],
    }


def build_layout(title: dict[str, Any], part_label: str) -> dict[str, Any]:
    title_banner = _banner_geometry(
        title["measured_width"], len(title["wrapped_lines"]) * title["line_height"],
        TITLE_BANNER, 0,
    )
    language = title["language"]
    part_path, part_family, part_variable = _font_for_language(language)
    part_font = _load_font(part_path, 72, bold_variable=part_variable)
    part_bbox = part_font.getbbox(part_label)
    part_width = math.ceil(part_font.getlength(part_label))
    part_height = part_bbox[3] - part_bbox[1]
    part_banner = _banner_geometry(
        part_width, part_height, PART_BANNER, 0,
    )
    content_block_height = (
        title_banner["height"] + TITLE_VIDEO_GAP + VIDEO_PLACEMENT["height"]
        + VIDEO_PART_GAP + part_banner["height"]
    )
    content_block_y = (CANVAS["height"] - content_block_height) // 2
    title_banner["y"] = content_block_y
    video = dict(VIDEO_PLACEMENT)
    video["y"] = title_banner["y"] + title_banner["height"] + TITLE_VIDEO_GAP
    part_banner["y"] = video["y"] + video["height"] + VIDEO_PART_GAP
    return {
        "canvas": CANVAS,
        "video_placement": video,
        "title_banner_geometry": title_banner,
        "part_banner_geometry": part_banner,
        "part_label_font": {
            "font_file": f"assets/fonts/{part_path.name}",
            "selected_font": part_family,
            "rendered_size_px": 72,
            "bold_variable": part_variable,
            "bbox_top": part_bbox[1],
            "measured_width": part_width,
            "measured_height": part_height,
        },
        "content_block": {
            "y": content_block_y,
            "height": content_block_height,
            "title_to_video_gap": TITLE_VIDEO_GAP,
            "video_to_part_gap": VIDEO_PART_GAP,
        },
        "stretch": False,
    }


def _outside_soft_range(duration: float) -> float:
    if duration < 180:
        return (180 - duration) * 2
    if duration <= 360:
        return 0.0
    if duration <= 420:
        return (duration - 360) * 0.15
    if duration > 420:
        return 9 + (duration - 420) * 1.25
    return 0.0


def formatter_status(clean_duration: float, format_anyway: bool = False) -> str:
    if clean_duration <= 0:
        raise ValueError("clean video duration must be positive")
    return "PLANNED"


def _source_at(clean_time: float, segments: list[dict[str, float]]) -> float | None:
    for segment in segments:
        if segment["output_start"] <= clean_time <= segment["output_end"]:
            return segment["source_start"] + clean_time - segment["output_start"]
    return None


def _clean_at(source_time: float, segments: list[dict[str, float]]) -> float | None:
    for segment in segments:
        if segment["source_start"] <= source_time <= segment["source_end"]:
            return segment["output_start"] + source_time - segment["source_start"]
    return None


def clean_mapping(keep_intervals: list[dict[str, float]]) -> list[dict[str, float]]:
    cursor = 0.0
    mapping = []
    for interval in keep_intervals:
        start, end = float(interval["start"]), float(interval["end"])
        duration = end - start
        if duration <= 0:
            continue
        mapping.append({
            "output_start": cursor, "output_end": cursor + duration,
            "source_start": start, "source_end": end,
        })
        cursor += duration
    return mapping


def _junction_candidates(
    clean_duration: float, segments: list[dict[str, float]], part_count: int,
) -> list[dict[str, Any]]:
    targets = tuple(clean_duration * index / part_count for index in range(1, part_count))
    candidates = []
    for index, (left, right) in enumerate(zip(segments, segments[1:]), start=1):
        clean_time = float(left["output_end"])
        pause = max(0.0, float(right["source_start"]) - float(left["source_end"]))
        natural_score = min(pause, 10.0) * 8 + 16
        placement_penalty = min(abs(clean_time - target) for target in targets) / 30
        candidates.append({
            "id": f"junction_{index}",
            "type": "edit_junction",
            "clean_timestamp": clean_time,
            "mapped_source_location": {
                "previous_keep_end": float(left["source_end"]),
                "next_keep_start": float(right["source_start"]),
            },
            "original_silence_duration": pause,
            "speech_ended_cleanly": pause > 0,
            "speech_resumed_cleanly": pause > 0,
            "score": round(natural_score - placement_penalty, 6),
            "natural_score": natural_score,
            "selected": False,
            "reason": "candidate edit junction",
        })
    return candidates


def _fallback_candidates(
    clean_duration: float, segments: list[dict[str, float]], part_count: int,
) -> list[dict[str, Any]]:
    candidates = []
    for index in range(1, part_count):
        clean_time = clean_duration * index / part_count
        candidates.append({
            "id": f"fallback_{index}",
            "type": "fallback_frame_boundary",
            "clean_timestamp": clean_time,
            "mapped_source_location": _source_at(clean_time, segments),
            "original_silence_duration": 0.0,
            "speech_ended_cleanly": False,
            "speech_resumed_cleanly": False,
            "score": -20.0,
            "natural_score": -20.0,
            "selected": False,
            "reason": "fallback candidate; used only when no viable edit junction exists",
        })
    return candidates


def _speech_pause_candidates(
    clean_duration: float,
    segments: list[dict[str, float]],
    speech_intervals: list[dict[str, float]],
    part_count: int,
) -> list[dict[str, Any]]:
    """Map already-detected speech gaps into the clean timeline."""
    targets = tuple(clean_duration * index / part_count for index in range(1, part_count))
    intervals = sorted(speech_intervals, key=lambda item: item["start"])
    candidates = []
    for index, (left, right) in enumerate(zip(intervals, intervals[1:]), start=1):
        pause = float(right["start"]) - float(left["end"])
        if pause < 0.04:
            continue
        source_time = (float(left["end"]) + float(right["start"])) / 2
        clean_time = _clean_at(source_time, segments)
        if clean_time is None or not 0 < clean_time < clean_duration:
            continue
        natural_score = min(pause, 0.5) * 20 + 4
        placement_penalty = min(abs(clean_time - target) for target in targets) / 30
        candidates.append({
            "id": f"speech_pause_{index}",
            "type": "detected_speech_pause",
            "clean_timestamp": clean_time,
            "mapped_source_location": {
                "previous_speech_end": float(left["end"]),
                "next_speech_start": float(right["start"]),
            },
            "original_silence_duration": pause,
            "speech_ended_cleanly": True,
            "speech_resumed_cleanly": True,
            "score": round(natural_score - placement_penalty, 6),
            "natural_score": natural_score,
            "selected": False,
            "reason": "candidate pause from existing fused speech timeline",
        })
    return candidates


def plan_parts(
    clean_duration: float,
    render_segments: list[dict[str, float]],
    speech_intervals: list[dict[str, float]] | None = None,
    part_count: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if clean_duration <= 0:
        raise ValueError("clean video duration must be positive")
    part_count = part_count or (2 if clean_duration < 600 else 3)
    if part_count not in {2, 3}:
        raise ValueError("formatter part count must be 2 or 3")
    segments = sorted(render_segments, key=lambda item: item["output_start"])
    candidates = _junction_candidates(clean_duration, segments, part_count)
    if speech_intervals:
        candidates.extend(
            _speech_pause_candidates(
                clean_duration, segments, speech_intervals, part_count
            )
        )
    candidates.extend(_fallback_candidates(clean_duration, segments, part_count))
    candidates.sort(key=lambda item: item["clean_timestamp"])
    ideal = clean_duration / part_count
    best: tuple[float, tuple[int, ...]] | None = None
    for indexes in itertools.combinations(range(len(candidates)), part_count - 1):
        times = [float(candidates[index]["clean_timestamp"]) for index in indexes]
        if not all(0 < value < clean_duration for value in times):
            continue
        boundaries = [0.0, *times, float(clean_duration)]
        durations = [right - left for left, right in zip(boundaries, boundaries[1:])]
        if any(duration <= 0 for duration in durations):
            continue
        score = (
            sum(float(candidates[index]["natural_score"]) for index in indexes)
            - sum(_outside_soft_range(value) for value in durations)
            - sum(abs(value - ideal) for value in durations) / 60
        )
        if best is None or score > best[0]:
            best = score, indexes
    if best is None:
        raise ValueError(f"could not create {part_count} ordered parts")
    _, selected_indexes = best
    selected = {
        candidate_index: boundary_index
        for boundary_index, candidate_index in enumerate(selected_indexes, start=1)
    }
    for index, candidate in enumerate(candidates):
        if index in selected:
            candidate["selected"] = True
            candidate["reason"] = (
                f"selected boundary {selected[index]}: "
                + (
                    "natural edit junction and best joint duration score"
                    if candidate["type"] == "edit_junction"
                    else (
                        "existing fused-speech pause and best joint duration score"
                        if candidate["type"] == "detected_speech_pause"
                        else "no viable speech pause near this required split region"
                    )
                )
            )
        else:
            candidate["reason"] = "rejected: lower joint natural-boundary/duration score"
        candidate.pop("natural_score", None)
    boundaries = [0.0] + [
        float(candidates[index]["clean_timestamp"]) for index in selected_indexes
    ] + [float(clean_duration)]
    parts = [
        {
            "index": index + 1,
            "label": f"PART {index + 1}",
            "clean_start": boundaries[index],
            "clean_end": boundaries[index + 1],
            "duration": boundaries[index + 1] - boundaries[index],
        }
        for index in range(part_count)
    ]
    return parts, candidates


def plan_done_job(
    job_path: str | Path,
    *,
    output_path: str | Path = "format_plan.json",
    preview_path: str | Path = "part1_preview.png",
    format_anyway: bool = False,
) -> dict[str, Any]:
    path = Path(job_path).expanduser().resolve()
    job_file = path / "job.json" if path.is_dir() else path
    job = _read_json(job_file)
    if job.get("status") != "DONE":
        raise ValueError("formatter requires an existing DONE Silence Cutter job")
    job_dir = job_file.parent
    report_path = Path(job.get("report_path") or job_dir / "pipeline_report.json")
    report = _read_json(report_path)
    clean_video = job_dir / "rendered.mp4"
    if not clean_video.is_file() and job.get("output_path"):
        candidate = Path(job["output_path"])
        if candidate.is_file() and candidate.name == "clean_master.mp4":
            clean_video = candidate
    source_video = Path(str(job.get("source_path") or ""))
    if not clean_video.is_file() and not source_video.is_file():
        raise FileNotFoundError("DONE job source and clean video are missing")
    debug = report.get("debug") or {}
    keep_intervals = debug.get("keep_intervals") or report.get("keep_intervals") or []
    render_segments = (
        (debug.get("render") or {}).get("segments") or clean_mapping(keep_intervals)
    )
    if not render_segments:
        raise ValueError("pipeline report contains no final render timeline mapping")
    clean_duration = float(
        report.get("output_duration") or report["expected_output_duration"]
    )
    status = formatter_status(clean_duration, format_anyway)
    part_count = 2 if clean_duration < 600 else 3
    from .title_rewrite import read_title_rewrite
    rewrite = read_title_rewrite(job_dir, str(job["title"]))
    title = fit_title(str(job["title"]), TITLE_BANNER)
    part_label_template = PART_LABELS[title["language"]]
    target = Path(output_path).expanduser().resolve()
    speech_intervals = debug.get("union_intervals") or []
    parts, candidates = plan_parts(
        clean_duration, render_segments, speech_intervals, part_count
    )
    geometry_source = clean_video if clean_video.is_file() else source_video
    width, height = probe_video_geometry(geometry_source)
    for part in parts:
        part["label"] = part_label_template.format(number=part["index"])
    layout = build_layout(title, parts[0]["label"])
    layout["crop_geometry"] = center_crop_geometry(width, height)
    selected_boundaries = [
        candidate["clean_timestamp"] for candidate in candidates if candidate["selected"]
    ]
    plan = {
        "schema_version": 2,
        "formatter_status": status,
        "part_count": part_count,
        "format_anyway": bool(format_anyway),
        "duration_policy": DURATION_POLICY,
        "source_job_id": job.get("id"),
        "source_job_path": str(job_file),
        "clean_video_path": str(clean_video) if clean_video.is_file() else None,
        "source_video_path": str(source_video) if source_video.is_file() else None,
        "direct_source_render": not clean_video.is_file(),
        "render_segments": render_segments,
        "input_duration": report.get("input_duration"),
        "clean_video_duration": clean_duration,
        "part_boundaries": selected_boundaries,
        "parts": parts,
        "boundary_candidates": candidates,
        "layout": layout,
        "title": title,
        "original_title": str(job["title"]),
        "rewritten_title": rewrite["rewritten_title"],
        "filename_base": rewrite["filename_base"],
        "title_rewrite_status": rewrite["status"],
        "title_rewrite_seconds": rewrite.get("total_seconds", 0.0),
        "part_label_template": part_label_template,
        "preview_path": str(Path(preview_path).expanduser().resolve()),
        "detector_reuse": {
            "timeline_source": "pipeline_report.debug.render.segments",
            "speech_boundary_source": "pipeline_report.debug.union_intervals",
            "silero_invoked": False,
            "sensevoice_invoked": False,
            "asr_invoked": False,
        },
    }
    target.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    from .preview import render_preview

    render_preview(plan, Path(preview_path).expanduser().resolve())
    return plan
