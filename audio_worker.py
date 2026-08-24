from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable


class AudioAnalysisWorker:
    """Own one long-lived production runtime and its audio models."""

    def __init__(
        self,
        *,
        runtime_factory: Callable[[], Any] | None = None,
        silero_loader: Callable[[], Any] | None = None,
    ) -> None:
        self._runtime_factory = runtime_factory
        self._silero_loader = silero_loader
        self._runtime: Any | None = None
        self._status = "LOADING"
        self._error: str | None = None
        self._reuse_count = 0
        self._lock = threading.RLock()

    def _create_runtime(self) -> Any:
        if self._runtime_factory is not None:
            return self._runtime_factory()
        from production.pipeline import ProductionRuntime

        return ProductionRuntime()

    def _load_silero(self) -> Any:
        if self._silero_loader is not None:
            return self._silero_loader()
        from silence_cutter.vad import _model

        return _model()

    def warm(self) -> dict[str, Any]:
        with self._lock:
            if self._status == "READY":
                return self.health()
            self._status = "LOADING"
            self._error = None
            try:
                runtime = self._create_runtime()
                runtime.detector._load()
                self._load_silero()
                self._runtime = runtime
                self._status = "READY"
            except Exception as exc:
                self._runtime = None
                self._status = "ERROR"
                self._error = f"{type(exc).__name__}: {exc}"[-1000:]
            return self.health()

    def health(self) -> dict[str, Any]:
        with self._lock:
            detector = getattr(self._runtime, "detector", None)
            return {
                "status": self._status,
                "error": self._error,
                "active_device": getattr(detector, "active_device", None),
                "runtime_reuse_count": self._reuse_count,
            }

    def process(self, source: Path, report_path: Path, rendered_path: Path) -> dict[str, Any]:
        with self._lock:
            if self._status != "READY" or self._runtime is None:
                self.warm()
            if self._status != "READY" or self._runtime is None:
                raise RuntimeError(self._error or "audio model is not ready")
            runtime = self._runtime
            self._reuse_count += 1
        return runtime.process(
            source,
            rendered_path,
            analysis_only=True,
            debug=True,
            report_path=report_path,
        )
