from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

from contentops_process_bridge import (
    ContentOpsProcessBridge, RequestError, _production_part_core,
)


def request(source: Path, output: Path, **overrides):
    return {
        "handoff_id": "123", "source_file": str(source),
        "channel_name": "Test Channel", "output_dir": str(output),
        "video_id": "abcdefghijk", "video_title": "Why America Is Changing?",
    } | overrides


def wait_done(bridge: ContentOpsProcessBridge, external_id: str):
    for _ in range(100):
        record = bridge.get(external_id)
        if record["state"] in {"DONE", "FAILED"}:
            return record
        time.sleep(0.01)
    raise AssertionError("processor did not finish")


@pytest.fixture
def media(tmp_path):
    source, output = tmp_path / "source.mp4", tmp_path / "nas"
    source.write_bytes(b"source")
    output.mkdir()
    return source, output


def test_submit_is_idempotent_and_returns_exact_part_paths(tmp_path, media):
    source, output = media
    calls = []
    def core(input_path, output_dir, title, job_dir):
        calls.append((input_path, output_dir, title, job_dir))
        parts = [output_dir / "PART_1.mp4", output_dir / "PART_2.mp4"]
        for part in parts:
            part.write_bytes(b"processed")
        return parts
    bridge = ContentOpsProcessBridge(records_path=tmp_path / "records.json", core=core)
    created, first = bridge.submit(request(source, output))
    duplicate, second = bridge.submit(request(source, output))
    result = wait_done(bridge, first["external_id"])
    assert created and not duplicate
    assert first["external_id"] == second["external_id"] == "contentops-process-123"
    assert len(calls) == 1
    assert [Path(value).name for value in result["processed_files"]] == ["PART_1.mp4", "PART_2.mp4"]
    assert result["processed_file_path"] == result["processed_files"][0]
    assert all(Path(value).read_bytes() == b"processed" for value in result["processed_files"])
    assert not list(output.glob("*.processing.mp4"))
    assert source.read_bytes() == b"source"
    source.unlink()
    assert bridge.submit(request(source, output))[0] is False
    bridge.close()


def test_invalid_missing_source_and_unavailable_nas(tmp_path, media):
    source, output = media
    bridge = ContentOpsProcessBridge(records_path=tmp_path / "records.json", core=lambda *_: None)
    with pytest.raises(RequestError, match="INVALID_REQUEST"):
        bridge.submit(request(source, output, handoff_id="bad id"))
    with pytest.raises(RequestError, match="SOURCE_FILE_MISSING"):
        bridge.submit(request(tmp_path / "missing.mp4", output))
    with pytest.raises(RequestError, match="NAS_UNAVAILABLE"):
        bridge.submit(request(source, tmp_path / "missing-nas"))
    bridge.close()


def test_bridge_rejects_non_loopback_bind(tmp_path):
    with pytest.raises(ValueError, match="must bind to 127.0.0.1"):
        ContentOpsProcessBridge(records_path=tmp_path / "records.json", host="0.0.0.0")


def test_production_core_reuses_existing_planner_and_renderer(tmp_path, media):
    source, output = media
    job_dir = tmp_path / "adapter-job"
    parts = [output / "PART_1.mp4", output / "PART_2.mp4"]
    for part in parts:
        part.write_bytes(b"part")
    with (
        patch("production.process_video") as process,
        patch("formatter.planner.plan_done_job", return_value={"formatter_status": "PLANNED"}) as plan,
        patch("formatter.renderer.render_format_plan", return_value={
            "formatter_status": "DONE",
            "formatted_outputs": [{"path": str(part)} for part in parts],
        }) as render,
    ):
        result = _production_part_core(source, output, "Video title", job_dir)
    assert result == parts
    assert process.call_args.kwargs["analysis_only"] is True
    assert process.call_args.kwargs["debug"] is True
    assert plan.call_count == render.call_count == 1
    job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert job["output_folder"] == str(output)


def test_failure_removes_partial_and_never_exposes_final(tmp_path, media):
    source, output = media
    def core(_source, output, _title, _job_dir):
        (output / ".PART_1-temp.mp4").write_bytes(b"partial")
        raise RuntimeError("render failed")
    bridge = ContentOpsProcessBridge(records_path=tmp_path / "records.json", core=core)
    _, record = bridge.submit(request(source, output))
    result = wait_done(bridge, record["external_id"])
    assert (result["state"], result["error"]) == ("FAILED", "PROCESSING_FAILED")
    assert not list(output.glob("PART_*.mp4"))
    assert source.is_file()
    bridge.close()


def test_restart_cleans_stale_partial_and_reuses_external_id(tmp_path, media):
    source, output = media
    records = tmp_path / "records.json"
    final = output / "PART_1.mp4"
    records.write_text(json.dumps([{
        "handoff_id": "123", "external_id": "contentops-process-123",
        "request": request(source, output, video_title="Title"),
        "state": "PROCESSING", "progress_percent": 20,
        "processed_files": [], "processed_file_path": None, "error": None,
        "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00",
    }]), encoding="utf-8")
    def core(_source, output_dir, _title, _job_dir):
        target = output_dir / "PART_1.mp4"
        target.write_bytes(b"recovered")
        return [target]
    bridge = ContentOpsProcessBridge(records_path=records, core=core)
    bridge.restore()
    result = wait_done(bridge, "contentops-process-123")
    assert result["state"] == "DONE"
    assert final.read_bytes() == b"recovered"
    assert bridge.submit(request(source, output, video_title="Title"))[0] is False
    bridge.close()


def http_json(method, url, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    request_value = urllib.request.Request(url, data=body, method=method, headers={"Content-Type": "application/json"})
    try:
        response = urllib.request.urlopen(request_value)
    except urllib.error.HTTPError as exc:
        response = exc
    return response.status, json.loads(response.read())


def test_localhost_http_contract(tmp_path, media):
    source, output = media
    def core(_source, output_dir, _title, _job_dir):
        target = output_dir / "PART_1.mp4"
        target.write_bytes(b"ok")
        return [target]
    bridge = ContentOpsProcessBridge(records_path=tmp_path / "records.json", port=0, core=core)
    host, port = bridge.start()
    assert host == "127.0.0.1"
    assert http_json("GET", f"http://{host}:{port}/health") == (200, {"status": "ok"})
    status, created = http_json("POST", f"http://{host}:{port}/api/process-jobs", request(source, output))
    assert status == 201
    wait_done(bridge, created["external_id"])
    status, fetched = http_json("GET", f"http://{host}:{port}/api/process-jobs/{created['external_id']}")
    assert status == 200 and fetched["state"] == "DONE"
    bridge.close()
