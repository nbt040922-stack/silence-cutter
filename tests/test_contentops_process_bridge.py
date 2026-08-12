from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from contentops_process_bridge import ContentOpsProcessBridge, RequestError, safe_stem


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


def test_submit_is_idempotent_and_returns_exact_final_path(tmp_path, media):
    source, output = media
    calls = []
    def core(input_path, temporary, report):
        calls.append((input_path, temporary, report))
        assert temporary.name == "Why America Is Changing.processing.mp4"
        temporary.write_bytes(b"processed")
    bridge = ContentOpsProcessBridge(records_path=tmp_path / "records.json", core=core)
    created, first = bridge.submit(request(source, output))
    duplicate, second = bridge.submit(request(source, output))
    result = wait_done(bridge, first["external_id"])
    assert created and not duplicate
    assert first["external_id"] == second["external_id"] == "contentops-process-123"
    assert len(calls) == 1
    assert Path(result["processed_file_path"]).read_bytes() == b"processed"
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


def test_safe_deterministic_collision_name(tmp_path, media):
    source, output = media
    assert safe_stem('CON<>:"/\\|?*.', "fallback") == "_CON"
    (output / "Title.mp4").write_bytes(b"other")
    bridge = ContentOpsProcessBridge(records_path=tmp_path / "records.json", core=lambda _s, target, _r: target.write_bytes(b"ok"))
    _, record = bridge.submit(request(source, output, video_title="Title"))
    result = wait_done(bridge, record["external_id"])
    assert Path(result["processed_file_path"]).name == "Title_abcdefghijk.mp4"
    bridge.close()


def test_active_jobs_reserve_distinct_output_names(tmp_path, media):
    source, output = media
    gate = threading.Event()
    def core(_source, target, _report):
        gate.wait(1)
        target.write_bytes(b"ok")
    bridge = ContentOpsProcessBridge(records_path=tmp_path / "records.json", core=core)
    _, first = bridge.submit(request(source, output, handoff_id="1", video_title="Title"))
    _, second = bridge.submit(request(source, output, handoff_id="2", video_title="Title"))
    assert first["target_path"] != second["target_path"]
    gate.set()
    wait_done(bridge, first["external_id"])
    wait_done(bridge, second["external_id"])
    bridge.close()


def test_failure_removes_partial_and_never_exposes_final(tmp_path, media):
    source, output = media
    def core(_source, temporary, _report):
        temporary.write_bytes(b"partial")
        raise RuntimeError("render failed")
    bridge = ContentOpsProcessBridge(records_path=tmp_path / "records.json", core=core)
    _, record = bridge.submit(request(source, output))
    result = wait_done(bridge, record["external_id"])
    assert (result["state"], result["error"]) == ("FAILED", "PROCESSING_FAILED")
    assert list(output.iterdir()) == []
    assert source.is_file()
    bridge.close()


def test_restart_cleans_stale_partial_and_reuses_external_id(tmp_path, media):
    source, output = media
    records = tmp_path / "records.json"
    final = output / "Title.mp4"
    partial = output / "Title.processing.mp4"
    partial.write_bytes(b"stale")
    records.write_text(json.dumps([{
        "handoff_id": "123", "external_id": "contentops-process-123",
        "request": request(source, output, video_title="Title"),
        "state": "PROCESSING", "progress_percent": 20,
        "processed_file_path": None, "error": None, "target_path": str(final),
        "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00",
    }]), encoding="utf-8")
    bridge = ContentOpsProcessBridge(records_path=records, core=lambda _s, target, _r: target.write_bytes(b"recovered"))
    bridge.restore()
    result = wait_done(bridge, "contentops-process-123")
    assert result["state"] == "DONE"
    assert final.read_bytes() == b"recovered"
    assert not partial.exists()
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
    bridge = ContentOpsProcessBridge(records_path=tmp_path / "records.json", port=0, core=lambda _s, target, _r: target.write_bytes(b"ok"))
    host, port = bridge.start()
    assert host == "127.0.0.1"
    assert http_json("GET", f"http://{host}:{port}/health") == (200, {"status": "ok"})
    status, created = http_json("POST", f"http://{host}:{port}/api/process-jobs", request(source, output))
    assert status == 201
    wait_done(bridge, created["external_id"])
    status, fetched = http_json("GET", f"http://{host}:{port}/api/process-jobs/{created['external_id']}")
    assert status == 200 and fetched["state"] == "DONE"
    bridge.close()
