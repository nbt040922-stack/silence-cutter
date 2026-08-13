from __future__ import annotations

import base64
import io
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from PIL import Image


class WorkerUnavailable(RuntimeError):
    pass


class _CudaMetrics:
    def max_memory_allocated(self) -> int:
        return 0


class _TorchMetrics:
    cuda = _CudaMetrics()


class QwenWorkerClient:
    """Qwen detector-compatible client. Model always lives in localhost worker."""

    def __init__(self, url: str | None = None, timeout: float | None = None) -> None:
        self.url = (url or os.getenv("QWEN_WORKER_URL", "http://127.0.0.1:8792")).rstrip("/")
        self.timeout = timeout or float(os.getenv("QWEN_WORKER_REQUEST_TIMEOUT", "120"))
        self.model_load_time = 0.0
        self.generation_count = 0
        self.torch = _TorchMetrics()

    def _request(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.url + path, data=data,
            headers={"Content-Type": "application/json"} if data else {},
            method="POST" if data else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, ValueError) as exc:
            raise WorkerUnavailable(f"Qwen worker unavailable: {exc}") from exc

    def health(self) -> dict[str, Any]:
        return self._request("/health")

    def wait_ready(
        self, timeout: float = 180.0, *, wait_through_error: bool = False,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last: dict[str, Any] = {"status": "UNAVAILABLE"}
        while time.monotonic() < deadline:
            try:
                last = self.health()
                if last.get("status") == "READY":
                    return last
                if last.get("status") == "ERROR" and not wait_through_error:
                    break
            except WorkerUnavailable:
                pass
            time.sleep(0.5)
        raise WorkerUnavailable(f"Qwen worker not READY: {last.get('status')}")

    def generate_text(
        self, images: list[Image.Image], prompt: str, *, max_new_tokens: int | None = None,
        task: str = "generic",
    ) -> str:
        encoded = []
        for image in images:
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, "JPEG", quality=88)
            encoded.append(base64.b64encode(buffer.getvalue()).decode("ascii"))
        started = time.perf_counter()
        payload = {"task": task, "prompt": prompt, "images": encoded,
                   "max_new_tokens": max_new_tokens}
        try:
            result = self._request("/generate", payload)
        except WorkerUnavailable:
            self.wait_ready(
                float(os.getenv("QWEN_WORKER_RESTART_TIMEOUT", "180")),
                wait_through_error=True,
            )
            result = self._request("/generate", payload)
        self.generation_count += 1
        self.last_queue_wait = float(result.get("queue_wait_seconds", 0.0))
        self.last_generation_time = float(result.get("generation_seconds", time.perf_counter() - started))
        return str(result["text"])
