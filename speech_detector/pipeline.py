from __future__ import annotations

import json
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from silence_cutter.audio import MediaProcessError, extract_analysis_audio, probe_media
from silence_cutter.report import write_report

from .config import HighRecallConfig
from .fusion import (
    build_keep_cut, fully_covered, interval_duration, overlap_duration,
    normalize_intervals, subtract_intervals, union_intervals,
)
from .models import SpeechInterval
from .sensevoice_detector import SenseVoiceDetector
from .silero_detector import detect_with_silero


def known_gap_metrics(
    path: Path | None,
    silero: list[SpeechInterval],
    sensevoice: list[SpeechInterval],
    final_keep: list[dict[str, float]],
    *,
    content_start: float = 0.0,
    content_end: float | None = None,
) -> dict[str, int]:
    if path is None or not path.is_file():
        return {
            "known_whisper_gap_count": 0, "protected_by_silero_count": 0,
            "protected_by_sensevoice_count": 0, "protected_by_union_count": 0,
            "fully_protected_by_silero_count": 0,
            "fully_protected_by_sensevoice_count": 0,
            "fully_protected_by_union_count": 0,
            "partially_protected_by_union_count": 0,
            "still_unprotected_count": 0,
            "known_gap_count_total": 0,
            "known_gap_count_inside_content": 0,
            "known_gap_count_removed_by_intro": 0,
            "known_gap_count_removed_by_outro": 0,
            "protected_inside_content": 0,
            "fully_protected_inside_content": 0,
            "partially_protected_inside_content": 0,
            "still_unprotected_inside_content": 0,
        }
    data = json.loads(path.read_text(encoding="utf-8"))
    gaps = data.get("gaps", data) if isinstance(data, dict) else data
    keeps = [SpeechInterval(item["start"], item["end"], "final_keep") for item in final_keep]
    content_end = float("inf") if content_end is None else content_end

    def coverage(items: list[dict[str, Any]]) -> tuple[int, int, int, int, int, int]:
        silero_count = sensevoice_count = union_count = 0
        silero_full = sensevoice_full = union_full = 0
        for gap in items:
            start, end = float(gap["start"]), float(gap["end"])
            silero_count += any(item.start < end and start < item.end for item in silero)
            sensevoice_count += any(item.start < end and start < item.end for item in sensevoice)
            union_count += any(item.start < end and start < item.end for item in keeps)
            silero_full += fully_covered(start, end, silero)
            sensevoice_full += fully_covered(start, end, sensevoice)
            union_full += fully_covered(start, end, keeps)
        return silero_count, sensevoice_count, union_count, silero_full, sensevoice_full, union_full

    silero_count, sensevoice_count, union_count, silero_full, sensevoice_full, union_full = coverage(gaps)
    inside: list[dict[str, Any]] = []
    removed_intro = removed_outro = 0
    for gap in gaps:
        start, end = float(gap["start"]), float(gap["end"])
        if end <= content_start:
            removed_intro += 1
        elif start >= content_end:
            removed_outro += 1
        else:
            inside.append({"start": max(start, content_start), "end": min(end, content_end)})
    _, _, protected_inside, _, _, fully_inside = coverage(inside)
    return {
        "known_whisper_gap_count": len(gaps),
        "protected_by_silero_count": silero_count,
        "protected_by_sensevoice_count": sensevoice_count,
        "protected_by_union_count": union_count,
        "fully_protected_by_silero_count": silero_full,
        "fully_protected_by_sensevoice_count": sensevoice_full,
        "fully_protected_by_union_count": union_full,
        "partially_protected_by_union_count": union_count - union_full,
        "still_unprotected_count": len(gaps) - union_count,
        "known_gap_count_total": len(gaps),
        "known_gap_count_inside_content": len(inside),
        "known_gap_count_removed_by_intro": removed_intro,
        "known_gap_count_removed_by_outro": removed_outro,
        "protected_inside_content": protected_inside,
        "fully_protected_inside_content": fully_inside,
        "partially_protected_inside_content": protected_inside - fully_inside,
        "still_unprotected_inside_content": len(inside) - protected_inside,
    }


