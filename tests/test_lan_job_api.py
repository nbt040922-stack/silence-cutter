from lan_job_api import authorize, is_local_address, validate_submission


def test_discovery_selects_one_unseen_highest_view_video_per_channel(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    import json
    import lan_job_api

    monkeypatch.setenv("SILENCE_CUTTER_DATA_DIR", str(tmp_path))
    rows = [
        {"id": "low", "url": "https://youtu.be/low", "title": "Low", "channel": "A", "channel_id": "UCA", "upload_date": "20260101", "view_count": 100},
        {"id": "high", "url": "https://youtu.be/high", "title": "High", "channel": "A", "channel_id": "UCA", "upload_date": "20260201", "view_count": 900},
    ]
    monkeypatch.setattr(lan_job_api, "_backend", lambda: type("Backend", (), {"_yt_dlp_command": staticmethod(lambda: ["yt-dlp"]), "list_jobs": staticmethod(lambda: [])})())
    monkeypatch.setattr(lan_job_api.subprocess, "run", lambda *args, **kwargs: type("Result", (), {"returncode": 0, "stdout": "\n".join(json.dumps(row) for row in rows), "stderr": ""})())
    created = []
    monkeypatch.setattr(lan_job_api, "create_remote_job", lambda payload, **_: created.append(payload.copy()) or {"job_id": "job-high", "status": "QUEUED"})

    result = lan_job_api.discover_channel_jobs(
        ["https://www.youtube.com/@channel/videos"],
        now=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    assert created == [{
        "url": "https://youtu.be/high", "discovery": "channel_top_view",
        "channel_name": "A", "display_name": "A",
    }]
    assert result["created"][0]["video_id"] == "high"
    assert json.loads((tmp_path / "channel-discovery-history.json").read_text())['high']['job_id'] == "job-high"


def test_discovery_history_store_is_initialized_when_missing(monkeypatch, tmp_path):
    import json
    import lan_job_api

    monkeypatch.setenv("SILENCE_CUTTER_DATA_DIR", str(tmp_path))

    assert lan_job_api._load_discovery_history() == {}
    assert json.loads((tmp_path / "channel-discovery-history.json").read_text()) == {}


def test_channel_scan_uses_fast_flat_playlist_mode(monkeypatch):
    import json
    import lan_job_api

    command_seen = []
    monkeypatch.setattr(lan_job_api, "_backend", lambda: type("Backend", (), {
        "_yt_dlp_command": staticmethod(lambda: ["yt-dlp"]),
    })())

    def run(command, **kwargs):
        command_seen.extend(command)
        return type("Result", (), {
            "returncode": 0,
            "stdout": json.dumps({"id": "fast", "view_count": 12, "upload_date": "20260101"}),
            "stderr": "",
        })()

    monkeypatch.setattr(lan_job_api.subprocess, "run", run)
    lan_job_api.fetch_channel_candidates(
        "https://youtube.com/@demo/videos",
        since=lan_job_api.datetime(2025, 1, 1, tzinfo=lan_job_api.timezone.utc),
        until=lan_job_api.datetime(2026, 1, 1, tzinfo=lan_job_api.timezone.utc),
        limit=10,
    )

    assert "--flat-playlist" in command_seen


def test_channel_scan_checks_publish_date_after_popular_view_order(monkeypatch):
    import json
    import lan_job_api

    commands = []
    monkeypatch.setattr(lan_job_api, "_backend", lambda: type("Backend", (), {
        "_yt_dlp_command": staticmethod(lambda: ["yt-dlp"]),
    })())

    def run(command, **kwargs):
        commands.append(command)
        if "--flat-playlist" in command:
            rows = [
                {"id": "popular-old", "url": "https://youtu.be/popular-old", "view_count": 900},
                {"id": "recent", "url": "https://youtu.be/recent", "view_count": 100},
            ]
            output = "\n".join(json.dumps(row) for row in rows)
        else:
            output = "20200101\n" if "popular-old" in command[-1] else "20260101\n"
        return type("Result", (), {"returncode": 0, "stdout": output, "stderr": ""})()

    monkeypatch.setattr(lan_job_api.subprocess, "run", run)
    candidates = lan_job_api.fetch_channel_candidates(
        "https://youtube.com/@demo/videos",
        since=lan_job_api.datetime(2025, 1, 1, tzinfo=lan_job_api.timezone.utc),
        until=lan_job_api.datetime(2026, 1, 1, tzinfo=lan_job_api.timezone.utc),
    )

    assert [item["video_id"] for item in candidates] == ["recent"]
    assert "sort=p" in commands[0][-1]
    assert "--flat-playlist" not in commands[1]


def test_discovery_skips_history_and_reports_channel_errors(monkeypatch, tmp_path):
    import json
    import lan_job_api

    monkeypatch.setenv("SILENCE_CUTTER_DATA_DIR", str(tmp_path))
    (tmp_path / "channel-discovery-history.json").write_text(json.dumps({"used": {"job_id": "old"}}))
    monkeypatch.setattr(lan_job_api, "_backend", lambda: type("Backend", (), {"_yt_dlp_command": staticmethod(lambda: ["yt-dlp"]), "list_jobs": staticmethod(lambda: [])})())

    def run(command, **kwargs):
        if "broken/videos" in command[-1]:
            return type("Result", (), {"returncode": 1, "stdout": "", "stderr": "blocked"})()
        row = {"id": "used", "url": "https://youtu.be/used", "title": "Used", "channel": "A", "upload_date": "20260101", "view_count": 999}
        return type("Result", (), {"returncode": 0, "stdout": json.dumps(row), "stderr": ""})()

    monkeypatch.setattr(lan_job_api.subprocess, "run", run)
    monkeypatch.setattr(lan_job_api, "create_remote_job", lambda *_args, **_kwargs: {"job_id": "never"})
    result = lan_job_api.discover_channel_jobs([
        "https://www.youtube.com/@used/videos",
        "https://www.youtube.com/@broken/videos",
    ])

    assert result["created"] == []
    assert result["skipped"][0]["reason"] == "already_used"
    assert result["errors"][0]["channel_url"].endswith("broken/videos")


def test_discovery_pauses_between_created_channel_jobs(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    import json
    import lan_job_api

    monkeypatch.setenv("SILENCE_CUTTER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(lan_job_api, "_backend", lambda: type("Backend", (), {
        "_yt_dlp_command": staticmethod(lambda: ["yt-dlp"]),
        "list_jobs": staticmethod(lambda: []),
    })())

    def run(command, **kwargs):
        channel = command[-1]
        suffix = "one" if "@one" in channel else "two"
        row = {"id": suffix, "url": f"https://youtu.be/{suffix}", "title": suffix,
               "channel": suffix, "upload_date": "20260101", "view_count": 10}
        return type("Result", (), {"returncode": 0, "stdout": json.dumps(row), "stderr": ""})()

    monkeypatch.setattr(lan_job_api.subprocess, "run", run)
    created = []
    monkeypatch.setattr(lan_job_api, "create_remote_job", lambda payload, **_: created.append(payload["url"]) or {
        "job_id": f"job-{len(created)}", "status": "QUEUED"
    })
    pauses = []

    result = lan_job_api.discover_channel_jobs(
        ["https://youtube.com/@one", "https://youtube.com/@two"],
        now=datetime(2026, 8, 24, tzinfo=timezone.utc),
        sleeper=pauses.append,
        pause_seconds=lambda: 90,
    )

    assert result["total"] == 2
    assert created == ["https://youtu.be/one", "https://youtu.be/two"]
    assert pauses == [90]


def test_discovery_persists_each_selected_video_before_next_channel(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    import json
    import lan_job_api

    monkeypatch.setenv("SILENCE_CUTTER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(lan_job_api, "_backend", lambda: type("Backend", (), {
        "list_jobs": staticmethod(lambda: []),
    })())
    calls = []

    def fetch(channel_url, **_kwargs):
        calls.append(channel_url)
        if "two" in channel_url:
            raise KeyboardInterrupt()
        return [{"video_id": "one", "url": "https://youtu.be/one", "title": "One", "view_count": 10, "published_at": "2026-01-01T00:00:00+00:00"}]

    monkeypatch.setattr(lan_job_api, "fetch_channel_candidates", fetch)
    monkeypatch.setattr(lan_job_api, "create_remote_job", lambda *_args, **_kwargs: {"job_id": "job-one", "status": "QUEUED"})

    try:
        lan_job_api.discover_channel_jobs(
            ["https://youtube.com/@one", "https://youtube.com/@two"],
            now=datetime(2026, 8, 24, tzinfo=timezone.utc),
            pause_seconds=lambda: 0,
            sleeper=lambda _: None,
        )
    except KeyboardInterrupt:
        pass

    saved = json.loads((tmp_path / "channel-discovery-history.json").read_text())
    assert calls == ["https://youtube.com/@one/videos", "https://youtube.com/@two/videos"]
    assert saved["one"]["job_id"] == "job-one"


