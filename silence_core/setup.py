from __future__ import annotations

import argparse
import json
import os
import sys
import subprocess
import time
import urllib.request
from pathlib import Path

from .launcher import check_model
from .runtime_paths import CorePaths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="silence-core-setup")
    parser.add_argument("action", choices=("install", "check-model", "repair-model", "update-model", "stop"))
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args(argv)
    if getattr(sys, "frozen", False):
        os.environ.setdefault("SILENCE_CORE_PACKAGED", "1")
        os.environ.setdefault("SILENCE_CORE_INSTALL_ROOT", str(packaged_install_root(Path(sys.executable))))
        os.environ.setdefault(
            "SILENCE_CORE_DATA_ROOT",
            str(Path(os.environ.get("PROGRAMDATA", Path.home())) / "ContentOps" / "SilenceCore"),
        )
    paths = CorePaths.from_environment()
    paths.ensure_data_layout()
    manifest = args.manifest or Path(os.getenv("SILENCE_CORE_MANIFEST", Path(__file__).resolve().parents[1] / "installer" / "core_model_manifest.json"))
    if args.action in {"install", "check-model", "repair-model", "update-model"}:
        result = check_model(paths, manifest)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get("status") == "FAILED":
            return 1
        if args.action == "install":
            return _start_core(paths)
        return 0
    if args.action == "stop":
        return _stop_core(paths)
    return 1


def packaged_install_root(executable: Path) -> Path:
    """Resolve the app root when setup is installed in its own subdirectory."""
    parent = executable.resolve().parent
    return parent.parent if parent.name.lower() == "silence_core_setup" else parent


def _health_ready(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
            return json.loads(response.read().decode("utf-8")).get("status") == "READY"
    except Exception:
        return False


def _start_core(paths: CorePaths) -> int:
    if all(_health_ready(port) for port in (8792, 8791, 8780)):
        return 0
    supervisor = paths.install_root / "supervisor" / "supervisor.exe"
    if not supervisor.is_file():
        # Development fallback; packaged installers always include supervisor.exe.
        command = [sys.executable, "-m", "silence_core.entry_supervisor"]
    else:
        command = [str(supervisor)]
    environment = os.environ.copy()
    environment.update({
        "SILENCE_CORE_PACKAGED": "1" if supervisor.is_file() else environment.get("SILENCE_CORE_PACKAGED", "0"),
        "SILENCE_CORE_INSTALL_ROOT": str(paths.install_root),
        "SILENCE_CORE_DATA_ROOT": str(paths.data_root),
        "SEMANTIC_QWEN_MODEL": str(paths.model_path),
    })
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(command, cwd=str(paths.install_root), env=environment, creationflags=flags,
                     close_fds=(os.name != "nt"))
    deadline = time.monotonic() + 240.0
    while time.monotonic() < deadline:
        if all(_health_ready(port) for port in (8792, 8791, 8780)):
            return 0
        time.sleep(1.0)
    return 1


def _stop_core(paths: CorePaths) -> int:
    for port in (8780, 8791, 8792):
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{port}/shutdown", method="POST")
            urllib.request.urlopen(request, timeout=2).read()
        except Exception:
            pass
    identity = paths.state_root / "supervisor.json"
    try:
        payload = json.loads(identity.read_text(encoding="utf-8"))
        pid = int(payload.get("supervisor_pid") or 0)
        if pid and pid != os.getpid():
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