def analyze_audio(
    audio_path: Path,
    duration: float,
    *,
    config: HighRecallConfig,
    sensevoice_detector: SenseVoiceDetector,
    known_gap_path: Path | None = None,
) -> tuple[dict[str, Any], list[SpeechInterval]]:
    started = time.perf_counter()
    model_was_loaded = sensevoice_detector.loaded

    def run_silero():
        detector_started = time.perf_counter()
        intervals = detect_with_silero(
            audio_path, sample_rate=config.sample_rate, threshold=config.vad_threshold
        )
        return (
            normalize_intervals(intervals, duration, "silero"),
            time.perf_counter() - detector_started,
        )

    def run_sensevoice():
        return sensevoice_detector.detect(audio_path, duration)

    detector_started = time.perf_counter()
    if config.parallel_detectors:
        with ThreadPoolExecutor(max_workers=2) as pool:
            silero_future = pool.submit(run_silero)
            sensevoice_future = pool.submit(run_sensevoice)
            silero, silero_time = silero_future.result()
            sensevoice, sensevoice_time, sensevoice_diagnostics = sensevoice_future.result()
    else:
        silero, silero_time = run_silero()
        sensevoice, sensevoice_time, sensevoice_diagnostics = run_sensevoice()
    detector_wall_time = time.perf_counter() - detector_started

    fusion_started = time.perf_counter()
    union = union_intervals(silero, sensevoice, duration)
    silero_only = subtract_intervals(silero, sensevoice, "silero_only")
    sensevoice_only = subtract_intervals(sensevoice, silero, "sensevoice_only")
    overlap = overlap_duration(silero, sensevoice)
    fusion_time = time.perf_counter() - fusion_started
    timeline_started = time.perf_counter()
    timeline = build_keep_cut(union, duration, config)
    timeline_time = time.perf_counter() - timeline_started
    disagreements = sorted(
        [*silero_only, *sensevoice_only], key=lambda item: (item.start, item.end)
    )
    keep_duration = sum(item["end"] - item["start"] for item in timeline["keep"])
    cut_duration = sum(item["end"] - item["start"] for item in timeline["cut"])
    known = known_gap_metrics(
        known_gap_path, silero, sensevoice, timeline["keep"]
    )
    metrics = {
        "silero_speech_duration": interval_duration(silero),
        "sensevoice_speech_duration": interval_duration(sensevoice),
        "union_speech_duration": interval_duration(union),
        "silero_interval_count": len(silero),
        "sensevoice_interval_count": len(sensevoice),
        "union_interval_count": len(union),
        "silero_only_duration": interval_duration(silero_only),
        "sensevoice_only_duration": interval_duration(sensevoice_only),
        "overlap_duration": overlap,
        "final_keep_duration": keep_duration,
        "final_cut_duration": cut_duration,
        "removed_percentage": cut_duration / duration * 100,
        "sensevoice_model_load_time": (
            0.0 if model_was_loaded else sensevoice_detector.model_load_time
        ),
        "sensevoice_inference_time": sensevoice_time,
        "silero_processing_time": silero_time,
        "detector_wall_time": detector_wall_time,
        "fusion_processing_time": fusion_time,
        "timeline_processing_time": timeline_time,
        "warm_model": model_was_loaded,
        "parallel_detectors": config.parallel_detectors,
        "core_analysis_time": time.perf_counter() - started,
        **sensevoice_diagnostics,
        **known,
    }
    return {
        "audio_duration": duration,
        "config": config.to_dict(),
        "silero_intervals": [item.to_dict() for item in silero],
        "sensevoice_intervals": [item.to_dict() for item in sensevoice],
        "union_intervals": [item.to_dict() for item in union],
        "final_keep_intervals": timeline["keep"],
        "final_cut_intervals": timeline["cut"],
        "metrics": metrics,
    }, disagreements


def analyze_speech(
    input_path: str | Path,
    *,
    output_path: str | Path = "high_recall_speech.json",
    disagreement_path: str | Path = "speech_disagreements.json",
    known_gap_path: str | Path | None = "asr_benchmark/whisper_speech_gaps.json",
    config: HighRecallConfig | None = None,
    sensevoice_detector: SenseVoiceDetector | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    config = config or HighRecallConfig()
    source = Path(input_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"input media does not exist: {source}")
    media = probe_media(source)
    if not media["has_audio"]:
        raise MediaProcessError("input media contains no audio stream")
    duration = float(media["duration"])
    detector = sensevoice_detector or SenseVoiceDetector(config)
    extraction_started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="high-recall-") as directory:
        audio_path = extract_analysis_audio(
            source, Path(directory) / "analysis.wav", config.sample_rate
        )
        extraction_time = time.perf_counter() - extraction_started
        report, disagreements = analyze_audio(
            audio_path,
            duration,
            config=config,
            sensevoice_detector=detector,
            known_gap_path=(
                Path(known_gap_path).expanduser().resolve()
                if known_gap_path is not None
                else None
            ),
        )
    report["metrics"]["audio_extraction_time"] = extraction_time
    report["metrics"]["total_analysis_time"] = time.perf_counter() - started
    write_report(Path(output_path).expanduser().resolve(), report)
    write_report(
        Path(disagreement_path).expanduser().resolve(),
        [item.to_dict() | {"duration": item.end - item.start, "detector": item.source} for item in disagreements],
    )
    return report
