from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


ALLOWED_TYPES = {"PERSONAL_BRAND", "SPONSOR", "QR", "ADVERTISEMENT"}


@dataclass(frozen=True)
class Detection:
    type: str
    start: float
    end: float
    confidence: float
    detectors: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""

    def __post_init__(self) -> None:
        label = self.type.upper()
        if label not in ALLOWED_TYPES:
            raise ValueError(f"unsupported detection type: {self.type}")
        if not all(math.isfinite(float(value)) for value in (self.start, self.end, self.confidence)):
            raise ValueError("detection values must be finite")
        if self.start < 0 or self.end <= self.start:
            raise ValueError("detection interval must be positive and non-negative")
        if not 0 <= self.confidence <= 1:
            raise ValueError("detection confidence must be between 0 and 1")
        object.__setattr__(self, "type", label)
        unique_detectors = tuple(dict.fromkeys(str(item) for item in self.detectors))
        object.__setattr__(self, "detectors", unique_detectors)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type, "start": self.start, "end": self.end,
            "confidence": self.confidence, "detectors": list(self.detectors),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class BrandScanResult:
    status: str
    detections: list[Detection]
    cut_intervals: list[dict[str, float]]
    frame_extraction_time: float = 0.0
    coarse_inference_time: float = 0.0
    fine_inference_time: float = 0.0
    qr_time: float = 0.0
    total_scan_time: float = 0.0
    qwen_generation_count: int = 0
    reason: str | None = None
    config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        removed = sum(item["end"] - item["start"] for item in self.cut_intervals)
        return {
            "status": self.status, "scan_version": "1",
            "recall_first": bool(self.config.get("recall_first", True)),
            "config": self.config,
            "detections": [item.to_dict() for item in self.detections],
            "cut_intervals": self.cut_intervals, "removed_duration": removed,
            "frame_extraction_time": self.frame_extraction_time,
            "coarse_inference_time": self.coarse_inference_time,
            "fine_inference_time": self.fine_inference_time, "qr_time": self.qr_time,
            "total_scan_time": self.total_scan_time,
            "qwen_generation_count": self.qwen_generation_count,
            "reason": self.reason,
        }
