from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from silence_cutter.audio import MediaProcessError, extract_analysis_audio, probe_media
from silence_cutter.renderer import render_video
from silence_cutter.report import write_report
from speech_detector.config import HighRecallConfig
from speech_detector.pipeline import analyze_audio
from speech_detector.sensevoice_detector import SenseVoiceDetector


class ProductionRuntime:
    def __init__(
        self,
        config: HighRecallConfig | None = None,
        detector: SenseVoiceDetector | None = None,
    ) -> None:
        self.config = config or HighRecallConfig()
        self.detector = detector or SenseVoiceDetector(self.config)

    def process(
        self,
        input_path: str | Path,
        output_path: str | Path,
        *,
        analysis_only: bool = False,
        debug: bool = False,
        report_path: str | Path | None = None,
        known_gap_path: str | Path | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        source = Path(input_path).expanduser().resolve()
        destination = Path(output_path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"input media does not exist: {source}")
        if source == destination:
            raise ValueError("output_path must differ from input_path")
        media = probe_media(source)
        if not media["has_audio"]:
            raise MediaProcessError("input media contains no audio stream")
        input_duration = float(media["duration"])

        extraction_started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="production-speech-") as directory:
            audio_path = extract_analysis_audio(
                source, Path(directory) / "analysis.wav", self.config.sample_rate
            )
            audio_extraction_time = time.perf_counter() - extraction_started
            analysis, disagreements = analyze_audio(
                audio_path,
                input_duration,
                config=self.config,
                sensevoice_detector=self.detector,
                known_gap_path=(
                    Path(known_gap_path).expanduser().resolve()
                    if known_gap_path is not None
                    else None
                ),
            )
        analysis_time = time.perf_counter() - started
        keep = analysis["final_keep_intervals"]
        cut = analysis["final_cut_intervals"]
        no_speech_detected = not keep

        render_time = 0.0
        output_duration: float | None = None
        if not analysis_only:
            render_started = time.perf_counter()
            if no_speech_detected:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                keep = [{"start": 0.0, "end": input_duration}]
                cut = []
            else:
                render_video(source, destination, keep)
            render_time = time.perf_counter() - render_started
            output_duration = float(probe_media(destination)["duration"])

        metrics = analysis["metrics"]
        keep_duration = sum(item["end"] - item["start"] for item in keep)
        cut_duration = sum(item["end"] - item["start"] for item in cut)
        report = {
            "input_duration": input_duration,
            "output_duration": output_duration,
            "silero_speech_duration": metrics["silero_speech_duration"],
            "sensevoice_speech_duration": metrics["sensevoice_speech_duration"],
            "union_speech_duration": metrics["union_speech_duration"],
            "keep_duration": keep_duration,
            "cut_duration": cut_duration,
            "removed_percentage": cut_duration / input_duration * 100,
            "silero_interval_count": metrics["silero_interval_count"],
            "sensevoice_interval_count": metrics["sensevoice_interval_count"],
            "sensevoice_raw_asr_segment_count": metrics["sensevoice_raw_asr_segment_count"],
            "sensevoice_raw_asr_segment_duration": metrics["sensevoice_raw_asr_segment_duration"],
            "sensevoice_fine_speech_interval_count": metrics["sensevoice_fine_speech_interval_count"],
            "sensevoice_fine_speech_duration": metrics["sensevoice_fine_speech_duration"],
            "largest_sensevoice_asr_segment": metrics["largest_sensevoice_asr_segment"],
            "largest_sensevoice_fine_speech_interval": metrics["largest_sensevoice_fine_speech_interval"],
            "final_union_interval_count": metrics["union_interval_count"],
            "final_keep_count": len(keep),
            "final_cut_count": len(cut),
            "audio_extraction_time": audio_extraction_time,
            "silero_time": metrics["silero_processing_time"],
            "sensevoice_load_time": metrics["sensevoice_model_load_time"],
            "sensevoice_inference_time": metrics["sensevoice_inference_time"],
            "detector_wall_time": metrics["detector_wall_time"],
            "fusion_time": metrics["fusion_processing_time"],
            "timeline_time": metrics["timeline_processing_time"],
            "analysis_time": analysis_time,
            "render_time": render_time,
            "total_time": time.perf_counter() - started,
            "warm_model": metrics["warm_model"],
            "parallel_detectors": metrics["parallel_detectors"],
            "analysis_only": analysis_only,
            "no_speech_detected": no_speech_detected,
            "known_whisper_gap_count": metrics["known_whisper_gap_count"],
            "protected_by_silero_count": metrics["protected_by_silero_count"],
            "protected_by_sensevoice_count": metrics["protected_by_sensevoice_count"],
            "protected_by_union_count": metrics["protected_by_union_count"],
            "fully_protected_by_union_count": metrics.get(
                "fully_protected_by_union_count", metrics["protected_by_union_count"]
            ),
            "partially_protected_by_union_count": metrics.get(
                "partially_protected_by_union_count", 0
            ),
            "still_unprotected_count": metrics["still_unprotected_count"],
        }
        if analysis_only:
            report["keep_intervals"] = keep
            report["cut_intervals"] = cut
        if debug:
            report["debug"] = {
                "silero_intervals": analysis["silero_intervals"],
                "sensevoice_intervals": analysis["sensevoice_intervals"],
                "union_intervals": analysis["union_intervals"],
                "keep_intervals": keep,
                "cut_intervals": cut,
                "disagreements": [
                    item.to_dict()
                    | {"duration": item.end - item.start, "detector": item.source}
                    for item in disagreements
                ],
            }
        target_report = (
            Path(report_path).expanduser().resolve()
            if report_path is not None
            else destination.with_suffix(".speech.json")
        )
        write_report(target_report, report)
        return {"output_path": None if analysis_only else str(destination), "report_path": str(target_report), **report}


def process_video(
    input_path: str | Path,
    output_path: str | Path,
    **kwargs: Any,
) -> dict[str, Any]:
    return ProductionRuntime().process(input_path, output_path, **kwargs)
