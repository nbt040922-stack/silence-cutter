from __future__ import annotations

import json
import itertools
import math
import os
import re
import tempfile
import time
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from semantic_cleaner.qwen import (
    QwenSemanticDetector, _contact_sheets, _extract_sampled_frames, _visual_candidates,
)


COARSE_PROMPT = """Choose ONLY the best {count} diverse moments from this entire long-video timeline. Do NOT describe cells or chunks. Do NOT print a header. Output exactly {count} lines only:
CENTER,SCORE,TOPIC
CENTER must be one absolute numeric source second. SCORE is 0..1. TOPIC is at most two words. Prefer importance, retention, novelty, payoff, tension and independent comprehension. Reject intro/ad/outro/CTA/filler/repetition. Topics and times must differ."""

@dataclass(frozen=True, slots=True)
class LongVideoSelectorConfig:
    threshold: float = 900.0
    coarse_chunk: float = 60.0
    refinement_interval: float = 20.0

    @classmethod
    def from_environment(cls) -> "LongVideoSelectorConfig":
        return cls(threshold=float(os.environ.get("LONG_VIDEO_THRESHOLD_SECONDS", "900")))


def adaptive_target_duration(source_duration: float) -> float:
    if source_duration <= 1500.0:
        return 180.0
    if source_duration <= 2400.0:
        return 180.0 + (source_duration - 1500.0) / 900.0 * 60.0
    return min(300.0, 240.0 + (source_duration - 2400.0) / 2400.0 * 60.0)


def enhanced_target_duration(source_duration: float) -> float:
    return min(240.0, max(120.0, source_duration * 0.18))


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _ranked_candidates(text: str, duration: float) -> list[dict[str, Any]]:
    result = []
    for line in text.strip().splitlines():
        row = [part.strip() for part in re.split(r"[|,]", line.strip().strip("|`,"))]
        if row and row[0].upper() == "C":
            row = row[1:]
        if len(row) < 3:
            continue
        try:
            center, score = float(row[0].removesuffix("s")), float(row[1])
        except ValueError:
            continue
        if 0 <= center <= duration and math.isfinite(score) and 0 <= score <= 1:
            result.append({"center": center, "score": score, "topic": row[2][:80],
                           "reason": " | ".join(row[3:])[:240] or row[2][:80]})
    return sorted(result, key=lambda item: item["score"], reverse=True)[:8]


def _topic_key(value: str) -> set[str]:
    return set(re.findall(r"[\w\u3040-\u30ff\u3400-\u9fff]+", value.casefold()))


def _duplicate_topic(left: str, right: str) -> bool:
    a, b = _topic_key(left), _topic_key(right)
    return bool(a and b and (a == b or len(a & b) / min(len(a), len(b)) >= 0.8))


def _form_range(
    center: float, target: float, duration: float, minimum: float = 180.0,
) -> tuple[float, float]:
    length = min(300.0, max(minimum, target))
    start = min(max(0.0, center - length / 2), max(0.0, duration - length))
    return start, min(duration, start + length)


def _select_ranked_ranges(
    ranked: list[dict[str, Any]], duration: float, target: float, minimum: float = 180.0,
) -> list[dict[str, Any]]:
    viable = []
    for candidate in ranked:
        start, end = _form_range(candidate["center"], target, duration, minimum)
        item = {"start": start, "end": end, "duration": end - start,
                "score": candidate["score"], "topic": candidate["topic"],
                "reason": candidate["reason"]}
        viable.append(item)
    best = None
    for group in itertools.combinations(viable, 3):
        ordered = sorted(group, key=lambda item: item["start"])
        if any(_duplicate_topic(left["topic"], right["topic"])
               for left, right in itertools.combinations(ordered, 2)):
            continue
        if any(left["end"] > right["start"] for left, right in zip(ordered, ordered[1:])):
            continue
        span = (ordered[-1]["end"] - ordered[0]["start"]) / duration
        centers = [(item["start"] + item["end"]) / 2 for item in ordered]
        minimum_separation = min(
            right - left for left, right in zip(centers, centers[1:])
        ) / duration
        value = sum(item["score"] for item in ordered) + 0.30 * span + 0.30 * minimum_separation
        if best is None or value > best[0]:
            best = value, ordered
    return best[1] if best else []


def validate_selected_ranges(
    ranges: Any, duration: float, *, minimum: float = 180.0,
) -> bool:
    if not isinstance(ranges, list) or len(ranges) != 3:
        return False
    previous_end = -1.0
    total = 0.0
    for item in sorted(ranges, key=lambda value: value.get("start", -1)):
        try:
            start, end, score = float(item["start"]), float(item["end"]), float(item["score"])
        except (KeyError, TypeError, ValueError):
            return False
        length = end - start
        if not (0 <= start < end <= duration and minimum <= length <= 300 and
                math.isfinite(score) and 0 <= score <= 1 and start >= previous_end):
            return False
        previous_end, total = end, total + length
    return total <= min(900.0, duration * 0.70) + 1e-6


def constrain_keep_intervals(
    keep: list[dict[str, float]], allowed: list[dict[str, float]],
) -> list[dict[str, float]]:
    result = []
    for source in keep:
        for scope in allowed:
            start, end = max(float(source["start"]), float(scope["start"])), min(float(source["end"]), float(scope["end"]))
            if start < end:
                result.append({"start": start, "end": end})
    return sorted(result, key=lambda item: item["start"])


