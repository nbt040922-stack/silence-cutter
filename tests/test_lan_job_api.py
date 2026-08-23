from lan_job_api import authorize, is_local_address, validate_submission


def test_root_status_is_public():
    # The HTTP handler keeps the root informational while job endpoints stay protected.
    from lan_job_api import _Handler
    assert _Handler is not None


def test_management_ui_is_local_and_token_protected_for_jobs():
    from lan_job_api import UI_HTML
    assert "/jobs" in UI_HTML
    assert "Bearer" in UI_HTML
    assert "http" not in UI_HTML.replace("http://", "").replace("https://", "")
    assert "/jobs/history" in UI_HTML
    assert "clearHistory" in UI_HTML
    assert "Retry" in UI_HTML
    assert "submitter_ip" in UI_HTML
    assert "<table" in UI_HTML
    assert "j.formatter_status==='FAILED'" in UI_HTML
    assert "['FAILED','INTERRUPTED','CANCELLED']" in UI_HTML
    assert "j.status==='CANCELLED'" in UI_HTML
    assert "Nơi lưu output" in UI_HTML
    assert "max-width:1500px" in UI_HTML
    assert "Máy gửi" in UI_HTML
    assert "Ngày hoàn thành" in UI_HTML
    assert "HH:MM - DD/MM/YYYY" in UI_HTML
    assert "text-align:center" in UI_HTML
    assert "title-cell" in UI_HTML
    assert "title-cell\" style=\"white-space:normal" in UI_HTML
    assert "Mở thư mục" in UI_HTML
    assert "file://" in UI_HTML
    assert "folderUrl" in UI_HTML
    assert "copyFolderPath" in UI_HTML
    assert "openClientFolder" in UI_HTML
    assert "window.alert(notice)" in UI_HTML
    assert "Helper chưa chạy trên máy này" in UI_HTML
    assert "127.0.0.1:8793/open" in UI_HTML


def test_retry_helper_delegates_to_backend(monkeypatch):
    import lan_job_api

    calls = []
    monkeypatch.setattr(lan_job_api, "_backend", lambda: type(
        "Backend", (), {"retry_job": staticmethod(lambda job_id: calls.append(job_id) or {"id": job_id})}
    )())
    assert lan_job_api.retry_remote_job("abc") == {"id": "abc"}
    assert calls == ["abc"]


def test_remote_submission_persists_submitter_ip(monkeypatch):
    import lan_job_api

    stored = []
    class Backend:
        @staticmethod
        def create_jobs(_urls):
            return [{"id": "job-1", "status": "QUEUED"}]

        @staticmethod
        def _write_job(job):
            stored.append(job)

    monkeypatch.setattr(lan_job_api, "_backend", lambda: Backend)
    result = lan_job_api.create_remote_job(
        {"url": "https://www.youtube.com/watch?v=abc"},
        submitter_ip="192.168.1.20",
    )
    assert result["job_id"] == "job-1"
    assert stored[0]["submitter_ip"] == "192.168.1.20"
    assert stored[0]["origin"] == "MANUAL_LAN"


def test_repeated_manual_submission_reuses_existing_video_job(monkeypatch):
    import lan_job_api

    existing = {
        "id": "existing-job", "status": "CANCELLED", "origin": "MANUAL_LAN",
        "url": "https://www.youtube.com/watch?v=abc",
    }
    class Backend:
        @staticmethod
        def list_jobs():
            return [existing]

        @staticmethod
        def create_jobs(_urls):
            raise AssertionError("duplicate MANUAL_LAN job was created")

    monkeypatch.setattr(lan_job_api, "_backend", lambda: Backend)
    result = lan_job_api.create_remote_job({"url": existing["url"]}, submitter_ip="192.168.1.20")
    assert result == {"job_id": "existing-job", "status": "CANCELLED", "deduplicated": True}


def test_terminal_manual_submission_is_idempotent_for_all_terminal_states(monkeypatch):
    import lan_job_api

    class Backend:
        @staticmethod
        def list_jobs():
            return [{
                "id": "terminal-job", "status": Backend.status, "origin": "MANUAL_LAN",
                "url": "https://www.youtube.com/watch?v=abc",
            }]

        @staticmethod
        def create_jobs(_urls):
            raise AssertionError("terminal MANUAL_LAN job was resurrected")

    monkeypatch.setattr(lan_job_api, "_backend", lambda: Backend)
    for status in ("DONE", "FAILED", "CANCELLED"):
        Backend.status = status
        result = lan_job_api.create_remote_job({"url": "https://www.youtube.com/watch?v=abc"})
        assert result == {"job_id": "terminal-job", "status": status, "deduplicated": True}


def test_manual_scheduler_payload_uses_shared_bridge(monkeypatch, tmp_path):
    import lan_job_api
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    job = {
        "id": "a" * 32, "origin": "MANUAL_LAN", "status": "READY",
        "source_path": str(source), "title": "Manual video",
        "url": "https://www.youtube.com/watch?v=abcdefghijk",
    }
    monkeypatch.setattr(lan_job_api, "_backend", lambda: type(
        "Backend", (), {"load_settings": staticmethod(lambda: {"output_folder": str(tmp_path / "out")})}
    )())
    (tmp_path / "out").mkdir()
    payload = lan_job_api._manual_scheduler_payload(job)
    assert payload["origin"] == "MANUAL_LAN"
    assert payload["source_file"] == str(source)
    assert payload["video_id"] == "abcdefghijk"
    assert payload["handoff_id"] == "manual-" + "a" * 32


