from __future__ import annotations

import json
import math
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


ALLOWED_TYPES = {"INTRO", "AD", "OUTRO", "CONTENT"}


@dataclass(frozen=True)
class SemanticCleanerConfig:
    threshold: float = 0.85
    snap_tolerance: float = 10.0

    @classmethod
    def from_environment(cls) -> "SemanticCleanerConfig":
        return cls(
            threshold=float(os.environ.get("SEMANTIC_REMOVE_THRESHOLD", "0.85")),
            snap_tolerance=float(os.environ.get("SEMANTIC_SNAP_TOLERANCE", "10.0")),
        )


def _duration(intervals: list[dict[str, float]]) -> float:
    return math.fsum(item["end"] - item["start"] for item in intervals)


def _merge_intervals(intervals: list[dict[str, float]]) -> list[dict[str, float]]:
    merged: list[dict[str, float]] = []
    for item in sorted(intervals, key=lambda value: (value["start"], value["end"])):
        if merged and item["start"] <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], item["end"])
        else:
            merged.append({"start": item["start"], "end": item["end"]})
    return merged


def subtract_intervals(
    keep: list[dict[str, float]], remove: list[dict[str, float]],
) -> list[dict[str, float]]:
    removals = _merge_intervals(remove)
    result: list[dict[str, float]] = []
    for source in sorted(keep, key=lambda item: item["start"]):
        cursor, end = float(source["start"]), float(source["end"])
        for cut in removals:
            if cut["end"] <= cursor:
                continue
            if cut["start"] >= end:
                break
            if cut["start"] > cursor:
                result.append({"start": cursor, "end": min(end, cut["start"])})
            cursor = max(cursor, cut["end"])
            if cursor >= end:
                break
        if cursor < end:
            result.append({"start": cursor, "end": end})
    return [item for item in result if item["end"] > item["start"]]


def _safe_points(report: dict[str, Any], keep: list[dict[str, float]]) -> list[float]:
    speech = (report.get("debug") or {}).get("union_intervals") or []
    return sorted({
        float(item[key])
        for item in [*keep, *speech]
        for key in ("start", "end")
        if key in item and math.isfinite(float(item[key]))
    })


def _snap_forward(value: float, points: list[float], tolerance: float) -> float | None:
    if tolerance <= 0:
        return value
    candidates = [point for point in points if point >= value]
    candidate = min(candidates, default=None)
    return candidate if candidate is not None and abs(candidate - value) <= tolerance else None


