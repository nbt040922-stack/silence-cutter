from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from silence_cutter.audio import extract_analysis_audio, probe_media
from silence_cutter.report import write_report
from speech_detector.pipeline import analyze_audio

from .pipeline import ProductionRuntime


def _detector_comparison(
    runtime: ProductionRuntime, source: Path, known_gaps: Path
) -> dict[str, Any]:
    duration = float(probe_media(source)["duration"])
    results: dict[str, list[float]] = {"sequential": [], "parallel": []}
    with tempfile.TemporaryDirectory(prefix="production-benchmark-") as directory:
        audio = extract_analysis_audio(
            source, Path(directory) / "analysis.wav", runtime.config.sample_rate
        )
        for parallel in (False, True, False, True):
            report, _ = analyze_audio(
                audio,
                duration,
                config=replace(runtime.config, parallel_detectors=parallel),
                sensevoice_detector=runtime.detector,
                known_gap_path=known_gaps,
            )
            key = "parallel" if parallel else "sequential"
            results[key].append(report["metrics"]["detector_wall_time"])
    sequential = statistics.median(results["sequential"])
    parallel = statistics.median(results["parallel"])
    return {
        "sequential_detector_times": results["sequential"],
        "parallel_detector_times": results["parallel"],
        "sequential_detector_median": sequential,
        "parallel_detector_median": parallel,
        "chosen_default": "parallel" if parallel < sequential else "sequential",
    }


def _markdown(data: dict[str, Any]) -> str:
    cold, warm, full = data["cold_analysis"], data["warm_analysis"], data["full_run"]
    comparison = data["detector_comparison"]
    status = "PASS" if data["pass"] else "FAIL"
    return f"""# Production Benchmark Report

## Pipeline

Silero + SenseVoiceSmall -> UNION -> merge <=0.15s -> zero padding -> KEEP/CUT -> NVENC (libx264 fallback)

## Latency

| Run | Audio extraction | Model load | Silero | SenseVoice | Fusion | Timeline | Render | Total |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Cold analysis | {cold['audio_extraction_time']:.3f}s | {cold['sensevoice_load_time']:.3f}s | {cold['silero_time']:.3f}s | {cold['sensevoice_inference_time']:.3f}s | {cold['fusion_time']:.4f}s | {cold['timeline_time']:.4f}s | - | {cold['total_time']:.3f}s |
| Warm analysis | {warm['audio_extraction_time']:.3f}s | {warm['sensevoice_load_time']:.3f}s | {warm['silero_time']:.3f}s | {warm['sensevoice_inference_time']:.3f}s | {warm['fusion_time']:.4f}s | {warm['timeline_time']:.4f}s | - | {warm['total_time']:.3f}s |
| Full run | {full['audio_extraction_time']:.3f}s | {full['sensevoice_load_time']:.3f}s | {full['silero_time']:.3f}s | {full['sensevoice_inference_time']:.3f}s | {full['fusion_time']:.4f}s | {full['timeline_time']:.4f}s | {full['render_time']:.3f}s | {full['total_time']:.3f}s |

Parallel detector median: {comparison['parallel_detector_median']:.3f}s. Sequential detector median: {comparison['sequential_detector_median']:.3f}s. Selected default: **{comparison['chosen_default']}**.

## Speech and cuts

- Silero speech: {full['silero_speech_duration']:.3f}s
- SenseVoice speech: {full['sensevoice_speech_duration']:.3f}s
- Union speech: {full['union_speech_duration']:.3f}s
- Final KEEP: {full['keep_duration']:.3f}s
- Final CUT: {full['cut_duration']:.3f}s
- Removed: {full['removed_percentage']:.3f}%

## Safety and regression

- Known gaps (total): {full['known_gap_count_total']}
- Known gaps (inside content): {full['known_gap_count_inside_content']}
- Removed by intro: {full['known_gap_count_removed_by_intro']}
- Removed by outro: {full['known_gap_count_removed_by_outro']}
- Protected inside content: {full['protected_inside_content']}
- Fully protected inside content: {full['fully_protected_inside_content']}
- Partially protected inside content: {full['partially_protected_inside_content']}
- Still unprotected inside content: {full['still_unprotected_inside_content']}
- Whisper model loaded: NO
- Fun-ASR-Nano model loaded: NO
- SRT generated: NO
- Tests: {data['tests']}
- Overall: **{status}**
"""


def run_benchmark(
    input_path: Path,
    output_path: Path,
    known_gaps: Path,
    json_path: Path,
    markdown_path: Path,
    tests: str,
) -> dict[str, Any]:
    source = input_path.resolve()
    output = output_path.resolve()
    runtime = ProductionRuntime()
    with tempfile.TemporaryDirectory(prefix="production-runs-") as directory:
        temp = Path(directory)
        cold = runtime.process(
            source, temp / "cold.mp4", analysis_only=True,
            report_path=temp / "cold.json", known_gap_path=known_gaps,
        )
        warm = runtime.process(
            source, temp / "warm.mp4", analysis_only=True,
            report_path=temp / "warm.json", known_gap_path=known_gaps,
        )
        comparison = _detector_comparison(runtime, source, known_gaps)
    full = runtime.process(
        source, output, report_path=output.with_suffix(".speech.json"),
        known_gap_path=known_gaps,
    )
    passed = (
        warm["total_time"] <= 10
        and full["total_time"] <= 120
        and full["still_unprotected_inside_content"] == 0
        and comparison["chosen_default"] == "parallel"
        and "passed" in tests.lower()
    )
    data = {
        "generated_at_unix": time.time(),
        "input": str(source),
        "output": str(output),
        "pipeline": "Silero + SenseVoiceSmall -> UNION -> KEEP/CUT -> NVENC",
        "whisper_loaded": False,
        "funasr_nano_loaded": False,
        "srt_generated": False,
        "cold_analysis": cold,
        "warm_analysis": warm,
        "detector_comparison": comparison,
        "full_run": full,
        "tests": tests,
        "pass": passed,
    }
    write_report(json_path.resolve(), data)
    markdown_path.resolve().write_text(_markdown(data), encoding="utf-8")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark the production pipeline")
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--known-gaps", type=Path, required=True)
    parser.add_argument("--json", type=Path, default=Path("production_benchmark.json"))
    parser.add_argument("--markdown", type=Path, default=Path("PRODUCTION_BENCHMARK_REPORT.md"))
    parser.add_argument("--tests", required=True)
    args = parser.parse_args()
    result = run_benchmark(
        args.input, args.output, args.known_gaps.resolve(), args.json,
        args.markdown, args.tests,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
