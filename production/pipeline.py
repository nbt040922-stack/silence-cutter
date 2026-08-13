from __future__ import annotations

import math
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from silence_cutter.audio import (
    MediaProcessError, extract_analysis_audio, extract_analysis_audio_range, probe_media,
)
from silence_cutter.renderer import render_video
from silence_cutter.report import write_report
from speech_detector.config import HighRecallConfig
from speech_detector.models import SpeechInterval
from speech_detector.pipeline import analyze_audio, known_gap_metrics
from speech_detector.sensevoice_detector import SenseVoiceDetector

from .content_boundary import (
    BoundaryConfig, detect_content_window, slice_analysis_wav,
)


def _intersect_ranges(
    ranges: list[dict[str, float]], start: float, end: float,
) -> list[dict[str, float]]:
    result = []
    for item in sorted(ranges, key=lambda value: value["start"]):
        left, right = max(start, float(item["start"])), min(end, float(item["end"]))
        if left < right:
            result.append({"start": left, "end": right})
    return result


def _complement_ranges(
    ranges: list[dict[str, float]], duration: float, reason: str,
) -> list[dict[str, Any]]:
    result, cursor = [], 0.0
    for item in ranges:
        if cursor < item["start"]:
            result.append({"start": cursor, "end": item["start"], "reason": reason})
        cursor = item["end"]
    if cursor < duration:
        result.append({"start": cursor, "end": duration, "reason": reason})
    return result


def _analyze_allowed_ranges(
    full_audio_path: Path,
    directory: Path,
    ranges: list[dict[str, float]],
    *,
    config: HighRecallConfig,
    detector: SenseVoiceDetector,
) -> tuple[dict[str, Any], list[SpeechInterval]]:
    analyses: list[tuple[dict[str, Any], float]] = []
    disagreements: list[SpeechInterval] = []
    for index, scope in enumerate(ranges):
        duration = scope["end"] - scope["start"]
        audio = slice_analysis_wav(
            full_audio_path, directory / f"allowed-{index:02d}.wav",
            scope["start"], scope["end"],
        )
        result, local_disagreements = analyze_audio(
            audio, duration, config=config, sensevoice_detector=detector, known_gap_path=None,
        )
        analyses.append((result, scope["start"]))
        disagreements.extend(
            SpeechInterval(item.start + scope["start"], item.end + scope["start"], item.source)
            for item in local_disagreements
        )

    interval_keys = (
        "silero_intervals", "sensevoice_intervals", "union_intervals",
        "final_keep_intervals", "final_cut_intervals",
    )
    combined: dict[str, Any] = {key: [] for key in interval_keys}
    for result, offset in analyses:
        for key in interval_keys:
            combined[key].extend(
                item | {"start": float(item["start"]) + offset, "end": float(item["end"]) + offset}
                for item in result[key]
            )
    metrics = dict(analyses[0][0]["metrics"])
    summed = {
        "silero_speech_duration", "sensevoice_speech_duration", "union_speech_duration",
        "silero_interval_count", "sensevoice_interval_count", "union_interval_count",
        "silero_only_duration", "sensevoice_only_duration", "overlap_duration",
        "final_keep_duration", "final_cut_duration", "sensevoice_model_load_time",
        "sensevoice_inference_time", "silero_processing_time", "detector_wall_time",
        "fusion_processing_time", "timeline_processing_time", "core_analysis_time",
        "sensevoice_raw_asr_segment_count", "sensevoice_raw_asr_segment_duration",
        "sensevoice_fine_speech_interval_count", "sensevoice_fine_speech_duration",
    }
    for key in summed:
        metrics[key] = sum(float(result["metrics"].get(key, 0)) for result, _ in analyses)
    for key in ("largest_sensevoice_asr_segment", "largest_sensevoice_fine_speech_interval"):
        metrics[key] = max(float(result["metrics"].get(key, 0)) for result, _ in analyses)
    metrics["removed_percentage"] = (
        metrics["final_cut_duration"] / sum(item["end"] - item["start"] for item in ranges) * 100
    )
    combined["metrics"] = metrics
    combined["audio_duration"] = sum(item["end"] - item["start"] for item in ranges)
    combined["config"] = config.to_dict()
    return combined, sorted(disagreements, key=lambda item: (item.start, item.end))


