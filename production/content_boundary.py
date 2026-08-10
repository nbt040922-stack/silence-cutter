from __future__ import annotations

import math
import re
import statistics
import time
import wave
from array import array
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from silence_cutter.audio import MediaProcessError, _require_executable, _run


@dataclass(frozen=True, slots=True)
class ContentWindow:
    start: float
    end: float
    intro_removed: float
    outro_removed: float
    intro_confidence: float
    outro_confidence: float
    intro_reason: str
    outro_reason: str

    def __post_init__(self) -> None:
        values = (self.start, self.end, self.intro_removed, self.outro_removed)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("content window values must be finite")
        if self.start < 0 or self.end <= self.start:
            raise ValueError("content window must have positive monotonic bounds")


@dataclass(frozen=True, slots=True)
class BoundaryConfig:
    intro_search_window: float = 120.0
    outro_search_window: float = 60.0
    confidence_threshold: float = 0.70
    post_intro_trim: float = 2.0
    analysis_fps: int = 4
    analysis_width: int = 320

    def __post_init__(self) -> None:
        if self.intro_search_window <= 0 or self.outro_search_window <= 0:
            raise ValueError("content search windows must be positive")
        if not 0 <= self.confidence_threshold <= 1:
            raise ValueError("content confidence threshold must be between 0 and 1")
        if self.post_intro_trim < 0:
            raise ValueError("post-intro trim must be non-negative")


@dataclass(frozen=True, slots=True)
class EdgeFeatures:
    start: float
    end: float
    scene_changes: tuple[float, ...] = ()
    freezes: tuple[tuple[float, float], ...] = ()
    black_frames: tuple[tuple[float, float], ...] = ()
    energy: tuple[tuple[float, float], ...] = ()
    error: str = ""


_SCENE_RE = re.compile(r"pts_time:([0-9.]+)")
_FREEZE_START_RE = re.compile(r"freeze_start: ([0-9.]+)")
_FREEZE_END_RE = re.compile(r"freeze_end: ([0-9.]+)")
_BLACK_RE = re.compile(
    r"black_start:([0-9.]+) black_end:([0-9.]+) black_duration:[0-9.]+"
)


def _visual_features(video: Path, start: float, end: float, config: BoundaryConfig) -> EdgeFeatures:
    ffmpeg = _require_executable("ffmpeg")
    graph = (
        f"setpts=PTS-STARTPTS,fps={config.analysis_fps},"
        f"scale={config.analysis_width}:-2,freezedetect=n=-50dB:d=2,"
        "blackdetect=d=1:pix_th=0.10,select='gt(scene,0.30)',showinfo"
    )
    completed = _run(
        [
            ffmpeg, "-hide_banner", "-nostdin", "-ss", f"{start:.6f}",
            "-t", f"{end - start:.6f}", "-i", str(video), "-an",
            "-vf", graph, "-f", "null", "-",
        ],
        "content edge analysis",
    )
    log = completed.stderr
    scenes = tuple(start + float(value) for value in _SCENE_RE.findall(log))
    freezes: list[tuple[float, float]] = []
    pending: float | None = None
    for line in log.splitlines():
        found_start = _FREEZE_START_RE.search(line)
        if found_start:
            pending = start + float(found_start.group(1))
        found_end = _FREEZE_END_RE.search(line)
        if found_end and pending is not None:
            freezes.append((pending, start + float(found_end.group(1))))
            pending = None
    if pending is not None:
        freezes.append((pending, end))
    black = tuple(
        (start + float(left), start + float(right))
        for left, right in _BLACK_RE.findall(log)
    )
    return EdgeFeatures(start, end, scenes, tuple(freezes), black)


