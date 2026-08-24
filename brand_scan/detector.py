from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from semantic_cleaner.qwen import _contact_sheets, _extract_sampled_frames

from .evidence import combine_detector_evidence, group_frame_evidence
from .intervals import expand_temporal_evidence, merge_intervals
from .models import BrandScanResult, Detection


BRAND_PROMPT = """Visual brand/ad scan. Every image has absolute SOURCE TIME. Output ONLY CSV lines TYPE,START,END,CONFIDENCE,REASON. Allowed TYPE: PERSONAL_BRAND, SPONSOR, QR, ADVERTISEMENT. Use NONE if no visual brand, sponsor, QR or commercial advertisement is present. Prefer recall when a logo, persistent watermark, QR, sponsor banner, discount, product offer or promotional visual is visible. Do not classify ordinary content as advertising without visual evidence."""


@dataclass(frozen=True)
class BrandScanConfig:
    coarse_interval: float = 10.0
    fine_interval: float = 2.0
    temporal_padding: float = 0.25
    min_consecutive_frames: int = 2
    recall_first: bool = True
    candidate_context: float = 12.0
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_environment(cls) -> "BrandScanConfig":
        return cls(
            coarse_interval=float(os.getenv("BRAND_SCAN_COARSE_INTERVAL", "10")),
            fine_interval=float(os.getenv("BRAND_SCAN_FINE_INTERVAL", "2")),
            temporal_padding=float(os.getenv("BRAND_SCAN_TEMPORAL_PADDING", "0.25")),
            min_consecutive_frames=int(os.getenv("BRAND_SCAN_MIN_CONSECUTIVE", "2")),
            recall_first=os.getenv("BRAND_SCAN_RECALL_FIRST", "1").lower() not in {"0", "false", "no"},
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "coarse_interval": self.coarse_interval, "fine_interval": self.fine_interval,
            "temporal_padding": self.temporal_padding,
            "min_consecutive_frames": self.min_consecutive_frames,
            "recall_first": self.recall_first,
        }


def _parse_response(text: str) -> list[Detection]:
    if text.strip().upper() == "NONE":
        return []
    result: list[Detection] = []
    for line in text.splitlines():
        match = re.match(r"\s*(PERSONAL_BRAND|SPONSOR|QR|ADVERTISEMENT)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*(0(?:\.\d+)?|1(?:\.0+)?)\s*(?:,\s*(.*))?$", line, re.I)
        if match:
            label, start, end, confidence, reason = match.groups()
            try:
                result.append(Detection(label, float(start), float(end), float(confidence), ("qwen",), reason or "Qwen visual evidence"))
            except ValueError:
                continue
    if result:
        return result
    try:
        value = json.loads(text)
        values = value if isinstance(value, list) else value.get("detections", value.get("segments", []))
        for item in values:
            result.append(Detection(
                str(item["type"]), float(item["start"]), float(item["end"]),
                float(item["confidence"]), ("qwen",), str(item.get("reason") or "Qwen visual evidence"),
            ))
    except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return []
    return result


def _bounded_detections(detections: list[Detection], duration: float) -> list[Detection]:
    bounded = []
    for item in detections:
        start = max(0.0, min(duration, item.start))
        end = max(0.0, min(duration, item.end))
        if end > start:
            bounded.append(Detection(item.type, start, end, item.confidence, item.detectors, item.reason))
    return bounded