@dataclass(frozen=True, slots=True)
class BrandingTailConfig:
    window: float = 10.0
    max_duration: float = 2.0
    following_silence_min: float = 0.5
    sustained_min: float = 3.0


@dataclass(frozen=True, slots=True)
class VisualSafetyConfig:
    post_intro_visual_trim: float = 0.30


def _apply_intro_greeting_heuristic(
    keep: list[dict[str, Any]],
    union_intervals: list[dict[str, Any]],
    effective_start: float,
    *,
    enabled: bool,
    detected_intro_boundary: float | None,
    structural_confidence: float,
    timeline_covers_start: bool = True,
) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any]
]:
    debug = {
        "enabled": enabled,
        "speech_1": None,
        "speech_2": None,
        "proposed_boundary": None,
        "applied": False,
        "reason": "disabled",
    }
    speech = sorted(union_intervals, key=lambda item: item["start"])
    if len(speech) >= 1:
        debug["speech_1"] = {
            "start": float(speech[0]["start"]), "end": float(speech[0]["end"])
        }
    if len(speech) >= 2:
        debug["speech_2"] = {
            "start": float(speech[1]["start"]), "end": float(speech[1]["end"])
        }
        debug["proposed_boundary"] = float(speech[1]["end"])
    greeting_valid = False
    if not enabled:
        pass
    elif not timeline_covers_start:
        debug["reason"] = "fused speech timeline begins after structural intro"
    elif len(speech) < 2:
        debug["reason"] = "fewer than two fused speech utterances"
    elif float(speech[1]["end"]) - effective_start > 30.0:
        debug["reason"] = "second utterance ends after 30-second safety window"
    else:
        greeting_valid = True
        debug["reason"] = "valid safe boundary at fused speech_2 end"
    greeting_boundary = debug["proposed_boundary"] if greeting_valid else None
    fusion = _fuse_intro_boundaries(
        detected_intro_boundary,
        structural_confidence,
        greeting_boundary,
        greeting_valid,
    )
    if not fusion["selected_source"].startswith("greeting"):
        return keep, [], debug, fusion
    boundary = float(fusion["final_boundary"])
    adjusted, removed = [], []
    for segment in keep:
        if segment["end"] <= boundary:
            removed.append(segment | {"reason": "intro_greeting"})
        elif segment["start"] < boundary:
            removed.append({
                "start": segment["start"], "end": boundary,
                "reason": "intro_greeting",
            })
            adjusted.append(segment | {"start": boundary})
        else:
            adjusted.append(segment)
    debug["applied"] = bool(removed)
    debug["reason"] = (
        "trimmed at existing fused speech_2 end"
        if removed else "safe boundary removed no KEEP content"
    )
    return adjusted, removed, debug, fusion


def _fuse_intro_boundaries(
    structural_boundary: float | None,
    structural_confidence: float,
    greeting_boundary: float | None,
    greeting_valid: bool,
) -> dict[str, Any]:
    if structural_boundary is not None:
        if greeting_valid and greeting_boundary is not None and greeting_boundary > structural_boundary:
            final, source = greeting_boundary, "greeting_later_than_structural"
            reason = "greeting safely extends accepted structural boundary"
        elif greeting_valid and greeting_boundary is not None:
            final, source = structural_boundary, "structural_later_than_greeting"
            reason = "accepted structural boundary is later than greeting"
        else:
            final, source = structural_boundary, "structural"
            reason = "accepted structural boundary preserved"
    elif greeting_valid and greeting_boundary is not None:
        final, source = greeting_boundary, "greeting_fallback"
        reason = "no accepted structural boundary; valid greeting fallback used"
    else:
        final, source = None, "none"
        reason = "no accepted structural or greeting boundary"
    return {
        "structural_boundary": structural_boundary,
        "structural_confidence": float(structural_confidence),
        "greeting_boundary": greeting_boundary,
        "greeting_valid": bool(greeting_valid),
        "final_boundary": final,
        "selected_source": source,
        "reason": reason,
    }


def _branding_tail_enabled(
    detected_intro_boundary: float | None,
    manual_content_start: float | None,
    keep_intro_outro: bool,
) -> bool:
    return (
        detected_intro_boundary is not None
        and manual_content_start is None
        and not keep_intro_outro
    )