def _skipped(duration: float, config: LongVideoSelectorConfig, reason: str) -> dict[str, Any]:
    return {"status": "LONG_VIDEO_SELECTOR_SKIPPED", "source_duration": duration,
            "threshold": config.threshold, "selected_ranges": [], "reason": reason}


def run_long_video_selector(
    source: str | Path, duration: float, output: str | Path, *,
    config: LongVideoSelectorConfig | None = None,
    detector_factory: Callable[[], Any] = QwenSemanticDetector,
    enhanced: bool = False,
    cache_root: Path | None = None,
) -> dict[str, Any]:
    config = config or LongVideoSelectorConfig.from_environment()
    destination, source_path = Path(output), Path(source)
    minimum = 120.0 if enhanced else 180.0
    if enhanced and duration * 0.70 < minimum * 3:
        result = _skipped(duration, config, "insufficient duration for three ranges")
        result["status"] = "ENHANCED_SELECTOR_SKIPPED_INSUFFICIENT_DURATION"
        _write(destination, result)
        return result
    if not enhanced and duration <= config.threshold:
        result = {"status": "NOT_APPLICABLE", "source_duration": duration,
                  "threshold": config.threshold, "selected_ranges": []}
        _write(destination, result)
        return result
    started = time.perf_counter()
    try:
        detector = detector_factory()
        extraction_time = coarse_time = ranking_time = refinement_time = 0.0
        context = (
            nullcontext(str(Path(cache_root))) if cache_root is not None
            else tempfile.TemporaryDirectory(prefix="long-video-selector-")
        )
        with context as directory:
            root = Path(directory)
            root.mkdir(parents=True, exist_ok=True)
            extraction_started = time.perf_counter()
            paths, timestamps = _extract_sampled_frames(
                source_path, 0.0, duration, config.coarse_chunk, root / "coarse",
            )
            extraction_time += time.perf_counter() - extraction_started
            sheets = _contact_sheets(
                paths, timestamps, cells=18, columns=6,
                cell_width=160, cell_height=105,
            )
            try:
                coarse_started = time.perf_counter()
                requested = 6 if enhanced else 3
                coarse_text = detector.generate_text(
                    sheets, COARSE_PROMPT.format(count=requested),
                    max_new_tokens=72 if enhanced else 48,
                    task="content_selector",
                )
                coarse_time = time.perf_counter() - coarse_started
            finally:
                for sheet in sheets:
                    sheet.close()
            ranking_started = time.perf_counter()
            ranked = _ranked_candidates(coarse_text, duration)
            ranking_time = time.perf_counter() - ranking_started
            if len(ranked) < 3:
                raise ValueError(f"fewer than 3 valid ranked candidates: {coarse_text[:500]!r}")
            target = enhanced_target_duration(duration) if enhanced else adaptive_target_duration(duration)
            selected = _select_ranked_ranges(ranked, duration, target, minimum)
            if len(selected) < 3:
                raise ValueError(
                    f"fewer than 3 non-overlapping diverse candidates: {ranked!r}"
                )
            fine_paths, fine_times = [], []
            extraction_started = time.perf_counter()
            for index, candidate in enumerate(selected):
                start, end = candidate["start"], candidate["end"]
                chunk_paths, chunk_times = _extract_sampled_frames(
                    source_path, start, end,
                    10.0 if enhanced else max(30.0, config.refinement_interval),
                    root / f"fine-{index:02d}",
                )
                fine_paths.extend(chunk_paths)
                fine_times.extend(chunk_times)
            extraction_time += time.perf_counter() - extraction_started
            refinement_started = time.perf_counter()
            _visual_candidates(fine_paths, fine_times, duration)
            refinement_time = time.perf_counter() - refinement_started
        if not validate_selected_ranges(selected, duration, minimum=minimum):
            raise ValueError("final selection is not exactly 3 valid diverse ranges")
        result = {
            "status": "APPLIED", "source_duration": duration, "threshold": config.threshold,
            "coarse_chunk_count": len(paths), "sampled_frame_count": len(paths) + len(fine_paths),
            "generation_count": detector.generation_count, "candidate_count": len(ranked),
            "ranked_candidates": ranked,
            "selected_ranges": [item | {"part_index": index} for index, item in enumerate(selected, 1)],
            "rejected_candidates": [item | {"rejection_reason": "not selected by diversity/overlap score"}
                                    for item in ranked if not any(item["center"] >= chosen["start"] and item["center"] <= chosen["end"] for chosen in selected)],
            "selected_total_duration": math.fsum(item["duration"] for item in selected),
            "model_load_time": detector.model_load_time, "frame_extraction_time": extraction_time,
            "coarse_time": coarse_time, "ranking_time": ranking_time,
            "refinement_time": refinement_time, "total_processing_time": time.perf_counter() - started,
            "peak_vram_bytes": detector.torch.cuda.max_memory_allocated(),
            "qwen_queue_wait": getattr(detector, "last_queue_wait", 0.0),
            "qwen_generation_time": getattr(detector, "last_generation_time", coarse_time),
        }
    except Exception as exc:
        result = _skipped(duration, config, f"{type(exc).__name__}: {exc}")
        result["total_processing_time"] = time.perf_counter() - started
    _write(destination, result)
    if cache_root is not None and result.get("status") == "APPLIED":
        result["_frame_cache"] = [
            {"path": str(path), "timestamp": timestamp}
            for path, timestamp in zip(fine_paths, fine_times, strict=True)
        ]
    return result
