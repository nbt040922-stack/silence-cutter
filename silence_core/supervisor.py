from __future__ import annotations

import json
import os
import subprocess
import time
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .runtime_paths import CorePaths


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    port: int
    command: list[str]


@dataclass(frozen=True)
class StartupResult:
    ready: bool
    failed_component: str | None = None
    reason: str | None = None


class CoreSupervisor:
    def __init__(
        self,
        *,
        paths: CorePaths | None = None,
        data_root: Path | None = None,
        popen: Callable[..., Any] | None = None,
        health_probe: Callable[[ServiceSpec], str] | None = None,
        services: list[ServiceSpec] | None = None,
        health_timeout: float = 180.0,
    ):
        self.paths = paths or CorePaths.from_environment()
        self.data_root = (data_root or self.paths.data_root).resolve()
        self._popen = popen or subprocess.Popen
        self._health_probe = health_probe or self._probe_health
        self.services = services or self._default_services()
        self.health_timeout = health_timeout
        self.processes: dict[str, Any] = {}
        self.identity_path = self.data_root / "state" / "supervisor.json"

    def start(self) -> StartupResult:
        self.data_root.mkdir(parents=True, exist_ok=True)
        first_failure: tuple[str, str] | None = None
        # Start every endpoint first.  Scheduler/LAN can expose health while
        # Qwen warms; job admission remains gated by the bridge's Qwen check.
        for spec in self.services:
            try:
                process = self._start_one(spec)
                self.processes[spec.name] = process
            except Exception as exc:
                if first_failure is None:
                    first_failure = (spec.name, f"{type(exc).__name__}: {exc}")
        for spec in self.services:
            if spec.name not in self.processes:
                continue
            try:
                state = self._wait_ready(spec)
                if state != "READY":
                    if first_failure is None:
                        first_failure = (spec.name, f"health={state}")
            except Exception as exc:
                if first_failure is None:
                    first_failure = (spec.name, f"{type(exc).__name__}: {exc}")
        self._write_identity()
        if first_failure:
            return StartupResult(False, first_failure[0], first_failure[1])
        return StartupResult(True)

    def watch_once(self) -> list[dict[str, Any]]:
        events = []
        for spec in self.services:
            process = self.processes.get(spec.name)
            if process is None or process.poll() is None:
                continue
            code = process.poll()
            replacement = self._start_one(spec)
            self.processes[spec.name] = replacement
            events.append({"component": spec.name, "exit_code": code, "restarted": True})
        if events:
            self._write_identity()
        return events

    def stop_owned(self) -> None:
        for process in self.processes.values():
            if process.poll() is None:
                process.terminate()
        self.processes.clear()
        self._write_identity()

    def _start_one(self, spec: ServiceSpec) -> Any:
        log_dir = self.data_root / "logs" / spec.name
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout = (log_dir / "stdout.log").open("ab")
        stderr = (log_dir / "stderr.log").open("ab")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        environment = os.environ.copy()
        tools = self.paths.install_root / "tools"
        if tools.is_dir():
            environment["PATH"] = str(tools) + os.pathsep + environment.get("PATH", "")
        environment.update({
            "SILENCE_CORE_PACKAGED": "1",
            "SILENCE_CORE_INSTALL_ROOT": str(self.paths.install_root),
            "SILENCE_CORE_DATA_ROOT": str(self.paths.data_root),
                "SILENCE_CUTTER_RESOURCE_DIR": str(self.paths.install_root),
                "SILENCE_CUTTER_DATA_DIR": str(self.paths.data_root),
                "SILENCE_CUTTER_OUTPUT_DIR": str(self.paths.data_root / "workspace" / "outputs"),
            "SILENCE_QWEN_ENDPOINT": "http://127.0.0.1:8792",
                "SEMANTIC_QWEN_MODEL": str(self.paths.model_path),
        })
        try:
            return self._popen(
                spec.command,
                cwd=str(self.paths.install_root),
                stdout=stdout,
                stderr=stderr,
                env=environment,
                creationflags=flags,
            )
        finally:
            stdout.close()
            stderr.close()

    def _write_identity(self) -> None:
        self.identity_path.parent.mkdir(parents=True, exist_ok=True)
        value = {
            "supervisor_pid": os.getpid(),
            "pid_by_component": {
                name: getattr(process, "pid", None) for name, process in self.processes.items()
            },
            "updated_at": time.time(),
        }
        temporary = self.identity_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.identity_path)

    def _probe_health(self, spec: ServiceSpec) -> str:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{spec.port}/health", timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("status") != "READY":
                return str(payload.get("status") or "NOT_READY")
            if spec.name == "qwen" and not (payload.get("model_loaded") and payload.get("warmed_up")):
                return "NOT_READY"
            return "READY"
        except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError):
            return "NOT_READY"

    def _wait_ready(self, spec: ServiceSpec) -> str:
        deadline = time.monotonic() + self.health_timeout
        last = "NOT_READY"
        while time.monotonic() < deadline:
            last = self._health_probe(spec)
            if last == "READY":
                return last
            if last in {"FAILED", "ERROR"} or last.startswith("EXITED:"):
                return last
            process = self.processes.get(spec.name)
            if process is not None and process.poll() is not None:
                return f"EXITED:{process.poll()}"
            time.sleep(1.0)
        return last

    def _default_services(self) -> list[ServiceSpec]:
        if os.getenv("SILENCE_CORE_PACKAGED", "") == "1":
            return [
                ServiceSpec("qwen", 8792, [str(self.paths.install_root / "qwen" / "qwen.exe")]),
                ServiceSpec("scheduler", 8791, [str(self.paths.install_root / "scheduler" / "scheduler.exe")]),
                ServiceSpec("lan", 8780, [str(self.paths.install_root / "lan" / "lan.exe")]),
            ]
        python = sys.executable
        return [
            ServiceSpec("qwen", 8792, [str(python), "-m", "qwen_worker.server"]),
            ServiceSpec("scheduler", 8791, [str(python), "-m", "contentops_process_bridge"]),
            ServiceSpec("lan", 8780, [str(python), "-m", "lan_job_api"]),
        ]
