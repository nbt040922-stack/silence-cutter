from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from .model_manager import ModelManager, ModelManifest
from .readiness import require_startup_ready, readiness_report
from .runtime_paths import CorePaths
from .supervisor import CoreSupervisor


def _health(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=3) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_ready(url: str, timeout: float = 180.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        try:
            last = _health(url)
            if last.get("status") == "READY":
                return last
        except Exception as exc:
            last = {"status": "FAILED", "error": str(exc)}
        time.sleep(1)
    raise RuntimeError(f"service did not become READY: {url}: {last}")


def check_model(paths: CorePaths, manifest_path: Path) -> dict:
    manifest = ModelManifest.from_file(manifest_path)
    manager = ModelManager(paths.model_path, paths.log_root / "installer" / "model.log")
    result = manager.ensure_model(manifest)
    return result.__dict__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="silence-core")
    parser.add_argument("command", choices=("status", "check-model", "repair-model", "start"))
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args(argv)
    paths = CorePaths.from_environment()
    paths.ensure_data_layout()
    if args.command in {"check-model", "repair-model"}:
        if not args.manifest:
            raise SystemExit("--manifest is required")
        print(json.dumps(check_model(paths, args.manifest), ensure_ascii=False, indent=2))
        return 0
    if args.command == "status":
        result = {
            "qwen": _safe_health("http://127.0.0.1:8792/health"),
            "scheduler": _safe_health("http://127.0.0.1:8791/health"),
            "lan": _safe_health("http://127.0.0.1:8780/health"),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if all(item.get("status") == "READY" for item in result.values()) else 1
    if args.command == "start":
        result = CoreSupervisor(paths=paths).start()
        if not result.ready:
            print(json.dumps(result.__dict__, ensure_ascii=False))
            return 1
        for url in ("http://127.0.0.1:8792/health", "http://127.0.0.1:8791/health", "http://127.0.0.1:8780/health"):
            wait_ready(url)
        return 0
    return 0


def _safe_health(url: str) -> dict:
    try:
        return _health(url)
    except Exception as exc:
        return {"status": "FAILED", "error": str(exc)}


if __name__ == "__main__":
    raise SystemExit(main())