def _audio_energy(audio: Path, start: float, end: float) -> tuple[tuple[float, float], ...]:
    with wave.open(str(audio), "rb") as source:
        rate = source.getframerate()
        if source.getnchannels() != 1 or source.getsampwidth() != 2:
            raise MediaProcessError("boundary audio must be mono 16-bit PCM")
        source.setpos(min(source.getnframes(), int(start * rate)))
        chunk_frames = max(1, rate // 2)
        remaining = max(0, int((end - start) * rate))
        result = []
        cursor = start
        while remaining:
            count = min(chunk_frames, remaining)
            samples = array("h")
            samples.frombytes(source.readframes(count))
            if not samples:
                break
            rms = math.sqrt(sum(value * value for value in samples) / len(samples)) / 32768
            result.append((cursor, rms))
            cursor += len(samples) / rate
            remaining -= len(samples)
    return tuple(result)


def _mean_energy(features: EdgeFeatures, start: float, end: float) -> float:
    values = [value for timestamp, value in features.energy if start <= timestamp < end]
    return statistics.fmean(values) if values else 0.0


def _score_candidate(
    timestamp: float, features: EdgeFeatures, *, intro: bool
) -> dict[str, Any]:
    before_scenes = sum(timestamp - 8 <= item < timestamp for item in features.scene_changes)
    after_scenes = sum(timestamp < item <= timestamp + 8 for item in features.scene_changes)
    before_energy = _mean_energy(features, max(features.start, timestamp - 4), timestamp)
    after_energy = _mean_energy(features, timestamp, min(features.end, timestamp + 5))
    audio_change = abs(after_energy - before_energy) / max(before_energy, after_energy, 0.01)
    reasons = []
    score = 0.0
    if any(abs(item - timestamp) <= 0.3 for item in features.scene_changes):
        score += 0.20
        reasons.append("scene_transition")
    if audio_change >= 0.45:
        score += min(0.25, 0.25 * audio_change)
        reasons.append("audio_transition")
    if intro and before_scenes >= 2 and after_scenes <= 1:
        score += 0.30
        reasons.append("montage_to_stable_content")
    if not intro and before_scenes >= 1 and after_scenes == 0:
        score += 0.20
        reasons.append("normal_content_to_stable_end")
    adjacent = (
        any(left < timestamp and 0 <= timestamp - right <= 1.0 for left, right in features.freezes)
        if intro
        else any(left >= timestamp - 1.0 and right - left >= 2 for left, right in features.freezes)
    )
    if adjacent:
        score += 0.35 if intro else 0.40
        reasons.append("static_graphic_sequence")
    black = (
        any(left < timestamp and 0 <= timestamp - right <= 1.0 for left, right in features.black_frames)
        if intro
        else any(left >= timestamp - 1.0 for left, _right in features.black_frames)
    )
    if black:
        score += 0.35 if intro else 0.40
        reasons.append("black_or_logo_transition")
    return {
        "timestamp": timestamp,
        "confidence": min(score, 1.0),
        "evidence_count": len(reasons),
        "reason": ", ".join(reasons),
        "audio_change": audio_change,
    }


def _intro_scores(candidate: dict[str, Any], features: EdgeFeatures) -> dict[str, Any]:
    timestamp = float(candidate["timestamp"])
    post_end = min(features.end, timestamp + 6)
    post_scene_count = sum(timestamp < item <= post_end for item in features.scene_changes)
    post_energy = _mean_energy(features, timestamp, post_end)
    post_boundary_support = (
        (0.60 if post_scene_count <= 1 else 0.0)
        + (0.40 if post_energy > 0.005 else 0.0)
    )
    before_energy = _mean_energy(features, max(features.start, timestamp - 4), timestamp)
    after_energy = _mean_energy(features, timestamp, min(features.end, timestamp + 5))
    audio_drop = max(
        0.0, (before_energy - after_energy) / max(before_energy, after_energy, 0.01)
    )
    branding_sting_end_score = 0.0
    if (
        "scene_transition" in candidate["reason"]
        and "audio_transition" in candidate["reason"]
        and audio_drop >= 0.45
    ):
        branding_sting_end_score = min(
            1.0, 0.15 + 0.65 * audio_drop + 0.20 * post_boundary_support
        )
    raw_score = min(
        1.0,
        float(candidate["confidence"])
        + 0.30 * branding_sting_end_score
        + 0.10 * post_boundary_support,
    )
    reason = str(candidate["reason"])
    if branding_sting_end_score:
        reason += ", branding_sting_end"
    return candidate | {
        "confidence": raw_score,
        "raw_score": raw_score,
        "adjusted_score": raw_score,
        "lateness_penalty": 0.0,
        "post_boundary_support": post_boundary_support,
        "branding_sting_end_score": branding_sting_end_score,
        "reason": reason,
    }


def _outro_scores(timestamp: float, features: EdgeFeatures) -> dict[str, Any]:
    terminal_duration = max(0.001, features.end - timestamp)
    scene_transition = any(abs(item - timestamp) <= 0.3 for item in features.scene_changes)
    before_energy = _mean_energy(features, max(features.start, timestamp - 4), timestamp)
    after_energy = _mean_energy(features, timestamp, min(features.end, timestamp + 5))
    audio_change = abs(after_energy - before_energy) / max(before_energy, after_energy, 0.01)
    audio_transition_score = min(1.0, audio_change / 0.60) if audio_change >= 0.35 else 0.0

    aligned_freeze = any(
        0 <= left - timestamp <= 2.01 and right - left >= 2.0
        for left, right in features.freezes
    )
    inside_freeze = any(left <= timestamp < right for left, right in features.freezes)
    visual_static_score = 1.0 if aligned_freeze or inside_freeze else 0.0

    aligned_black = any(
        left <= timestamp + 1.0 and right > timestamp
        for left, right in features.black_frames
    )
    fade_black_score = 1.0 if aligned_black else 0.0
    terminal_support = max(
        visual_static_score,
        fade_black_score,
        audio_transition_score if terminal_duration >= 3.0 else 0.0,
    )
    position_score = max(
        0.0,
        min(1.0, (features.end - timestamp) / (features.end - features.start)),
    )
    signals = {
        "scene_transition": 1.0 if scene_transition else 0.0,
        "visual_static": visual_static_score,
        "audio_transition": audio_transition_score,
        "fade_black": fade_black_score,
    }
    evidence_count = sum(value >= 0.5 for value in signals.values())
    raw_score = min(
        1.0,
        0.25 * signals["scene_transition"]
        + 0.30 * visual_static_score
        + 0.25 * audio_transition_score
        + 0.40 * fade_black_score
        + 0.20 * terminal_support,
    )
    adjusted_score = min(1.0, raw_score + 0.05 * position_score)
    reasons = [name for name, value in signals.items() if value >= 0.5]
    return {
        "timestamp": timestamp,
        "confidence": adjusted_score,
        "raw_score": raw_score,
        "adjusted_score": adjusted_score,
        "evidence_count": evidence_count,
        "terminal_support": terminal_support,
        "visual_static_score": visual_static_score,
        "audio_transition_score": audio_transition_score,
        "fade_black_score": fade_black_score,
        "lateness_or_position_score": position_score,
        "reason": ", ".join(reasons),
    }


def score_edge(features: EdgeFeatures, *, intro: bool, threshold: float) -> tuple[float | None, float, str, list[dict[str, Any]]]:
    candidate_times = set(features.scene_changes)
    candidate_times.update(right if intro else left for left, right in features.freezes)
    candidate_times.update(right if intro else left for left, right in features.black_frames)
    candidate_times = [
        timestamp for timestamp in sorted(candidate_times)
        if features.start + 0.5 <= timestamp <= features.end - 0.5
    ]
    if intro:
        candidates = [
            _score_candidate(timestamp, features, intro=True)
            for timestamp in candidate_times
        ]
        candidates = [_intro_scores(candidate, features) for candidate in candidates]
        eligible = [
            candidate for candidate in candidates
            if candidate["raw_score"] >= threshold
            and candidate["evidence_count"] >= 2
            and candidate["post_boundary_support"] >= 0.5
        ]
        if eligible:
            earliest = min(candidate["timestamp"] for candidate in eligible)
            for candidate in candidates:
                candidate["lateness_penalty"] = min(
                    0.20, max(0.0, candidate["timestamp"] - earliest) * 0.005
                )
                candidate["adjusted_score"] = max(
                    0.0, candidate["raw_score"] - candidate["lateness_penalty"]
                )
            eligible.sort(
                key=lambda item: (-item["adjusted_score"], item["timestamp"])
            )
            best = eligible[0]
            candidates.sort(key=lambda item: item["timestamp"])
            return (
                float(best["timestamp"]), float(best["adjusted_score"]),
                str(best["reason"]), candidates,
            )
        candidates.sort(key=lambda item: item["timestamp"])
        confidence = max(
            (candidate["adjusted_score"] for candidate in candidates), default=0.0
        )
        return None, float(confidence), "insufficient evidence", candidates
    candidates = [_outro_scores(timestamp, features) for timestamp in candidate_times]
    eligible = [
        candidate for candidate in candidates
        if candidate["raw_score"] >= threshold
        and candidate["evidence_count"] >= 2
        and candidate["terminal_support"] >= 0.5
    ]
    best = min(eligible, key=lambda item: item["timestamp"]) if eligible else None
    candidates.sort(key=lambda item: item["timestamp"])
    if not best:
        confidence = max((item["adjusted_score"] for item in candidates), default=0.0)
        return None, float(confidence), "insufficient evidence", candidates
    return float(best["timestamp"]), float(best["confidence"]), str(best["reason"]), candidates


def detect_content_window(
    video: Path,
    audio: Path,
    duration: float,
    *,
    config: BoundaryConfig | None = None,
    content_start: float | None = None,
    content_end: float | None = None,
    disabled: bool = False,
) -> tuple[ContentWindow, dict[str, Any]]:
    config = config or BoundaryConfig()
    started = time.perf_counter()
    midpoint = duration / 2
    intro_bounds = (0.0, min(config.intro_search_window, duration))
    outro_bounds = (max(midpoint, duration - config.outro_search_window), duration)
    if disabled:
        window = ContentWindow(0.0, duration, 0.0, 0.0, 1.0, 1.0, "disabled", "disabled")
        return window, _debug_report(
            duration, config, window, [], [], time.perf_counter() - started,
            None, None, 0.0, 0.0,
        )

    def inspect(bounds: tuple[float, float]) -> tuple[EdgeFeatures, float]:
        inspected = time.perf_counter()
        try:
            visual = _visual_features(video, *bounds, config)
            features = EdgeFeatures(
                visual.start, visual.end, visual.scene_changes, visual.freezes,
                visual.black_frames, _audio_energy(audio, *bounds),
            )
        except (MediaProcessError, OSError, wave.Error) as exc:
            features = EdgeFeatures(*bounds, error=str(exc))
        return features, time.perf_counter() - inspected

    with ThreadPoolExecutor(max_workers=2) as pool:
        intro_future = pool.submit(inspect, intro_bounds)
        outro_future = pool.submit(inspect, outro_bounds)
        intro_features, intro_boundary_time = intro_future.result()
        outro_features, outro_boundary_time = outro_future.result()
    auto_start, intro_confidence, intro_reason, intro_candidates = score_edge(
        intro_features, intro=True, threshold=config.confidence_threshold
    )
    auto_end, outro_confidence, outro_reason, outro_candidates = score_edge(
        outro_features, intro=False, threshold=config.confidence_threshold
    )
    end = float(content_end) if content_end is not None else (auto_end or duration)
    start = (
        float(content_start)
        if content_start is not None
        else min(auto_start + config.post_intro_trim, math.nextafter(end, 0.0))
        if auto_start is not None
        else 0.0
    )
    if content_start is not None:
        intro_confidence, intro_reason = 1.0, "manual override"
    if content_end is not None:
        outro_confidence, outro_reason = 1.0, "manual override"
    if not 0 <= start < end <= duration:
        raise ValueError("content overrides must satisfy 0 <= start < end <= duration")
    window = ContentWindow(
        start, end, start, duration - end, intro_confidence, outro_confidence,
        intro_reason, outro_reason,
    )
    debug = _debug_report(
        duration, config, window, intro_candidates, outro_candidates,
        time.perf_counter() - started, auto_start, auto_end,
        intro_boundary_time, outro_boundary_time,
    )
    debug["intro_features"] = asdict(intro_features)
    debug["outro_features"] = asdict(outro_features)
    return window, debug


def _debug_report(
    duration: float,
    config: BoundaryConfig,
    window: ContentWindow,
    intro_candidates: list[dict[str, Any]],
    outro_candidates: list[dict[str, Any]],
    elapsed: float,
    detected_intro_boundary: float | None,
    detected_outro_boundary: float | None,
    intro_boundary_time: float,
    outro_boundary_time: float,
) -> dict[str, Any]:
    return {
        "video_duration": duration,
        "intro_search_window": config.intro_search_window,
        "outro_search_window": config.outro_search_window,
        "content_start": window.start,
        "final_content_start": window.start,
        "detected_intro_boundary": detected_intro_boundary,
        "detected_outro_boundary": detected_outro_boundary,
        "post_intro_trim": config.post_intro_trim,
        "content_end": window.end,
        "final_content_end": window.end,
        "intro_removed": window.intro_removed,
        "outro_removed": window.outro_removed,
        "intro_confidence": window.intro_confidence,
        "outro_confidence": window.outro_confidence,
        "intro_candidates": intro_candidates,
        "outro_candidates": outro_candidates,
        "intro_reason": window.intro_reason,
        "outro_reason": window.outro_reason,
        "boundary_analysis_time": elapsed,
        "intro_boundary_time": intro_boundary_time,
        "outro_boundary_time": outro_boundary_time,
    }


def slice_analysis_wav(source: Path, destination: Path, start: float, end: float) -> Path:
    with wave.open(str(source), "rb") as reader, wave.open(str(destination), "wb") as writer:
        writer.setparams(reader.getparams())
        rate = reader.getframerate()
        reader.setpos(min(reader.getnframes(), int(start * rate)))
        writer.writeframes(reader.readframes(max(0, int((end - start) * rate))))
    return destination