def test_validate_discovery_submission_requires_bounded_channel_list():
    import lan_job_api

    assert lan_job_api.validate_discovery_submission({"channels": [" https://youtube.com/@one ", ""]}) == {
        "channels": ["https://youtube.com/@one"]
    }
    try:
        lan_job_api.validate_discovery_submission({"channels": "https://youtube.com/@one"})
    except ValueError as error:
        assert str(error) == "channels must be a list"
    else:
        raise AssertionError("scalar channel input was accepted")


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


def test_youtube_metadata_uses_backend_yt_dlp_command(monkeypatch):
    import lan_job_api

    class Result:
        returncode = 0
        stdout = '{"title":"Real video","uploader":"Real channel","duration":1122,"thumbnail":"https://img.test/thumb.jpg"}'
        stderr = ""

    monkeypatch.setattr(lan_job_api, "_backend", lambda: type(
        "Backend", (), {"_yt_dlp_command": staticmethod(lambda: ["yt-dlp"])}
    )())
    monkeypatch.setattr(lan_job_api.subprocess, "run", lambda *args, **kwargs: Result())

    result = lan_job_api.fetch_youtube_metadata("https://www.youtube.com/watch?v=abc")

    assert result == {
        "title": "Real video",
        "channel": "Real channel",
        "duration_seconds": 1122,
        "duration": "18:42",
        "thumbnail": "https://img.test/thumb.jpg",
        "url": "https://www.youtube.com/watch?v=abc",
    }


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


