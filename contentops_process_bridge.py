from __future__ import annotations

import json
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parent
LOOPBACK = "127.0.0.1"
ACTIVE_STATES = {"QUEUED", "PROCESSING", "FINALIZING"}


class RequestError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_stem(title: str, fallback: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", title.strip()).rstrip(" .")
    value = value[:120].rstrip(" .") or fallback
    if value.split(".", 1)[0].upper() in {
        "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        value = f"_{value}"
    return value


def _production_core(source: Path, temporary: Path, report: Path) -> Any:
    from production import process_video

    return process_video(source, temporary, report_path=report)


class ContentOpsProcessBridge:
    def __init__(
        self,
        *,
        records_path: Path | None = None,
        port: int = 8791,
        host: str = LOOPBACK,
        max_concurrency: int = 1,
        core: Callable[[Path, Path, Path], Any] = _production_core,
    ) -> None:
        if host != LOOPBACK:
            raise ValueError("Content Ops bridge must bind to 127.0.0.1")
        data_root = Path(os.getenv("SILENCE_CUTTER_DATA_DIR", ROOT)).expanduser().resolve()
        self.records_path = records_path or data_root / "workspace" / "contentops-process-jobs.json"
        self.report_dir = self.records_path.parent / "contentops-process-reports"
        self.port, self.host, self.core = port, host, core
        self.records: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=max(1, max_concurrency), thread_name_prefix="contentops-process")
        self._server: ThreadingHTTPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._load()

    def _load(self) -> None:
        try:
            rows = json.loads(self.records_path.read_text(encoding="utf-8"))
            if isinstance(rows, list):
                self.records = {str(row["handoff_id"]): row for row in rows}
        except (OSError, ValueError, TypeError, KeyError):
            self.records = {}

    def _save(self) -> None:
        self.records_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.records_path.with_name(f".{self.records_path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(list(self.records.values()), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.records_path)

    def _validate(self, payload: Any) -> dict[str, str]:
        if not isinstance(payload, dict):
            raise RequestError("INVALID_REQUEST")
        request = {key: str(payload.get(key) or "").strip() for key in (
            "handoff_id", "source_file", "channel_name", "output_dir", "video_id", "video_title"
        )}
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", request["handoff_id"]):
            raise RequestError("INVALID_REQUEST")
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", request["video_id"]):
            raise RequestError("INVALID_REQUEST")
        source, output = Path(request["source_file"]), Path(request["output_dir"])
        if not request["channel_name"] or not source.is_absolute() or not output.is_absolute():
            raise RequestError("INVALID_REQUEST")
        if not source.is_file():
            raise RequestError("SOURCE_FILE_MISSING")
        if not output.is_dir() or not os.access(output, os.W_OK):
            raise RequestError("NAS_UNAVAILABLE")
        return request

    def _target(self, request: dict[str, str]) -> Path:
        output = Path(request["output_dir"])
        stem = safe_stem(request["video_title"], request["video_id"])
        reserved = {str(item["target_path"]).casefold() for item in self.records.values()}
        occupied = lambda candidate: candidate.exists() or str(candidate).casefold() in reserved
        target = output / f"{stem}.mp4"
        if occupied(target):
            target = output / f"{stem}_{request['video_id']}.mp4"
        if occupied(target):
            target = output / f"{stem}_{request['video_id']}_{request['handoff_id']}.mp4"
        return target

    def submit(self, payload: Any) -> tuple[bool, dict[str, Any]]:
        if isinstance(payload, dict):
            handoff_id = str(payload.get("handoff_id") or "").strip()
            if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", handoff_id):
                with self._lock:
                    existing = self.records.get(handoff_id)
                    if existing:
                        return False, dict(existing)
        request = self._validate(payload)
        with self._lock:
            existing = self.records.get(request["handoff_id"])
            if existing:
                return False, dict(existing)
            now = utc_now()
            external_id = f"contentops-process-{request['handoff_id']}"
            record = {
                "handoff_id": request["handoff_id"], "external_id": external_id,
                "request": request, "state": "QUEUED", "progress_percent": 0,
                "processed_file_path": None, "error": None,
                "target_path": str(self._target(request)),
                "created_at": now, "updated_at": now,
            }
            self.records[request["handoff_id"]] = record
            self._save()
            try:
                self._executor.submit(self._process, request["handoff_id"])
            except Exception:
                self.records.pop(request["handoff_id"], None)
                self._save()
                raise
            return True, dict(record)

    def get(self, external_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = next((item for item in self.records.values() if item["external_id"] == external_id), None)
            return dict(record) if record else None

    def _update(self, handoff_id: str, **values: Any) -> None:
        with self._lock:
            self.records[handoff_id].update(values, updated_at=utc_now())
            self._save()

    def _process(self, handoff_id: str) -> None:
        with self._lock:
            record = dict(self.records[handoff_id])
        request = record["request"]
        source, final = Path(request["source_file"]), Path(record["target_path"])
        partial = final.with_suffix(".processing.mp4")
        report = self.report_dir / f"{record['external_id']}.json"
        try:
            if not source.is_file():
                raise RequestError("SOURCE_FILE_MISSING")
            if not final.parent.is_dir() or not os.access(final.parent, os.W_OK):
                raise RequestError("NAS_UNAVAILABLE")
            partial.unlink(missing_ok=True)
            self.report_dir.mkdir(parents=True, exist_ok=True)
            self._update(handoff_id, state="PROCESSING", progress_percent=5, error=None)
            self.core(source, partial, report)
            if not partial.is_file():
                raise RuntimeError("processor completed without output")
            self._update(handoff_id, state="FINALIZING", progress_percent=95)
            os.replace(partial, final)
            self._update(
                handoff_id, state="DONE", progress_percent=100,
                processed_file_path=str(final.resolve()), error=None,
            )
        except Exception as exc:
            partial.unlink(missing_ok=True)
            code = exc.code if isinstance(exc, RequestError) else "PROCESSING_FAILED"
            self._update(handoff_id, state="FAILED", error=code, progress_percent=0)

    def restore(self) -> None:
        with self._lock:
            active = [item["handoff_id"] for item in self.records.values() if item.get("state") in ACTIVE_STATES]
        for handoff_id in active:
            with self._lock:
                record = self.records[handoff_id]
                final = Path(record["target_path"])
                partial = final.with_suffix(".processing.mp4")
                if final.is_file():
                    record.update(
                        state="DONE", progress_percent=100,
                        processed_file_path=str(final.resolve()), error=None, updated_at=utc_now(),
                    )
                    self._save()
                    continue
                partial.unlink(missing_ok=True)
                record.update(state="QUEUED", progress_percent=0, error=None, updated_at=utc_now())
                self._save()
            self._executor.submit(self._process, handoff_id)

    def start(self) -> tuple[str, int]:
        if self._server:
            return self._server.server_address
        self.restore()
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *args: Any) -> None:
                pass

            def send_json(self, status: int, value: Any) -> None:
                body = json.dumps(value, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                path = urlsplit(self.path).path
                if path == "/health":
                    return self.send_json(200, {"status": "ok"})
                prefix = "/api/process-jobs/"
                if path.startswith(prefix):
                    record = bridge.get(unquote(path[len(prefix):]))
                    return self.send_json(200 if record else 404, record or {"error": "NOT_FOUND"})
                self.send_json(404, {"error": "NOT_FOUND"})

            def do_POST(self) -> None:
                if urlsplit(self.path).path != "/api/process-jobs":
                    return self.send_json(404, {"error": "NOT_FOUND"})
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if size > 64 * 1024:
                        raise RequestError("INVALID_REQUEST")
                    created, record = bridge.submit(json.loads(self.rfile.read(size)))
                    self.send_json(201 if created else 200, record)
                except RequestError as exc:
                    self.send_json(422 if exc.code in {"SOURCE_FILE_MISSING", "NAS_UNAVAILABLE"} else 400, {"error": exc.code})
                except (ValueError, TypeError, json.JSONDecodeError):
                    self.send_json(400, {"error": "INVALID_REQUEST"})
                except Exception:
                    self.send_json(500, {"error": "BRIDGE_ERROR"})

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._server_thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._server_thread.start()
        return self._server.server_address

    def close(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        self._executor.shutdown(wait=True, cancel_futures=False)


def main() -> None:
    data_root = Path(os.getenv("SILENCE_CUTTER_DATA_DIR", ROOT)).expanduser().resolve()
    bridge = ContentOpsProcessBridge(
        records_path=data_root / "workspace" / "contentops-process-jobs.json",
        port=int(os.getenv("CONTENTOPS_PROCESS_BRIDGE_PORT", "8791")),
        max_concurrency=int(os.getenv("SILENCE_PROCESS_MAX_CONCURRENCY", "1")),
    )
    bridge.start()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        bridge.close()


if __name__ == "__main__":
    main()
