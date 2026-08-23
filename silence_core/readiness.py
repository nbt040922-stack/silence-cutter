from __future__ import annotations

import json
import os
import shutil
import socket
from pathlib import Path
from typing import Any


CORE_PORTS = (8780, 8791, 8792)


def find_port_owner(port: int) -> dict[str, Any]:
    """Return a conservative socket-level result without killing processes."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.2)
    try:
        occupied = sock.connect_ex(("127.0.0.1", port)) == 0
    finally:
        sock.close()
    return {"status": "FAIL" if occupied else "PASS", "reason": "PORT_OCCUPIED" if occupied else None}


def readiness_report(
    *,
    install_root: Path,
    model_ready: bool,
    gpu: dict[str, Any] | None = None,
    disk: dict[str, Any] | None = None,
    ports: dict[int, dict[str, Any]] | None = None,
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gpu = gpu or {"status": "WARN", "reason": "GPU_NOT_PROBED"}
    disk = disk or _disk_report(install_root)
    ports = ports or {port: find_port_owner(port) for port in CORE_PORTS}
    runtime = runtime or _runtime_report(install_root)
    checks = {
        "windows_x64": {"status": "PASS" if os.name == "nt" else "WARN"},
        "gpu": gpu,
        "disk": disk,
        "model": {"status": "PASS" if model_ready else "FAIL", "ready": model_ready},
        "ports": ports,
        "runtime": runtime,
    }
    failures = []
    for name, value in checks.items():
        if name == "ports":
            failures.extend(str(p) for p, detail in value.items() if detail.get("status") == "FAIL")
        elif value.get("status") == "FAIL":
            failures.append(name)
    return {"status": "FAIL" if failures else "PASS", "checks": checks, "failures": failures}


def require_startup_ready(report: dict[str, Any]) -> None:
    if report.get("status") != "PASS":
        details = json.dumps(
            {"failures": report.get("failures"), "checks": report.get("checks")},
            ensure_ascii=False,
        )
        raise RuntimeError(f"SILENCE CORE STARTUP FAILED: {details}")


def _disk_report(root: Path) -> dict[str, Any]:
    try:
        free = shutil.disk_usage(root).free
    except OSError as exc:
        return {"status": "FAIL", "reason": str(exc), "free_bytes": 0}
    return {"status": "PASS", "free_bytes": free}


def _runtime_report(root: Path) -> dict[str, Any]:
    ffmpeg = root / "tools" / "ffmpeg.exe"
    ffprobe = root / "tools" / "ffprobe.exe"
    return {
        "status": "PASS" if ffmpeg.is_file() and ffprobe.is_file() else "FAIL",
        "ffmpeg": "PASS" if ffmpeg.is_file() else "MISSING",
        "ffprobe": "PASS" if ffprobe.is_file() else "MISSING",
    }