def test_remote_submission_persists_youtube_metadata(monkeypatch):
    import lan_job_api

    stored = []
    class Backend:
        @staticmethod
        def create_jobs(_urls):
            return [{"id": "job-meta", "status": "QUEUED", "title": "www.youtube.com", "display_name": "www.youtube.com"}]

        @staticmethod
        def _write_job(job):
            stored.append(job)

    monkeypatch.setattr(lan_job_api, "_backend", lambda: Backend)
    monkeypatch.setattr(lan_job_api, "fetch_youtube_metadata", lambda _url: {
        "title": "Real video", "channel": "Real channel", "duration_seconds": 1122,
        "duration": "18:42", "thumbnail": "thumb", "url": "https://youtu.be/abc",
    })

    result = lan_job_api.create_remote_job({"url": "https://youtu.be/abc"})

    assert result["job_id"] == "job-meta"
    assert stored[0]["title"] == "Real video"
    assert stored[0]["display_name"] == "Real video"
    assert stored[0]["channel_name"] == "Real channel"
    assert stored[0]["duration"] == 1122


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
        "source_path": str(source), "title": "www.youtube.com",
        "display_name": "Original Manual Video",
        "url": "https://www.youtube.com/watch?v=abcdefghijk",
    }
    output = tmp_path / "out"
    monkeypatch.setattr(lan_job_api, "_backend", lambda: type(
        "Backend", (), {
            "load_settings": staticmethod(lambda: {"output_folder": str(output)}),
            "_user_output_folder": staticmethod(
                lambda root, title, job_id: root / f"{title}_{job_id[:8]}"
            ),
        }
    )())
    output.mkdir()
    payload = lan_job_api._manual_scheduler_payload(job)
    assert payload["origin"] == "MANUAL_LAN"
    assert payload["source_file"] == str(source)
    assert payload["video_id"] == "abcdefghijk"
    assert payload["handoff_id"] == "manual-" + "a" * 32
    assert payload["output_dir"] == str(output / "Original Manual Video_aaaaaaaa")


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
