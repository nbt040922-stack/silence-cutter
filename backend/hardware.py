from __future__ import annotations

import argparse
import ctypes
import gc
import hashlib
import json
import os
import platform
import subprocess
import time
from pathlib import Path
from typing import Any

from silence_cutter.runtime_paths import bundled_path, find_executable


ROOT = Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    configured = os.environ.get("SILENCE_CUTTER_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return (ROOT / "workspace").resolve()


def _write_json(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)
    return path


def _cpu_model() -> str:
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    return platform.processor() or platform.machine() or "unknown"


def _ram_bytes() -> int:
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.total_physical)
    return 0


def _nvidia_gpus() -> list[dict[str, Any]]:
    executable = find_executable("nvidia-smi")
    if not executable:
        return []
    completed = subprocess.run(
        [
            executable,
            "--query-gpu=name,memory.total",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode:
        return []
    gpus = []
    for line in completed.stdout.splitlines():
        try:
            name, memory = (item.strip() for item in line.rsplit(",", 1))
            gpus.append({"model": name, "vram_mib": int(memory)})
        except (TypeError, ValueError):
            continue
    return gpus


def _torch_cuda() -> dict[str, Any]:
    try:
        import torch

        available = bool(torch.cuda.is_available())
        return {
            "available": available,
            "torch_version": torch.__version__,
            "cuda_runtime": torch.version.cuda,
            "device_count": torch.cuda.device_count() if available else 0,
            "device": torch.cuda.get_device_name(0) if available else None,
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def _nvenc_initializes() -> tuple[bool, str]:
    ffmpeg = find_executable("ffmpeg")
    if not ffmpeg:
        return False, "ffmpeg not found"
    completed = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
            "-f", "lavfi", "-i", "color=size=256x256:rate=1:duration=1",
            "-frames:v", "1", "-an", "-c:v", "h264_nvenc",
            "-preset", "p4", "-cq", "23", "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    detail = (completed.stderr or completed.stdout).strip()
    return completed.returncode == 0, detail[-1000:]


def probe_hardware(*, load_sensevoice: bool = True) -> dict[str, Any]:
    started = time.perf_counter()
    ram_bytes = _ram_bytes()
    gpus = _nvidia_gpus()
    cuda = _torch_cuda()
    nvenc, nvenc_error = _nvenc_initializes()
    sensevoice = {
        "requested_device": "cuda:0",
        "active_device": None,
        "cuda_model_load_succeeds": False,
        "cpu_fallback": False,
        "error": "not tested",
    }
    if load_sensevoice:
        try:
            from speech_detector.config import HighRecallConfig
            from speech_detector.sensevoice_detector import SenseVoiceDetector

            detector = SenseVoiceDetector(HighRecallConfig())
            detector._load()
            sensevoice.update(
                active_device=detector.active_device,
                cuda_model_load_succeeds=detector.active_device.startswith("cuda"),
                cpu_fallback=detector.cuda_fallback,
                error=detector.cuda_error or "",
                model_load_time=detector.model_load_time,
            )
            del detector
            gc.collect()
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
        except Exception as exc:
            sensevoice["error"] = str(exc)
    return {
        "schema_version": 1,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cpu": {"model": _cpu_model()},
        "ram": {"bytes": ram_bytes, "gib": round(ram_bytes / 2**30, 2)},
        "nvidia_detected": bool(gpus),
        "gpus": gpus,
        "cuda": cuda,
        "sensevoice": sensevoice,
        "nvenc": {"initializes": nvenc, "error": "" if nvenc else nvenc_error},
        "component_profile": {
            "sensevoice": "CUDA" if sensevoice["cuda_model_load_succeeds"] else "CPU_FALLBACK",
            "renderer": "h264_nvenc" if nvenc else "libx264",
        },
        "full_gpu_production": bool(
            gpus and cuda.get("available")
            and sensevoice["cuda_model_load_succeeds"] and nvenc
        ),
        "probe_time": time.perf_counter() - started,
    }


def write_startup_probe() -> dict[str, Any]:
    report = probe_hardware(load_sensevoice=True)
    path = _write_json(data_dir() / "hardware_probe.json", report)
    return {**report, "path": str(path)}


def _benchmark_source() -> Path:
    bundled = bundled_path("benchmark", "hardware_benchmark.mp4")
    local = ROOT / "release_assets" / "hardware_benchmark.mp4"
    source = bundled or local
    if not source.is_file():
        raise FileNotFoundError(f"fixed hardware benchmark video not found: {source}")
    return source


def _rounded(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, list):
        return [_rounded(item) for item in value]
    if isinstance(value, dict):
        return {key: _rounded(value[key]) for key in sorted(value)}
    return value


def timeline_identity(report: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    debug = report.get("debug") or {}
    identity = _rounded({
        "intro_boundary": report.get("detected_intro_boundary"),
        "outro_boundary": report.get("detected_outro_boundary"),
        "silero_intervals": debug.get("silero_intervals", []),
        "sensevoice_intervals": debug.get("sensevoice_intervals", []),
        "keep_intervals": debug.get("keep_intervals", []),
        "cut_intervals": debug.get("cut_intervals", []),
    })
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest(), identity


def _classification(total_time: float, input_duration: float) -> str:
    realtime_ratio = total_time / input_duration
    if realtime_ratio <= 0.5:
        return "FAST"
    if realtime_ratio <= 1.0:
        return "STANDARD"
    return "SLOW"


def run_hardware_benchmark() -> dict[str, Any]:
    from production.pipeline import ProductionRuntime

    source = _benchmark_source().resolve()
    folder = data_dir() / "hardware-benchmark"
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / "hardware_benchmark_output.mp4"
    pipeline_report = folder / "hardware_benchmark.pipeline.json"
    output.unlink(missing_ok=True)
    pipeline_report.unlink(missing_ok=True)

    probe_path = data_dir() / "hardware_probe.json"
    hardware = (
        json.loads(probe_path.read_text(encoding="utf-8"))
        if probe_path.is_file() else probe_hardware(load_sensevoice=False)
    )
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
    except Exception:
        torch = None

    result = ProductionRuntime().process(
        source, output, debug=True, report_path=pipeline_report
    )
    timeline_hash, identity = timeline_identity(result)
    peak_mib = 0.0
    if torch is not None and torch.cuda.is_available():
        peak_mib = torch.cuda.max_memory_allocated() / 2**20
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    benchmark = {
        "schema_version": 1,
        "hardware": hardware,
        "execution": {
            "sensevoice_requested_device": result.get("sensevoice_requested_device"),
            "sensevoice_active_device": result.get("sensevoice_active_device"),
            "sensevoice_cuda_fallback": result.get("sensevoice_cuda_fallback", False),
            "renderer": ((result.get("debug") or {}).get("render") or {}).get("codec"),
        },
        "performance_class": _classification(
            float(result["total_time"]), float(result["input_duration"])
        ),
        "benchmark_video": str(source),
        "benchmark_video_sha256": source_hash,
        "input_duration": result["input_duration"],
        "output_duration": result["output_duration"],
        "audio_extraction_time": result["audio_extraction_time"],
        "intro_outro_time": result["boundary_analysis_time"],
        "intro_time": result["intro_boundary_time"],
        "outro_time": result["outro_boundary_time"],
        "silero_time": result["silero_time"],
        "sensevoice_time": result["sensevoice_inference_time"],
        "render_time": result["render_time"],
        "total_time": result["total_time"],
        "vram_peak_mib": round(peak_mib, 1),
        "timeline_hash": timeline_hash,
        "timeline_identity": identity,
        "output_path": str(output),
    }
    path = _write_json(folder / "hardware_benchmark.json", benchmark)
    return {**benchmark, "report_path": str(path)}


def compare_reports(paths: list[Path]) -> dict[str, Any]:
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    hashes = {item.get("timeline_hash") for item in reports}
    videos = {item.get("benchmark_video_sha256") for item in reports}
    return {
        "timeline_comparison": "PASS" if len(hashes) == 1 and len(videos) == 1 else "FAIL",
        "same_benchmark_video": len(videos) == 1,
        "timeline_hashes": sorted(str(value) for value in hashes),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Silence Cutter hardware tools")
    parser.add_argument("operation", choices=("probe", "benchmark", "compare"))
    parser.add_argument("reports", nargs="*", type=Path)
    args = parser.parse_args()
    if args.operation == "probe":
        result = write_startup_probe()
    elif args.operation == "benchmark":
        result = run_hardware_benchmark()
    else:
        if len(args.reports) < 2:
            parser.error("compare requires at least two hardware_benchmark.json files")
        result = compare_reports(args.reports)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
