"""Single-file installer and loopback folder opener for Windows clients."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


HOST = "127.0.0.1"
PORT = 8793
LOCAL_APP_DATA = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "SilenceCutterFolderHelper"


def install_path() -> Path:
    return LOCAL_APP_DATA / "SilenceCutter" / "folder-helper.exe"


def service_command(path: Path) -> list[str]:
    return [str(path), "--service"]


def _reply(handler: BaseHTTPRequestHandler, status: int, value: dict[str, Any]) -> None:
    body = json.dumps(value, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *args: Any) -> None:
        return

    def do_OPTIONS(self) -> None:
        _reply(self, 204, {})

    def do_POST(self) -> None:
        if self.path != "/open":
            _reply(self, 404, {"error": "not found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 16 * 1024:
                raise ValueError("invalid request size")
            payload = json.loads(self.rfile.read(size))
            raw = str(payload.get("path") or "").strip()
            if not raw or not Path(raw).is_absolute():
                raise ValueError("folder path must be absolute")
            if not hasattr(os, "startfile"):
                raise RuntimeError("folder helper requires Windows")
            os.startfile(raw)  # type: ignore[attr-defined]
            _reply(self, 200, {"ok": True})
        except (ValueError, json.JSONDecodeError) as error:
            _reply(self, 400, {"error": str(error)})
        except Exception as error:
            _reply(self, 500, {"error": str(error)})


def run_service() -> None:
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


def install() -> None:
    if os.name != "nt" or not getattr(sys, "frozen", False):
        raise RuntimeError("installer must run as the packaged Windows executable")
    target = install_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    source = Path(sys.executable).resolve()
    if source != target:
        shutil.copy2(source, target)
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, '"%s" --service' % target)
    subprocess.Popen(service_command(target), creationflags=subprocess.CREATE_NO_WINDOW)


def main() -> None:
    if "--service" in sys.argv[1:]:
        run_service()
    else:
        install()


if __name__ == "__main__":
    main()
