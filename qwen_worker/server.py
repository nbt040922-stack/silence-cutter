from __future__ import annotations

import argparse
import base64
import io
import json
import os
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import urlsplit

from PIL import Image

from semantic_cleaner.qwen import QwenSemanticDetector


LOOPBACK = "127.0.0.1"


class QwenWorkerRuntime:
    def __init__(self, detector_factory: Callable[[], Any] = QwenSemanticDetector) -> None:
        self.detector_factory = detector_factory
        self.detector: Any | None = None
        self.status = "STARTING"
        self.error: str | None = None
        self.model_loaded = False
        self.warmed_up = False
        self.model_load_count = 0
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.model_load_seconds = 0.0
        self.warmup_seconds = 0.0
        self.ready_seconds = 0.0
        self.request_count = 0
        self._started = time.perf_counter()
        self._queue = threading.Lock()

    def load(self) -> None:
        try:
            self.status = "LOADING_MODEL"
            started = time.perf_counter()
            self.detector = self.detector_factory()
            self.model_load_count += 1
            self.model_load_seconds = time.perf_counter() - started
            self.model_loaded = True
            self.status = "WARMING_UP"
            warmed = time.perf_counter()
            self.detector.generate_text([], "Reply only READY", max_new_tokens=2)
            self.warmup_seconds = time.perf_counter() - warmed
            self.warmed_up = True
            self.ready_seconds = time.perf_counter() - self._started
            self.status = "READY"
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self.status = "ERROR"

    def health(self) -> dict[str, Any]:
        detector = self.detector
        return {
            "status": self.status,
            "model_loaded": self.model_loaded,
            "warmed_up": self.warmed_up,
            "device": "cuda" if self.model_loaded else None,
            "model": getattr(detector, "model_reference", None),
            "model_load_count": self.model_load_count,
            "model_load_seconds": self.model_load_seconds,
            "warmup_seconds": self.warmup_seconds,
            "ready_seconds": self.ready_seconds,
            "request_count": self.request_count,
            "started_at": self.started_at,
            "error": self.error,
        }

    def generate(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.status not in {"READY", "BUSY"} or not self.detector:
            raise RuntimeError(f"worker is not READY: {self.status}")
        queued = time.perf_counter()
        with self._queue:
            queue_wait = time.perf_counter() - queued
            self.status = "BUSY"
            try:
                images = []
                for value in payload.get("images") or []:
                    with Image.open(io.BytesIO(base64.b64decode(value))) as image:
                        images.append(image.convert("RGB"))
                started = time.perf_counter()
                text = self.detector.generate_text(
                    images, str(payload["prompt"]),
                    max_new_tokens=payload.get("max_new_tokens"),
                )
                self.request_count += 1
                return {
                    "text": text, "task": str(payload.get("task") or "generic"),
                    "queue_wait_seconds": queue_wait,
                    "generation_seconds": time.perf_counter() - started,
                }
            finally:
                self.status = "READY" if self.warmed_up else "ERROR"


class QwenWorkerServer:
    def __init__(self, runtime: QwenWorkerRuntime, host: str = LOOPBACK, port: int = 8792) -> None:
        if host != LOOPBACK:
            raise ValueError("Qwen worker must bind to 127.0.0.1")
        self.runtime, self.host, self.port = runtime, host, port
        self.stop_event = threading.Event()
        self.httpd: ThreadingHTTPServer | None = None

    def serve(self) -> None:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *args: Any) -> None:
                pass

            def reply(self, status: int, value: dict[str, Any]) -> None:
                body = json.dumps(value).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if urlsplit(self.path).path == "/health":
                    self.reply(200, owner.runtime.health())
                else:
                    self.reply(404, {"error": "NOT_FOUND"})

            def do_POST(self) -> None:
                path = urlsplit(self.path).path
                if path == "/shutdown":
                    self.reply(200, {"status": "STOPPING"})
                    threading.Thread(target=owner.httpd.shutdown, daemon=True).start()
                    return
                if path != "/generate":
                    self.reply(404, {"error": "NOT_FOUND"})
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if size <= 0 or size > 64 * 1024 * 1024:
                        raise ValueError("invalid request size")
                    self.reply(200, owner.runtime.generate(json.loads(self.rfile.read(size))))
                except Exception as exc:
                    owner.runtime.error = f"{type(exc).__name__}: {exc}"
                    owner.runtime.status = "ERROR"
                    self.reply(503, {"error": f"{type(exc).__name__}: {exc}"})
                    threading.Thread(target=owner.httpd.shutdown, daemon=True).start()

        self.httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        threading.Thread(target=self.runtime.load, daemon=True).start()
        self.httpd.serve_forever()
        self.httpd.server_close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=LOOPBACK)
    parser.add_argument("--port", type=int, default=int(os.getenv("QWEN_WORKER_PORT", "8792")))
    args = parser.parse_args()
    server = QwenWorkerServer(QwenWorkerRuntime(), args.host, args.port)
    server.serve()
    if server.runtime.status == "ERROR":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
