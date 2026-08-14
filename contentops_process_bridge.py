from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.request
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


def qwen_ready() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8792/health", timeout=2) as response:
            health = json.loads(response.read().decode("utf-8"))
        return bool(
            health.get("status") == "READY"
            and health.get("model_loaded")
            and health.get("warmed_up")
        )
    except (OSError, urllib.error.URLError, ValueError):
        return False


def _production_part_core(
    source: Path, output_dir: Path, title: str, job_dir: Path, *,
    enhanced_content_selection: bool = False,
) -> list[Path]:
    if enhanced_content_selection:
        from enhanced_content_flow import EnhancedFlowSkipped, run_enhanced_content_flow

        try:
            return run_enhanced_content_flow(source, output_dir, title, job_dir)
        except EnhancedFlowSkipped:
            pass
    from backend.job_runner import _run_semantic_stage
    from formatter.planner import plan_done_job
    from formatter.renderer import render_format_plan
    from formatter.title_rewrite import rewrite_title_once
    from production import process_video

    job_dir.mkdir(parents=True, exist_ok=True)
    report = job_dir / "pipeline_report.json"
    process_video(
        source, job_dir / "rendered.mp4", analysis_only=True,
        debug=True, report_path=report,
    )
    _run_semantic_stage({}, job_dir, source, report)
    try:
        source_id = json.loads((job_dir / "request.json").read_text(encoding="utf-8")).get("video_id")
    except (OSError, ValueError):
        source_id = job_dir.name
    rewrite = rewrite_title_once(
        job_dir, title, output_dir, source_id=source_id, part_count=3,
    )
    job_file = job_dir / "job.json"
    job_file.write_text(json.dumps({
        "id": job_dir.name, "status": "DONE", "title": title,
        "source_path": str(source), "report_path": str(report),
        "output_folder": str(output_dir), "output_path": None,
        "rewritten_title": rewrite["rewritten_title"],
        "title_rewrite_status": rewrite["status"],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    plan_path = job_dir / "format_plan.json"
    plan = plan_done_job(
        job_file, output_path=plan_path,
        preview_path=job_dir / "part1_preview.png",
    )
    if plan["formatter_status"] != "PLANNED":
        raise RequestError(f"FORMATTER_{plan['formatter_status']}")
    result = render_format_plan(plan_path)
    if result["formatter_status"] != "DONE":
        raise RuntimeError(result.get("formatter_error") or "formatter failed")
    return [Path(item["path"]) for item in result["formatted_outputs"]]


class ContentOpsProcessBridge:
    def __init__(
        self,
        *,
        records_path: Path | None = None,
        port: int = 8791,
        host: str = LOOPBACK,
        max_concurrency: int = 1,
        core: Callable[[Path, Path, str, Path], list[Path]] = _production_part_core,
        qwen_health: Callable[[], bool] = qwen_ready,
    ) -> None:
        if host != LOOPBACK:
            raise ValueError("Content Ops bridge must bind to 127.0.0.1")
        data_root = Path(os.getenv("SILENCE_CUTTER_DATA_DIR", ROOT)).expanduser().resolve()
        self.records_path = records_path or data_root / "workspace" / "contentops-process-jobs.json"
        self.report_dir = self.records_path.parent / "contentops-process-reports"
        self.port, self.host, self.core = port, host, core
        self.qwen_health = qwen_health
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

    def _validate(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise RequestError("INVALID_REQUEST")
        request = {key: str(payload.get(key) or "").strip() for key in (
            "handoff_id", "source_file", "channel_name", "output_dir", "video_id", "video_title"
        )}
        enhanced = payload.get("enhanced_content_selection", False)
        if not isinstance(enhanced, bool):
            raise RequestError("INVALID_REQUEST")
        request["enhanced_content_selection"] = enhanced
        if enhanced and not self.qwen_health():
            raise RequestError("QWEN_WORKER_UNAVAILABLE")
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
                "processed_files": [], "processed_file_path": None, "error": None,
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
        source, output_dir = Path(request["source_file"]), Path(request["output_dir"])
        job_dir = self.report_dir / record["external_id"]
        try:
            if not source.is_file():
                raise RequestError("SOURCE_FILE_MISSING")
            if not output_dir.is_dir() or not os.access(output_dir, os.W_OK):
                raise RequestError("NAS_UNAVAILABLE")
            self.report_dir.mkdir(parents=True, exist_ok=True)
            job_dir.mkdir(parents=True, exist_ok=True)
            (job_dir / "request.json").write_text(
                json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
            )
            self._update(handoff_id, state="PROCESSING", progress_percent=5, error=None)
            if request.get("enhanced_content_selection"):
                outputs = self.core(
                    source, output_dir, request["video_title"], job_dir,
                    enhanced_content_selection=True,
                )
            else:
                outputs = self.core(source, output_dir, request["video_title"], job_dir)
            if not outputs or not all(path.is_file() for path in outputs):
                raise RuntimeError("formatter completed without all outputs")
            self._update(handoff_id, state="FINALIZING", progress_percent=95)
            exact = [str(path.resolve()) for path in outputs]
            self._update(
                handoff_id, state="DONE", progress_percent=100,
                processed_files=exact, processed_file_path=exact[0], error=None,
            )
        except Exception as exc:
            code = exc.code if isinstance(exc, RequestError) else "PROCESSING_FAILED"
            self._update(handoff_id, state="FAILED", error=code, progress_percent=0)

    def restore(self) -> None:
        with self._lock:
            active = [item["handoff_id"] for item in self.records.values() if item.get("state") in ACTIVE_STATES]
        for handoff_id in active:
            with self._lock:
                record = self.records[handoff_id]
                outputs = [Path(value) for value in record.get("processed_files") or []]
                legacy = Path(str(record.get("processed_file_path") or ""))
                if (outputs and all(path.is_file() for path in outputs)) or legacy.is_file():
                    record.update(
                        state="DONE", progress_percent=100,
                        processed_files=[str(path.resolve()) for path in outputs],
                        processed_file_path=(
                            str(outputs[0].resolve()) if outputs else str(legacy.resolve())
                        ),
                        error=None, updated_at=utc_now(),
                    )
                    self._save()
                    continue
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
                    status = 503 if exc.code == "QWEN_WORKER_UNAVAILABLE" else 422 if exc.code in {"SOURCE_FILE_MISSING", "NAS_UNAVAILABLE"} else 400
                    self.send_json(status, {"error": exc.code})
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