def _remove_intro_branding_tail(
    keep: list[dict[str, Any]],
    content_start: float,
    *,
    enabled: bool,
    config: BrandingTailConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not enabled or len(keep) < 2:
        return keep, []
    first, following = keep[0], keep[1]
    if (
        0 <= first["start"] - content_start <= config.window
        and first["end"] - first["start"] <= config.max_duration
        and following["start"] - first["end"] >= config.following_silence_min
        and following["end"] - following["start"] >= config.sustained_min
    ):
        return keep[1:], [{
            "start": first["start"], "end": first["end"],
            "reason": "intro_branding_tail",
        }]
    return keep, []


def _apply_post_intro_visual_trim(
    keep: list[dict[str, Any]],
    content_end: float,
    *,
    enabled: bool,
    config: VisualSafetyConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float | None]:
    clean_start = keep[0]["start"] if keep else None
    if not enabled or clean_start is None:
        return keep, [], clean_start
    render_start = min(clean_start + config.post_intro_visual_trim, content_end)
    if render_start <= clean_start:
        return keep, [], clean_start
    adjusted = []
    removed = []
    for segment in keep:
        if segment["end"] <= render_start:
            removed.append(segment | {"reason": "intro_visual_safety"})
        elif segment["start"] < render_start:
            removed.append({
                "start": segment["start"], "end": render_start,
                "reason": "intro_visual_safety",
            })
            adjusted.append(segment | {"start": render_start})
        else:
            adjusted.append(segment)
    return adjusted, removed, clean_start


class ProductionRuntime:
    def __init__(
        self,
        config: HighRecallConfig | None = None,
        detector: SenseVoiceDetector | None = None,
    ) -> None:
        self.config = config or HighRecallConfig()
        self.detector = detector or SenseVoiceDetector(self.config)

    def analyze_selected_scope(
        self, input_path: str | Path, scope: dict[str, float], report_path: str | Path,
    ) -> dict[str, Any]:
        """Analyze one absolute source scope without decoding unused source audio."""
        source = Path(input_path).expanduser().resolve()
        media = probe_media(source)
        if not media["has_audio"]:
            raise MediaProcessError("input media contains no audio stream")
        source_duration = float(media["duration"])
        start, end = float(scope["start"]), float(scope["end"])
        if not 0 <= start < end <= source_duration:
            raise ValueError("selected range is outside source duration")
        began = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="production-selected-") as directory:
            extraction = time.perf_counter()
            audio = extract_analysis_audio_range(
                source, Path(directory) / "selected.wav", self.config.sample_rate, start, end,
            )
            extraction_time = time.perf_counter() - extraction
            analysis, _ = analyze_audio(
                audio, end - start, config=self.config,
                sensevoice_detector=self.detector, known_gap_path=None,
            )

        def absolute(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [item | {"start": float(item["start"]) + start,
                            "end": float(item["end"]) + start} for item in items]

        keep = absolute(analysis["final_keep_intervals"])
        cut = absolute(analysis["final_cut_intervals"])
        no_speech = not keep
        if no_speech:
            keep, cut = [], []
        keep_duration = math.fsum(item["end"] - item["start"] for item in keep)
        cut_duration = math.fsum(item["end"] - item["start"] for item in cut)
        report = {
            "input_duration": source_duration,
            "selected_source_range": {"start": start, "end": end},
            "keep_intervals": keep, "cut_intervals": cut,
            "keep_duration": keep_duration, "cut_duration": cut_duration,
            "expected_output_duration": keep_duration,
            "total_removed_duration": cut_duration,
            "removed_percentage": cut_duration / source_duration * 100,
            "silence_removed_duration": cut_duration,
            "no_speech_detected": no_speech,
            "audio_extraction_time": extraction_time,
            "speech_analysis_time": analysis["metrics"]["core_analysis_time"],
            "analysis_time": time.perf_counter() - began,
            "debug": {
                "silero_intervals": absolute(analysis["silero_intervals"]),
                "sensevoice_intervals": absolute(analysis["sensevoice_intervals"]),
                "union_intervals": absolute(analysis["union_intervals"]),
                "keep_intervals": keep, "cut_intervals": cut,
            },
        }
        write_report(Path(report_path), report)
        return report

    def process(
        self,
        input_path: str | Path,
        output_path: str | Path,
        *,
        analysis_only: bool = False,
        debug: bool = False,
        report_path: str | Path | None = None,
        known_gap_path: str | Path | None = None,
        content_start: float | None = None,
        content_end: float | None = None,
        keep_intro_outro: bool = False,
        boundary_config: BoundaryConfig | None = None,
        branding_tail_config: BrandingTailConfig | None = None,
        visual_safety_config: VisualSafetyConfig | None = None,
        allowed_ranges: list[dict[str, float]] | None = None,
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
            directory_path = Path(directory)
            full_audio_path = extract_analysis_audio(
                source, directory_path / "analysis.wav", self.config.sample_rate
            )
            audio_extraction_time = time.perf_counter() - extraction_started
            window, boundary_report = detect_content_window(
                source,
                full_audio_path,
                input_duration,
                config=boundary_config,
                content_start=content_start,
                content_end=content_end,
                disabled=keep_intro_outro,
            )
            boundary_analysis_time = boundary_report["boundary_analysis_time"]
            intro_boundary_time = boundary_report["intro_boundary_time"]
            outro_boundary_time = boundary_report["outro_boundary_time"]
            content_duration = window.end - window.start
            scoped_ranges = (
                _intersect_ranges(allowed_ranges, window.start, window.end)
                if allowed_ranges else []
            )
            if allowed_ranges and not scoped_ranges:
                raise ValueError("long-video selected ranges do not intersect content window")
            speech_started = time.perf_counter()
            if scoped_ranges:
                analysis, disagreements = _analyze_allowed_ranges(
                    full_audio_path, directory_path, scoped_ranges,
                    config=self.config, detector=self.detector,
                )
            else:
                audio_path = (
                    full_audio_path
                    if window.start == 0 and window.end == input_duration
                    else slice_analysis_wav(
                        full_audio_path, directory_path / "content.wav", window.start, window.end
                    )
                )
                analysis, disagreements = analyze_audio(
                    audio_path, content_duration, config=self.config,
                    sensevoice_detector=self.detector, known_gap_path=None,
                )
            speech_analysis_time = time.perf_counter() - speech_started
        analysis_time = time.perf_counter() - started
        analysis_origin = 0.0 if scoped_ranges else window.start
        def shift(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [
                item | {
                    "start": float(item["start"]) + analysis_origin,
                    "end": float(item["end"]) + analysis_origin,
                }
                for item in items
            ]

        shifted_union = shift(analysis["union_intervals"])
        keep = shift(analysis["final_keep_intervals"])
        scope_covers_content_start = not scoped_ranges or scoped_ranges[0]["start"] <= window.start
        greeting_enabled = content_start is None and not keep_intro_outro and scope_covers_content_start
        keep, greeting_cuts, greeting_debug, intro_fusion = _apply_intro_greeting_heuristic(
            keep,
            shifted_union,
            window.start,
            enabled=greeting_enabled,
            detected_intro_boundary=boundary_report["detected_intro_boundary"],
            structural_confidence=float(
                boundary_report.get("intro_confidence", window.intro_confidence)
            ),
            timeline_covers_start=window.start == 0.0 and scope_covers_content_start,
        )
        keep, branding_tail_cuts = _remove_intro_branding_tail(
            keep,
            window.start,
            enabled=_branding_tail_enabled(
                boundary_report["detected_intro_boundary"],
                content_start,
                keep_intro_outro,
            ) and scope_covers_content_start,
            config=branding_tail_config or BrandingTailConfig(),
        )
        visual_config = visual_safety_config or VisualSafetyConfig()
        keep, visual_safety_cuts, final_clean_content_start = _apply_post_intro_visual_trim(
            keep,
            window.end,
            enabled=_branding_tail_enabled(
                boundary_report["detected_intro_boundary"],
                content_start,
                keep_intro_outro,
            ) and scope_covers_content_start,
            config=visual_config,
        )
        silence_cuts = [item | {"reason": "silence"} for item in shift(analysis["final_cut_intervals"])]
        selector_cuts = (
            _complement_ranges(scoped_ranges, input_duration, "long_video_unselected")
            if scoped_ranges else []
        )
        boundary_cuts = []
        if not scoped_ranges:
            if window.start > 0:
                boundary_cuts.append({"start": 0.0, "end": window.start, "reason": "intro"})
            if window.end < input_duration:
                boundary_cuts.append({"start": window.end, "end": input_duration, "reason": "outro"})
        cut = sorted(
            [
                *boundary_cuts, *greeting_cuts, *branding_tail_cuts,
                *visual_safety_cuts, *silence_cuts, *selector_cuts,
            ],
            key=lambda item: item["start"],
        )
        known_path = (
            Path(known_gap_path).expanduser().resolve()
            if known_gap_path is not None else None
        )
        shifted_silero = [
            SpeechInterval(item["start"] + analysis_origin, item["end"] + analysis_origin, "silero")
            for item in analysis["silero_intervals"]
        ]
        shifted_sensevoice = [
            SpeechInterval(item["start"] + analysis_origin, item["end"] + analysis_origin, "sensevoice")
            for item in analysis["sensevoice_intervals"]
        ]
        known = known_gap_metrics(
            known_path,
            shifted_silero,
            shifted_sensevoice,
            keep,
            content_start=(
                greeting_debug["proposed_boundary"]
                if greeting_debug["applied"] else window.start
            ),
            content_end=window.end,
        )
        analysis["metrics"].update(known)
        no_speech_detected = not keep

        if no_speech_detected:
            keep = [{"start": 0.0, "end": input_duration}]
            cut = []
            silence_cuts = []
            boundary_cuts = []
            branding_tail_cuts = []
            visual_safety_cuts = []
            greeting_cuts = []
            selector_cuts = []

        render_time = 0.0
        render_diagnostics: dict[str, Any] = {}
        output_duration: float | None = None
        if not analysis_only:
            render_started = time.perf_counter()
            if no_speech_detected:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            else:
                render_video(
                    source, destination, keep,
                    **({"diagnostics": render_diagnostics} if debug else {}),
                )
            render_time = time.perf_counter() - render_started
            output_duration = float(probe_media(destination)["duration"])

        metrics = analysis["metrics"]
        keep_duration = sum(item["end"] - item["start"] for item in keep)
        expected_output_duration = keep_duration
        duration_error = (
            abs(output_duration - expected_output_duration)
            if output_duration is not None else None
        )
        silence_removed_duration = sum(item["end"] - item["start"] for item in silence_cuts)
        branding_tail_removed_duration = sum(
            item["end"] - item["start"] for item in branding_tail_cuts
        )
        visual_safety_removed_duration = sum(
            item["end"] - item["start"] for item in visual_safety_cuts
        )
        greeting_removed_duration = sum(
            item["end"] - item["start"] for item in greeting_cuts
        )
        structural_intro_removed_duration = sum(
            item["end"] - item["start"] for item in boundary_cuts if item["reason"] == "intro"
        )
        intro_removed_duration = structural_intro_removed_duration + greeting_removed_duration
        outro_removed_duration = sum(
            item["end"] - item["start"] for item in boundary_cuts if item["reason"] == "outro"
        )
        selector_removed_duration = sum(item["end"] - item["start"] for item in selector_cuts)
        cut_duration = (
            silence_removed_duration + branding_tail_removed_duration
            + visual_safety_removed_duration + greeting_removed_duration
            + structural_intro_removed_duration + outro_removed_duration
            + selector_removed_duration
        )
        report = {
            "input_duration": input_duration,
            "output_duration": output_duration,
            "expected_output_duration": expected_output_duration,
            "actual_output_duration": output_duration,
            "duration_error": duration_error,
            "silero_speech_duration": metrics["silero_speech_duration"],
            "sensevoice_speech_duration": metrics["sensevoice_speech_duration"],
            "union_speech_duration": metrics["union_speech_duration"],
            "keep_duration": keep_duration,
            "cut_duration": cut_duration,
            "removed_percentage": cut_duration / input_duration * 100,
            "content_start": window.start,
            "final_content_start": window.start,
            "detected_intro_boundary": boundary_report["detected_intro_boundary"],
            "post_intro_trim": boundary_report["post_intro_trim"],
            "content_end": window.end,
            "intro_removed_duration": intro_removed_duration,
            "intro_greeting_removed_duration": greeting_removed_duration,
            "intro_greeting_heuristic": greeting_debug,
            "intro_fusion": intro_fusion,
            "intro_branding_tail_removed_duration": branding_tail_removed_duration,
            "intro_visual_tail_removed_duration": visual_safety_removed_duration,
            "outro_removed_duration": outro_removed_duration,
            "silence_removed_duration": silence_removed_duration,
            "long_video_selector_applied": bool(scoped_ranges),
            "long_video_selected_ranges": scoped_ranges,
            "long_video_unselected_duration": selector_removed_duration,
            "branding_tail_detected": bool(branding_tail_cuts),
            "branding_tail_intervals": [
                {"start": item["start"], "end": item["end"]}
                for item in branding_tail_cuts
            ],
            "branding_tail_removed_duration": branding_tail_removed_duration,
            "final_clean_content_start": final_clean_content_start,
            "post_intro_visual_trim": visual_config.post_intro_visual_trim,
            "final_render_start": keep[0]["start"] if keep else None,
            "visual_safety_removed_duration": visual_safety_removed_duration,
            "first_final_keep_source_start": keep[0]["start"] if keep else None,
            "final_intro_start": keep[0]["start"] if keep else None,
            "detected_outro_boundary": boundary_report["detected_outro_boundary"],
            "final_content_end": window.end,
            "total_removed_duration": cut_duration,
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
            "cut_segments": cut,
            "audio_extraction_time": audio_extraction_time,
            "silero_time": metrics["silero_processing_time"],
            "sensevoice_load_time": metrics["sensevoice_model_load_time"],
            "sensevoice_inference_time": metrics["sensevoice_inference_time"],
            "sensevoice_requested_device": metrics.get("sensevoice_requested_device"),
            "sensevoice_active_device": metrics.get("sensevoice_active_device"),
            "sensevoice_cuda_fallback": metrics.get("sensevoice_cuda_fallback", False),
            "sensevoice_cuda_error": metrics.get("sensevoice_cuda_error", ""),
            "detector_wall_time": metrics["detector_wall_time"],
            "fusion_time": metrics["fusion_processing_time"],
            "timeline_time": metrics["timeline_processing_time"],
            "analysis_time": analysis_time,
            "boundary_analysis_time": boundary_analysis_time,
            "intro_boundary_time": intro_boundary_time,
            "outro_boundary_time": outro_boundary_time,
            "speech_analysis_time": speech_analysis_time,
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
            "known_gap_count_total": metrics["known_gap_count_total"],
            "known_gap_count_inside_content": metrics["known_gap_count_inside_content"],
            "known_gap_count_removed_by_intro": metrics["known_gap_count_removed_by_intro"],
            "known_gap_count_removed_by_outro": metrics["known_gap_count_removed_by_outro"],
            "protected_inside_content": metrics["protected_inside_content"],
            "fully_protected_inside_content": metrics["fully_protected_inside_content"],
            "partially_protected_inside_content": metrics["partially_protected_inside_content"],
            "still_unprotected_inside_content": metrics["still_unprotected_inside_content"],
        }
        if analysis_only:
            report["keep_intervals"] = keep
            report["cut_intervals"] = cut
        if debug:
            write_report(destination.parent / "content_boundary.json", boundary_report)
            write_report(destination.parent / "render_mapping.json", render_diagnostics)
            report["debug"] = {
                "render": render_diagnostics,
                "silero_intervals": shift(analysis["silero_intervals"]),
                "sensevoice_intervals": shift(analysis["sensevoice_intervals"]),
                "union_intervals": shifted_union,
                "keep_intervals": keep,
                "cut_intervals": cut,
                "intro_greeting_heuristic": greeting_debug,
                "intro_fusion": intro_fusion,
                "disagreements": [
                    item.to_dict() | {
                        "start": item.start + (0.0 if scoped_ranges else window.start),
                        "end": item.end + (0.0 if scoped_ranges else window.start),
                        "duration": item.end - item.start,
                        "detector": item.source,
                    }
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