class BrandScanDetector:
    def __init__(self, detector: Any, qr_detector: Callable[[Image.Image], bool], config: BrandScanConfig | None = None) -> None:
        self.detector = detector
        self.qr_detector = qr_detector
        self.config = config or BrandScanConfig.from_environment()

    def _qwen(self, images: list[Image.Image]) -> list[Detection]:
        if not images:
            return []
        return _parse_response(self.detector.generate_text(images, BRAND_PROMPT, task="brand_scan"))

    def scan(self, source: Path, duration: float) -> BrandScanResult:
        started = time.perf_counter()
        extraction_time = qr_time = coarse_time = fine_time = 0.0
        generation_before = int(getattr(self.detector, "generation_count", 0))
        try:
            root = source.parent / ".brand_scan_cache"
            extraction_started = time.perf_counter()
            coarse_paths, coarse_times = _extract_sampled_frames(
                source, 0.0, duration, self.config.coarse_interval, root / "coarse",
            )
            extraction_time += time.perf_counter() - extraction_started
            qr_started = time.perf_counter()
            coarse_qr = []
            for path, timestamp in zip(coarse_paths, coarse_times, strict=True):
                with Image.open(path) as image:
                    if self.qr_detector(image.convert("RGB")):
                        coarse_qr.append({"timestamp": timestamp, "type": "QR", "confidence": 1.0, "reason": "QR detector"})
            qr_time += time.perf_counter() - qr_started
            coarse_sheets = _contact_sheets(coarse_paths, coarse_times)
            try:
                coarse_started = time.perf_counter()
                coarse_qwen = self._qwen(coarse_sheets)
                coarse_time = time.perf_counter() - coarse_started
            finally:
                for sheet in coarse_sheets:
                    sheet.close()
            coarse_qr_detections = group_frame_evidence(
                coarse_qr, duration, self.config.min_consecutive_frames, self.config.temporal_padding,
            )
            candidates = [{"start": max(0.0, item.start - self.config.candidate_context), "end": min(duration, item.end + self.config.candidate_context)} for item in [*coarse_qwen, *coarse_qr_detections]]
            candidates = merge_intervals(candidates, 0.0, duration)
            fine_qwen: list[Detection] = []
            fine_qr_frames = []
            for index, candidate in enumerate(candidates):
                extraction_started = time.perf_counter()
                paths, times = _extract_sampled_frames(source, candidate["start"], candidate["end"], self.config.fine_interval, root / f"fine-{index:03d}")
                extraction_time += time.perf_counter() - extraction_started
                qr_started = time.perf_counter()
                for path, timestamp in zip(paths, times, strict=True):
                    with Image.open(path) as image:
                        if self.qr_detector(image.convert("RGB")):
                            fine_qr_frames.append({"timestamp": timestamp, "type": "QR", "confidence": 1.0, "reason": "QR detector"})
                qr_time += time.perf_counter() - qr_started
                sheets = _contact_sheets(paths, times)
                try:
                    fine_started = time.perf_counter()
                    fine_qwen.extend(self._qwen(sheets))
                    fine_time += time.perf_counter() - fine_started
                finally:
                    for sheet in sheets:
                        sheet.close()
            qr_detections = group_frame_evidence(fine_qr_frames, duration, self.config.min_consecutive_frames, self.config.temporal_padding)
            detections = combine_detector_evidence(
                _bounded_detections([*coarse_qwen, *fine_qwen, *coarse_qr_detections], duration),
                _bounded_detections(qr_detections, duration), duration,
            )
            detections = expand_temporal_evidence(detections, duration, self.config.temporal_padding, self.config.temporal_padding)
            cuts = merge_intervals([item.to_dict() for item in detections], 0.0, duration)
            status = "APPLIED" if detections else "NO_CANDIDATES"
            reason = None
        except Exception as exc:
            return BrandScanResult(
                "BRAND_SCAN_INCOMPLETE", [], [], extraction_time, coarse_time, fine_time, qr_time,
                time.perf_counter() - started, int(getattr(self.detector, "generation_count", 0)) - generation_before,
                f"{type(exc).__name__}: {exc}", self.config.as_dict(),
            )
        return BrandScanResult(
            status, detections, cuts, extraction_time, coarse_time, fine_time, qr_time,
            time.perf_counter() - started, int(getattr(self.detector, "generation_count", 0)) - generation_before,
            reason, self.config.as_dict(),
        )
