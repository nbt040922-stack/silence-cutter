"""Tiny loopback bridge for opening output folders on the clicking PC."""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


HOST = "127.0.0.1"
PORT = int(os.environ.get("SILENCE_CUTTER_FOLDER_HELPER_PORT", "8793"))


def normalize_folder(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("folder path is required")
    # pathlib on Windows understands both drive-letter and UNC paths.
    path = Path(raw)
    if not (path.is_absolute() or raw.startswith(("\\\\", "//"))):
        raise ValueError("folder path must be absolute")
    return raw


def open_folder(value: str) -> None:
    folder = normalize_folder(value)
    if not hasattr(os, "startfile"):
        raise RuntimeError("folder helper requires Windows")
    os.startfile(folder)  # type: ignore[attr-defined]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *args: Any) -> None:
        return

    def _reply(self, status: int, value: dict[str, Any]) -> None:
        body = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self._reply(204, {})

    def do_POST(self) -> None:
        if self.path != "/open":
            self._reply(404, {"error": "not found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 16 * 1024:
                raise ValueError("invalid request size")
            payload = json.loads(self.rfile.read(size))
            open_folder(payload.get("path"))
            self._reply(200, {"ok": True})
        except (ValueError, json.JSONDecodeError) as error:
            self._reply(400, {"error": str(error)})
        except Exception as error:
            self._reply(500, {"error": str(error)})


def main() -> None:
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