def test_manual_ready_job_is_submitted_to_shared_scheduler_not_qwen(monkeypatch, tmp_path):
    import lan_job_api
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    output = tmp_path / "out"
    output.mkdir()
    job = {
        "id": "b" * 32, "origin": "MANUAL_LAN", "status": "READY",
        "source_path": str(source), "title": "Manual video",
        "url": "https://www.youtube.com/watch?v=abcdefghijk",
        "silence_external_id": None,
    }
    saved = []
    backend = type("Backend", (), {
        "list_jobs": staticmethod(lambda: [job]),
        "load_settings": staticmethod(lambda: {"output_folder": str(output)}),
        "_write_job": staticmethod(lambda value: saved.append(value.copy())),
    })
    calls = []
    def request(method, url, payload=None):
        calls.append((method, url, payload))
        return {"external_id": "contentops-process-manual"}
    monkeypatch.setattr(lan_job_api, "_backend", lambda: backend)
    monkeypatch.setattr(lan_job_api, "_scheduler_request", request)

    lan_job_api._sync_manual_jobs_once()

    assert calls[0][0] == "POST"
    assert calls[0][1] == "http://127.0.0.1:8791/api/process-jobs"
    assert calls[0][2]["origin"] == "MANUAL_LAN"
    assert saved[-1]["silence_external_id"] == "contentops-process-manual"


def test_manual_scheduler_failure_is_authoritative(monkeypatch, tmp_path):
    import lan_job_api
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    output = tmp_path / "out"
    output.mkdir()
    job = {
        "id": "c" * 32, "origin": "MANUAL_LAN", "status": "QUEUED",
        "source_path": str(source), "title": "Manual video",
        "url": "https://www.youtube.com/watch?v=abcdefghijk",
        "silence_external_id": "contentops-process-manual",
    }
    saved = []
    backend = type("Backend", (), {
        "list_jobs": staticmethod(lambda: [job]),
        "_now": staticmethod(lambda: "now"),
        "_write_job": staticmethod(lambda value: saved.append(value.copy())),
    })
    monkeypatch.setattr(lan_job_api, "_backend", lambda: backend)
    monkeypatch.setattr(lan_job_api, "_scheduler_request", lambda *_args, **_kwargs: {
        "state": "FAILED", "error": "PROCESSING_FAILED",
        "failure_detail": "production analysis completed without report",
    })

    lan_job_api._sync_manual_jobs_once()

    assert saved[-1]["status"] == "FAILED"
    assert saved[-1]["error"] == "PROCESSING_FAILED"
    assert saved[-1]["scheduler_state"] == "FAILED"
    assert saved[-1]["scheduler_failure_detail"] == "production analysis completed without report"


def test_manual_scheduler_done_requires_processed_outputs(monkeypatch, tmp_path):
    import lan_job_api
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    output = tmp_path / "out"
    output.mkdir()
    job = {
        "id": "d" * 32, "origin": "MANUAL_LAN", "status": "QUEUED",
        "source_path": str(source), "title": "Manual video",
        "url": "https://www.youtube.com/watch?v=abcdefghijk",
        "silence_external_id": "contentops-process-manual",
    }
    saved = []
    backend = type("Backend", (), {
        "list_jobs": staticmethod(lambda: [job]),
        "_now": staticmethod(lambda: "now"),
        "_write_job": staticmethod(lambda value: saved.append(value.copy())),
    })
    monkeypatch.setattr(lan_job_api, "_backend", lambda: backend)
    monkeypatch.setattr(lan_job_api, "_scheduler_request", lambda *_args, **_kwargs: {
        "state": "DONE", "processed_files": [], "processed_file_path": None,
    })

    lan_job_api._sync_manual_jobs_once()

    assert saved[-1]["status"] == "FAILED"
    assert saved[-1]["error"] == "PROCESSING_FAILED"


def test_remote_submission_persists_submitter_name(monkeypatch):
    import lan_job_api

    stored = []
    class Backend:
        @staticmethod
        def create_jobs(_urls):
            return [{"id": "job-name", "status": "QUEUED"}]

        @staticmethod
        def _write_job(job):
            stored.append(job)

    monkeypatch.setattr(lan_job_api, "_backend", lambda: Backend)
    monkeypatch.setattr(lan_job_api, "resolve_submitter_name", lambda _ip: "DESKTOP-TEST")
    lan_job_api.create_remote_job(
        {"url": "https://www.youtube.com/watch?v=name"},
        submitter_ip="192.168.1.20",
    )
    assert stored[0]["submitter_name"] == "DESKTOP-TEST"


def test_authorize_requires_configured_token():
    assert authorize("secret", "secret") is True
    assert authorize("wrong", "secret") is False
    assert authorize("", "secret") is False


def test_only_loopback_clients_get_tokenless_management_access():
    assert is_local_address("127.0.0.1") is True
    assert is_local_address("::1") is True
    assert is_local_address("192.168.1.20") is False


def test_validate_submission_accepts_url():
    value = validate_submission({"url": "https://www.youtube.com/watch?v=abc"})
    assert value == {"url": "https://www.youtube.com/watch?v=abc", "source_path": None}


def test_validate_submission_rejects_missing_input():
    try:
        validate_submission({})
    except ValueError as error:
        assert str(error) == "job requires url or source_path"
    else:
        raise AssertionError("missing input was accepted")
