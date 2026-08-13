from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from formatter.planner import (
    PART_LABELS, TITLE_BANNER, build_layout, center_crop_geometry, fit_title,
    probe_video_geometry,
)
from formatter.renderer import render_format_plan
from long_video_selector.selector import (
    LongVideoSelectorConfig, _duplicate_topic, _form_range,
    enhanced_target_duration,
)
from production.pipeline import ProductionRuntime
from semantic_cleaner.cleaner import apply_semantic_cleaner
from semantic_cleaner.qwen import QwenSemanticDetector
from silence_cutter.audio import probe_media


class EnhancedFlowSkipped(RuntimeError):
    pass


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def recover_three_parts(
    candidates: list[dict[str, Any]], duration: float,
    process: Callable[[dict[str, Any], dict[str, float]], dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    target = enhanced_target_duration(duration)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for candidate in candidates:
        if any(_duplicate_topic(candidate["topic"], item["candidate"]["topic"]) for item in accepted):
            rejected.append(candidate | {"rejection_reason": "duplicate topic"})
            continue
        attempts = [target] + ([] if target >= 300 else [300.0])
        result = None
        for length in attempts:
            start, end = _form_range(candidate["center"], length, duration, 120.0)
            scope = {"start": start, "end": end}
            if any(start < item["range"]["end"] and item["range"]["start"] < end for item in accepted):
                continue
            attempt = process(candidate, scope)
            if 60.0 < float(attempt["final_duration"]) <= 300.0:
                result = attempt | {"candidate": candidate, "range": scope}
                break
        if result is None:
            rejected.append(candidate | {"rejection_reason": "no valid >60s non-overlapping result"})
            continue
        accepted.append(result)
        if len(accepted) == 3:
            break
    if len(accepted) != 3:
        raise EnhancedFlowSkipped("could not recover exactly three eligible parts")
    accepted.sort(key=lambda item: item["range"]["start"])
    for index, item in enumerate(accepted, 1):
        item["part_index"] = index
    return accepted, rejected


def _format_plan(
    source: Path, output_dir: Path, title_text: str, job_dir: Path,
    parts: list[dict[str, Any]], duration: float,
) -> Path:
    mapping, clean_parts, cursor = [], [], 0.0
    for part in parts:
        start_cursor = cursor
        for keep in part["final_keep"]:
            length = float(keep["end"]) - float(keep["start"])
            mapping.append({
                "output_start": cursor, "output_end": cursor + length,
                "source_start": float(keep["start"]), "source_end": float(keep["end"]),
            })
            cursor += length
        clean_parts.append({
            "index": part["part_index"], "clean_start": start_cursor,
            "clean_end": cursor, "duration": cursor - start_cursor,
        })
    title = fit_title(title_text, TITLE_BANNER)
    label_template = PART_LABELS[title["language"]]
    for part in clean_parts:
        part["label"] = label_template.format(number=part["index"])
    width, height = probe_video_geometry(source)
    layout = build_layout(title, clean_parts[0]["label"])
    layout["crop_geometry"] = center_crop_geometry(width, height)
    job_file = job_dir / "job.json"
    job_file.write_text(json.dumps({
        "id": job_dir.name, "status": "DONE", "title": title_text,
        "source_path": str(source), "output_folder": str(output_dir), "output_path": None,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    plan = {
        "schema_version": 2, "formatter_status": "PLANNED", "part_count": 3,
        "enhanced_content_selection": True, "direct_source_render": True,
        "source_job_id": job_dir.name, "source_job_path": str(job_file),
        "source_video_path": str(source), "clean_video_path": None,
        "render_segments": mapping, "input_duration": duration,
        "clean_video_duration": cursor, "parts": clean_parts,
        "boundary_candidates": [], "part_boundaries": [item["clean_end"] for item in clean_parts[:-1]],
        "layout": layout, "title": title, "part_label_template": label_template,
        "preview_path": None,
    }
    path = job_dir / "format_plan.json"
    _write(path, plan)
    return path


def run_enhanced_content_flow(
    source: Path, output_dir: Path, title: str, job_dir: Path, *,
    selector: Callable[..., dict[str, Any]] | None = None,
    runtime: ProductionRuntime | None = None,
    semantic_detector_factory: Callable[[], Any] = QwenSemanticDetector,
    renderer: Callable[[Path], dict[str, Any]] = render_format_plan,
) -> list[Path]:
    started = time.perf_counter()
    source = source.resolve()
    duration = float(probe_media(source)["duration"])
    artifact_path = job_dir / "enhanced_content_selection.json"
    selection_path = job_dir / "long_video_selection.json"
    if selector is None:
        command = [
            sys.executable, "-m", "long_video_selector", str(source),
            "--output", str(selection_path), "--enhanced",
        ]
        timeout = float(os.environ.get("ENHANCED_SELECTOR_TIMEOUT", "180"))
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout, creationflags=0x08000000 if os.name == "nt" else 0,
            )
            if completed.returncode or not selection_path.is_file():
                detail = (completed.stderr or completed.stdout or "enhanced selector failed").strip()
                selection = {"status": "ENHANCED_CONTENT_SELECTION_SKIPPED", "reason": detail[-2000:]}
            else:
                selection = json.loads(selection_path.read_text(encoding="utf-8"))
        except Exception as exc:
            selection = {"status": "ENHANCED_CONTENT_SELECTION_SKIPPED",
                         "reason": f"{type(exc).__name__}: {exc}"}
    else:
        selection = selector(
            source, duration, selection_path,
            config=LongVideoSelectorConfig.from_environment(), enhanced=True,
        )
    if selection.get("status") != "APPLIED":
        artifact = {"status": "ENHANCED_CONTENT_SELECTION_SKIPPED",
                    "source_duration": duration, "reason": selection.get("reason") or selection.get("status")}
        _write(artifact_path, artifact)
        raise EnhancedFlowSkipped(artifact["reason"])

    runtime = runtime or ProductionRuntime()
    semantic_detector = semantic_detector_factory()
    semantic_result = semantic_detector.detect(source, duration)
    attempts = 0

    def process(candidate: dict[str, Any], scope: dict[str, float]) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        attempt_dir = job_dir / f"candidate-{attempts:02d}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        report_path = attempt_dir / "pipeline_report.json"
        runtime.process(
            source, attempt_dir / "unused.mp4", analysis_only=True, debug=True,
            report_path=report_path, allowed_ranges=[scope], keep_intro_outro=True,
        )
        semantic_path = attempt_dir / "semantic_segments.json"
        semantic = apply_semantic_cleaner(
            source, report_path, semantic_path,
            detector=lambda _source, _duration: semantic_result,
        )
        if semantic.get("status") != "APPLIED":
            raise EnhancedFlowSkipped("semantic cleaner failed in enhanced mode")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        keep = report.get("keep_intervals") or (report.get("debug") or {}).get("keep_intervals") or []
        final_duration = math.fsum(float(item["end"]) - float(item["start"]) for item in keep)
        if report.get("no_speech_detected"):
            final_duration = 0.0
        return {
            "selected_source_range": scope, "silence_keep": report.get("original_keep_intervals", keep),
            "semantic_removed": semantic.get("removed_segments") or [],
            "final_keep": keep, "final_duration": final_duration,
            "report_path": str(report_path), "semantic_path": str(semantic_path),
        }

    try:
        chosen_topics = [item["topic"] for item in selection["selected_ranges"]]
        ranked = selection["ranked_candidates"]
        preferred = [item for topic in chosen_topics for item in ranked if item["topic"] == topic]
        alternates = [item for item in ranked if item not in preferred]
        parts, rejected = recover_three_parts(preferred + alternates, duration, process)
        for part in parts:
            index = part["part_index"]
            semantic_path = job_dir / f"semantic_segments_part_{index}.json"
            report_path = job_dir / f"pipeline_report_part_{index}.json"
            shutil.copy2(part["semantic_path"], semantic_path)
            shutil.copy2(part["report_path"], report_path)
            part["semantic_path"] = str(semantic_path)
            part["report_path"] = str(report_path)
        plan_path = _format_plan(source, output_dir, title, job_dir, parts, duration)
        rendered = renderer(plan_path)
        outputs = [Path(item["path"]) for item in rendered.get("formatted_outputs") or []]
        if rendered.get("formatter_status") != "DONE" or len(outputs) != 3:
            raise EnhancedFlowSkipped(rendered.get("formatter_error") or "enhanced render failed")
        artifact = {
            "status": "APPLIED", "source_duration": duration,
            "ranked_candidates": selection["ranked_candidates"],
            "rejected_candidates": rejected, "parts": [{
                key: value for key, value in item.items() if key != "candidate"
            } for item in parts],
            "selector_generation_count": selection.get("generation_count"),
            "selector_model_load_time": selection.get("model_load_time"),
            "semantic_model_load_count": 1, "processing_attempt_count": attempts,
            "total_processing_time": time.perf_counter() - started,
            "outputs": [str(path.resolve()) for path in outputs],
        }
        _write(artifact_path, artifact)
        return outputs
    except Exception as exc:
        artifact = {
            "status": "ENHANCED_CONTENT_SELECTION_SKIPPED", "source_duration": duration,
            "ranked_candidates": selection.get("ranked_candidates") or [],
            "reason": f"{type(exc).__name__}: {exc}",
            "total_processing_time": time.perf_counter() - started,
        }
        _write(artifact_path, artifact)
        raise EnhancedFlowSkipped(artifact["reason"]) from exc
