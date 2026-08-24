from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from semantic_cleaner.cleaner import subtract_intervals

from .models import BrandScanResult


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run_brand_scan(
    source: str | Path, report: str | Path, artifact: str | Path, detector: Any,
) -> dict[str, Any]:
    source_path = Path(source).expanduser().resolve()
    report_path = Path(report).expanduser().resolve()
    artifact_path = Path(artifact).expanduser().resolve()
    report_data = json.loads(report_path.read_text(encoding="utf-8"))
    duration = float(report_data["input_duration"])
    result: BrandScanResult = detector.scan(source_path, duration)
    payload = result.to_dict()
    _write_json(artifact_path, payload)
    report_data["brand_scan_status"] = result.status
    report_data["brand_scan_artifact"] = str(artifact_path)
    report_data["brand_scan_reason"] = result.reason
    report_data["brand_cut_intervals"] = result.cut_intervals
    report_data["brand_removed_duration"] = float(payload["removed_duration"])
    report_data["brand_scan_time"] = result.total_scan_time
    if result.status == "APPLIED":
        keep = report_data.get("keep_intervals") or (report_data.get("debug") or {}).get("keep_intervals") or []
        final_keep = subtract_intervals(keep, result.cut_intervals)
        report_data["keep_intervals"] = final_keep
        report_data["expected_output_duration"] = sum(item["end"] - item["start"] for item in final_keep)
        report_data["keep_duration"] = report_data["expected_output_duration"]
        report_data["cut_segments"] = [
            *report_data.get("cut_segments", []),
            *[item | {"reason": "brand_ad"} for item in result.cut_intervals],
        ]
        report_data["total_removed_duration"] = float(report_data.get("total_removed_duration") or 0.0) + float(payload["removed_duration"])
        report_data["cut_duration"] = float(report_data.get("cut_duration") or 0.0) + float(payload["removed_duration"])
        report_data["removed_percentage"] = report_data["total_removed_duration"] / duration * 100
    _write_json(report_path, report_data)
    return payload
