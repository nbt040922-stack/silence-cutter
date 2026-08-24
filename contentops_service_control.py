"""Loopback-only, ownership-safe controls for the local ContentOps services."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
YT_ROOT = Path(os.environ.get("YT_NOTIFI_ROOT", r"D:\yt_notifi"))
YTDOWNLOAD_ROOT = Path(os.environ.get("YTDOWNLOAD_ROOT", r"D:\YTDOWNLOAD"))
SILENCE_ROOT = Path(os.environ.get("SILENCE_CUTTER_ROOT", str(ROOT)))
SILENCE_PYTHON = SILENCE_ROOT / ".venv_asr_test" / "Scripts" / "python.exe"
YT_PYTHON = YT_ROOT / ".venv" / "Scripts" / "python.exe"
ELECTRON = YTDOWNLOAD_ROOT / "node_modules" / "electron" / "dist" / "electron.exe"
MODEL = Path(os.environ.get("SEMANTIC_QWEN_MODEL", str(SILENCE_ROOT / "local_models" / "Qwen2.5-VL-7B-Instruct-AWQ")))


@dataclass(frozen=True)
class ServiceDefinition:
    name: str
    port: int
    health_path: str
    executable: str
    args: tuple[str, ...]
    cwd: str
    marker: str
    env: dict[str, str]

    @property
    def health_url(self) -> str:
        return f"http://127.0.0.1:{self.port}{self.health_path}"


SERVICE_DEFINITIONS = {
    "YT_NOTIFI": ServiceDefinition("YT_NOTIFI", 8787, "/health", str(YT_PYTHON), ("-m", "uvicorn", "app.main:app", "--app-dir", str(YT_ROOT), "--host", "127.0.0.1", "--port", "8787"), str(YT_ROOT), "uvicorn app.main:app", {}),
    "YTDOWNLOAD": ServiceDefinition("YTDOWNLOAD", 8790, "/health", str(ELECTRON), (".",), str(YTDOWNLOAD_ROOT), "electron.exe", {"CONTENTOPS_HEADLESS": "1"}),
    "Silence Scheduler": ServiceDefinition("Silence Scheduler", 8791, "/health", str(SILENCE_PYTHON), ("contentops_process_bridge.py",), str(SILENCE_ROOT), "contentops_process_bridge.py", {"SILENCE_QWEN_ENDPOINT": "http://127.0.0.1:8792", "CONTENTOPS_ENFORCE_RUNTIME": "1"}),
    "Qwen": ServiceDefinition("Qwen", 8792, "/health", str(SILENCE_PYTHON), ("-m", "qwen_worker.supervisor"), str(SILENCE_ROOT), "qwen_worker.supervisor", {"SILENCE_QWEN_ENDPOINT": "http://127.0.0.1:8792", "SEMANTIC_QWEN_MODEL": str(MODEL)}),
    "Manual LAN API": ServiceDefinition("Manual LAN API", 8780, "/health", str(SILENCE_PYTHON), ("-m", "lan_job_api"), str(SILENCE_ROOT), "-m lan_job_api", {}),
}


def _health(url: str, timeout: float = 1.5) -> tuple[bool, dict[str, Any] | None]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return response.status < 500, json.loads(body) if body else {}
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return False, None


class RealRuntime:
    def __init__(self) -> None:
        self.processes: dict[str, tuple[int, float]] = {}

    def launch(self, definition: ServiceDefinition) -> int:
        env = os.environ.copy()
        env.update(definition.env)
        process = subprocess.Popen([definition.executable, *definition.args], cwd=definition.cwd, env=env, creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.processes[definition.name] = (process.pid, _process_start_time(process.pid) or time.time())
        return process.pid

    def health(self, definition: ServiceDefinition) -> bool:
        return _health(definition.health_url)[0]

    def stop(self, definition: ServiceDefinition, pid: int) -> None:
        if not self.owns(definition, pid):
            raise PermissionError(f"refusing to stop unowned {definition.name} process {pid}")
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.processes.pop(definition.name, None)

    def owns(self, definition: ServiceDefinition, pid: int) -> bool:
        expected = self.processes.get(definition.name)
        if expected is None or expected[0] != pid:
            return False
        command = _process_command(pid)
        started = _process_start_time(pid)
        return bool(command and started and definition.marker.lower() in command.lower() and abs(started - expected[1]) < 5)

    def adopt(self, runtime_path: Path) -> None:
        state = None
        for _ in range(3):
            try:
                state = json.loads(runtime_path.read_text(encoding="utf-8-sig"))
                break
            except (OSError, json.JSONDecodeError):
                time.sleep(0.1)
        if not isinstance(state, dict):
            return
        mapping = {"YT_NOTIFI": "watcher", "YTDOWNLOAD": "ytdownload", "Silence Scheduler": "silence", "Qwen": "qwen_worker"}
        for name, key in mapping.items():
            pid = state.get(f"{key}_pid")
            definition = SERVICE_DEFINITIONS[name]
            if isinstance(pid, int) and pid > 0 and _matches(pid, definition.marker):
                self.processes[name] = (pid, _process_start_time(pid) or time.time())
            else:
                found = _find_process_info(definition.marker, definition.port)
                if not found:
                    found = _find_process_info(definition.marker)
                if found:
                    self.processes[name] = found

        # LAN API is only adopted when its command marker proves ownership.
        found = _find_process_info("-m lan_job_api", SERVICE_DEFINITIONS["Manual LAN API"].port)
        if found:
            self.processes["Manual LAN API"] = found


def _process_command(pid: int) -> str | None:
    command = f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=3, check=False)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _process_start_time(pid: int) -> float | None:
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", f"(Get-Process -Id {pid}).StartTime.ToUniversalTime().Subtract([datetime]'1970-01-01').TotalSeconds"], capture_output=True, text=True, timeout=3, check=False)
        return float(result.stdout.strip()) if result.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _matches(pid: int, marker: str) -> bool:
    command = _process_command(pid)
    return bool(command and marker.lower() in command.lower())


def _find_process_info(marker: str, port: int | None = None) -> tuple[int, float] | None:
    escaped = marker.lower().replace("'", "''")
    if port is None:
        command = "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine.ToLower().Contains('" + escaped + "') } | Select-Object -First 1 -ExpandProperty ProcessId"
    else:
        command = f"$owners=@(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess); Get-CimInstance Win32_Process | Where-Object {{ $_.ProcessId -in $owners -and $_.CommandLine -and $_.CommandLine.ToLower().Contains('{escaped}') }} | Select-Object -First 1 -ExpandProperty ProcessId"
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=3, check=False)
        pid = int(result.stdout.strip()) if result.stdout.strip().isdigit() else None
        started = _process_start_time(pid) if pid else None
        return (pid, started) if pid and started else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


class ServiceController:
    def __init__(self, runtime: Any, runtime_path: Path | None = None) -> None:
        self.runtime = runtime
        self._lock = threading.RLock()
        self._pids: dict[str, int] = {}
        if runtime_path is not None and hasattr(runtime, "adopt"):
            runtime.adopt(runtime_path)
            self._pids = {name: pid for name, (pid, _) in runtime.processes.items()}

    def status(self, name: str) -> dict[str, Any]:
        definition = self._definition(name)
        ready = bool(self.runtime.health(definition))
        pid = self._pids.get(name)
        state = "READY" if ready else ("STARTING" if pid else "DOWN")
        return {"name": name, "port": definition.port, "state": state, "pid": pid, "health_url": definition.health_url, "managed": bool(pid)}

    def all_status(self) -> list[dict[str, Any]]:
        return [self.status(name) for name in SERVICE_DEFINITIONS]

    def start(self, name: str) -> dict[str, Any]:
        with self._lock:
            definition = self._definition(name)
            if self.runtime.health(definition):
                return self.status(name)
            pid = self.runtime.launch(definition)
            self._pids[name] = pid
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if self.runtime.health(definition):
                    return self.status(name)
                time.sleep(0.25)
            return self.status(name)

    def stop(self, name: str) -> dict[str, Any]:
        with self._lock:
            definition = self._definition(name)
            self._refresh_managed_pid(definition)
            pid = self._pids.get(name)
            if pid is None:
                if self.runtime.health(definition):
                    raise PermissionError(f"refusing to stop unowned {name} process")
                return self.status(name)
            self.runtime.stop(definition, pid)
            self._pids.pop(name, None)
            return self.status(name)

    def _refresh_managed_pid(self, definition: ServiceDefinition) -> None:
        """Replace a stale adopted PID with the currently running marked process."""
        processes = getattr(self.runtime, "processes", None)
        if not isinstance(processes, dict):
            return
        found = _find_process_info(definition.marker, definition.port)
        if not found:
            found = _find_process_info(definition.marker)
        if not found:
            return
        self._pids[definition.name] = found[0]
        processes[definition.name] = found

    def restart(self, name: str) -> dict[str, Any]:
        with self._lock:
            if name in self._pids:
                self.stop(name)
            return self.start(name)

    @staticmethod
    def _definition(name: str) -> ServiceDefinition:
        try:
            return SERVICE_DEFINITIONS[name]
        except KeyError as error:
            raise KeyError(f"unknown service: {name}") from error


def parse_action_path(path: str) -> tuple[str, str] | None:
    parts = path.strip("/").split("/")
    if len(parts) != 4 or parts[:2] != ["api", "services"]:
        return None
    return urllib.parse.unquote(parts[2]), urllib.parse.unquote(parts[3])


class ControlHandler(BaseHTTPRequestHandler):
    controller: ServiceController
    port = 8794

    def _send(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send(200, {"status": "READY", "service": "ContentOps Service Control", "port": self.port})
        elif self.path == "/api/services":
            self._send(200, {"services": self.controller.all_status()})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = parse_action_path(self.path)
        if parsed is None:
            self._send(404, {"error": "not found"})
            return
        name, action = parsed
        try:
            result = {"start": self.controller.start, "stop": self.controller.stop, "restart": self.controller.restart}[action](name)
            self._send(200, result)
        except KeyError:
            self._send(404, {"error": "unknown service or action"})
        except PermissionError as error:
            self._send(409, {"error": str(error)})

    def log_message(self, *_args: Any) -> None:
        return


def serve(port: int = 8794) -> None:
    runtime = RealRuntime()
    runtime_path = YT_ROOT / "state" / "production-runtime.json"
    controller = ServiceController(runtime, runtime_path)
    ControlHandler.controller = controller
    ControlHandler.port = port
    with ThreadingHTTPServer(("127.0.0.1", port), ControlHandler) as server:
        server.serve_forever()


if __name__ == "__main__":
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8794)