def _normalize_segments(
    raw_segments: Any,
    duration: float,
    threshold: float,
    safe_points: list[float],
    snap_tolerance: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    uncertain: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    if not isinstance(raw_segments, list):
        return accepted, uncertain, [{"value": raw_segments, "reason": "segments must be a list"}]
    for raw in raw_segments:
        try:
            label = str(raw["type"]).upper()
            start, end = float(raw["start"]), float(raw["end"])
            confidence = float(raw["confidence"])
            if (
                label not in ALLOWED_TYPES or not all(map(math.isfinite, (start, end, confidence)))
                or start < 0 or end > duration or start >= end or not 0 <= confidence <= 1
            ):
                raise ValueError("invalid semantic interval")
            item = {
                "type": label.lower(), "start": start, "end": end,
                "confidence": confidence, "reason": str(raw.get("reason") or ""),
            }
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            invalid.append({"value": raw, "reason": str(exc)})
            continue
        if label == "CONTENT" or confidence < threshold:
            uncertain.append(item)
            continue
        snapped_start = _snap_forward(start, safe_points, snap_tolerance)
        snapped_end = _snap_forward(end, safe_points, snap_tolerance)
        if snapped_start is None or snapped_end is None or snapped_start >= snapped_end:
            invalid.append({"value": raw, "reason": "no safe forward-aligned interval"})
            continue
        item.update(
            start=snapped_start, end=snapped_end,
            raw_start=start, raw_end=end,
        )
        accepted.append(item)
    return accepted, uncertain, invalid


def _clean_mapping(keep: list[dict[str, float]]) -> list[dict[str, float]]:
    cursor = 0.0
    mapping = []
    for item in keep:
        length = item["end"] - item["start"]
        mapping.append({
            "output_start": cursor, "output_end": cursor + length,
            "source_start": item["start"], "source_end": item["end"],
        })
        cursor += length
    return mapping


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def write_skipped_artifact(
    output_path: str | Path, *, reason: str, model: str | None = None,
) -> dict[str, Any]:
    artifact = {
        "status": "SEMANTIC_CLEANER_SKIPPED",
        "model": model,
        "threshold": SemanticCleanerConfig.from_environment().threshold,
        "segments": [], "removed_segments": [], "kept_uncertain_segments": [],
        "invalid_segments": [], "reason": reason,
    }
    _write_json(Path(output_path), artifact)
    return artifact


def apply_semantic_cleaner(
    source_path: str | Path,
    report_path: str | Path,
    output_path: str | Path,
    *,
    detector: Callable[[Path, float], dict[str, Any]] | None = None,
    config: SemanticCleanerConfig | None = None,
) -> dict[str, Any]:
    source = Path(source_path).expanduser().resolve()
    report_file = Path(report_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    config = config or SemanticCleanerConfig.from_environment()
    started = time.perf_counter()
    report = json.loads(report_file.read_text(encoding="utf-8"))
    original_keep = [
        {"start": float(item["start"]), "end": float(item["end"])}
        for item in (report.get("keep_intervals") or (report.get("debug") or {}).get("keep_intervals") or [])
    ]
    duration = float(report["input_duration"])
    original_report = report_file.with_name(f"{report_file.stem}.original{report_file.suffix}")
    if not original_report.exists():
        shutil.copy2(report_file, original_report)
    try:
        if detector is None:
            from .qwen import QwenSemanticDetector

            detector = QwenSemanticDetector().detect
        detected = detector(source, duration)
        accepted, uncertain, invalid = _normalize_segments(
            detected.get("segments"), duration, config.threshold,
            _safe_points(report, original_keep), config.snap_tolerance,
        )
        final_keep = subtract_intervals(original_keep, accepted)
        if accepted and not final_keep:
            raise ValueError("semantic removal would erase all KEEP content")
        original_keep_duration = _duration(original_keep)
        final_keep_duration = _duration(final_keep)
        removed_duration = original_keep_duration - final_keep_duration
        report["original_keep_intervals"] = original_keep
        report["keep_intervals"] = final_keep
        report["keep_duration"] = final_keep_duration
        report["expected_output_duration"] = final_keep_duration
        report.pop("output_duration", None)
        report["semantic_removed_duration"] = removed_duration
        report["semantic_cleaner_status"] = "APPLIED"
        report["semantic_segments_path"] = str(output)
        report["total_removed_duration"] = float(report.get("total_removed_duration") or 0) + removed_duration
        report["removed_percentage"] = report["total_removed_duration"] / duration * 100
        debug = report.setdefault("debug", {})
        debug["original_keep_intervals"] = original_keep
        debug["keep_intervals"] = final_keep
        debug.setdefault("render", {})["segments"] = _clean_mapping(final_keep)
        artifact = {
            "status": "APPLIED",
            "model": detected.get("model"),
            "runtime": detected.get("runtime"),
            "threshold": config.threshold,
            "segments": detected.get("segments") or [],
            "coarse_segments": detected.get("coarse_segments") or [],
            "candidate_windows": detected.get("candidate_windows") or [],
            "removed_segments": accepted,
            "kept_uncertain_segments": uncertain,
            "invalid_segments": invalid,
            "original_keep_duration": original_keep_duration,
            "final_keep_duration": final_keep_duration,
            "removed_duration": removed_duration,
            "model_load_time": detected.get("model_load_time"),
            "frame_extraction_time": detected.get("frame_extraction_time"),
            "coarse_inference_time": detected.get("coarse_inference_time"),
            "fine_inference_time": detected.get("fine_inference_time"),
            "semantic_scan_time": detected.get("semantic_scan_time"),
            "coarse_frame_count": detected.get("coarse_frame_count"),
            "fine_frame_count": detected.get("fine_frame_count"),
            "contact_sheet_count": detected.get("contact_sheet_count"),
            "candidate_count": detected.get("candidate_count"),
            "generation_count": detected.get("generation_count"),
            "peak_vram_bytes": detected.get("peak_vram_bytes"),
            "allocated_vram_bytes": detected.get("allocated_vram_bytes"),
            "reserved_vram_bytes": detected.get("reserved_vram_bytes"),
            "total_additional_processing_time": time.perf_counter() - started,
            "original_pipeline_report": str(original_report),
        }
        _write_json(output, artifact)
        _write_json(report_file, report)
        return artifact
    except Exception as exc:
        return write_skipped_artifact(
            output, reason=f"{type(exc).__name__}: {exc}",
            model=os.environ.get("SEMANTIC_QWEN_MODEL"),
        )
