from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from semantic_cleaner.qwen import (
    QwenSemanticDetector, _contact_sheets, _extract_sampled_frames, _visual_candidates,
)


COARSE_PROMPT = """Choose ONLY the best 3 diverse moments from this entire long-video timeline. Do NOT describe cells or chunks. Do NOT print a header. Output exactly 3 lines only:
CENTER,SCORE,TOPIC
CENTER must be one absolute numeric source second. SCORE is 0..1. Prefer importance, retention, novelty, payoff, tension and independent comprehension. Reject intro/ad/outro/CTA/filler/repetition. The 3 topics and times must differ."""

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
            center, score = float(row[0]), float(row[1])
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


def _form_range(center: float, target: float, duration: float) -> tuple[float, float]:
    length = min(300.0, max(180.0, target))
    start = min(max(0.0, center - length / 2), max(0.0, duration - length))
    return start, min(duration, start + length)


def _select_ranked_ranges(
    ranked: list[dict[str, Any]], duration: float, target: float,
) -> list[dict[str, Any]]:
    selected = []
    for candidate in ranked:
        start, end = _form_range(candidate["center"], target, duration)
        item = {"start": start, "end": end, "duration": end - start,
                "score": candidate["score"], "topic": candidate["topic"],
                "reason": candidate["reason"]}
        if any(_duplicate_topic(item["topic"], other["topic"]) for other in selected):
            continue
        if any(start < other["end"] and other["start"] < end for other in selected):
            continue
        selected.append(item)
        if len(selected) == 3:
            break
    return sorted(selected, key=lambda item: item["start"])


def validate_selected_ranges(ranges: Any, duration: float) -> bool:
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
        if not (0 <= start < end <= duration and 180 <= length <= 300 and
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
) -> dict[str, Any]:
    config = config or LongVideoSelectorConfig.from_environment()
    destination, source_path = Path(output), Path(source)
    if duration <= config.threshold:
        result = {"status": "NOT_APPLICABLE", "source_duration": duration,
                  "threshold": config.threshold, "selected_ranges": []}
        _write(destination, result)
        return result
    started = time.perf_counter()
    try:
        detector = detector_factory()
        extraction_time = coarse_time = ranking_time = refinement_time = 0.0
        with tempfile.TemporaryDirectory(prefix="long-video-selector-") as directory:
            root = Path(directory)
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
                coarse_text = detector.generate_text(
                    sheets, COARSE_PROMPT, max_new_tokens=48,
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
            target = adaptive_target_duration(duration)
            selected = _select_ranked_ranges(ranked, duration, target)
            if len(selected) < 3:
                raise ValueError(
                    f"fewer than 3 non-overlapping diverse candidates: {ranked!r}"
                )
            fine_paths, fine_times = [], []
            extraction_started = time.perf_counter()
            for index, candidate in enumerate(selected):
                start, end = candidate["start"], candidate["end"]
                chunk_paths, chunk_times = _extract_sampled_frames(
                    source_path, start, end, max(30.0, config.refinement_interval), root / f"fine-{index:02d}",
                )
                fine_paths.extend(chunk_paths)
                fine_times.extend(chunk_times)
            extraction_time += time.perf_counter() - extraction_started
            refinement_started = time.perf_counter()
            _visual_candidates(fine_paths, fine_times, duration)
            refinement_time = time.perf_counter() - refinement_started
        if not validate_selected_ranges(selected, duration):
            raise ValueError("final selection is not exactly 3 valid diverse ranges")
        result = {
            "status": "APPLIED", "source_duration": duration, "threshold": config.threshold,
            "coarse_chunk_count": len(paths), "sampled_frame_count": len(paths) + len(fine_paths),
            "generation_count": detector.generation_count, "candidate_count": len(ranked),
            "selected_ranges": selected, "selected_total_duration": math.fsum(item["duration"] for item in selected),
            "model_load_time": detector.model_load_time, "frame_extraction_time": extraction_time,
            "coarse_time": coarse_time, "ranking_time": ranking_time,
            "refinement_time": refinement_time, "total_processing_time": time.perf_counter() - started,
            "peak_vram_bytes": detector.torch.cuda.max_memory_allocated(),
        }
    except Exception as exc:
        result = _skipped(duration, config, f"{type(exc).__name__}: {exc}")
        result["total_processing_time"] = time.perf_counter() - started
    _write(destination, result)
    return result
