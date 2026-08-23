from pathlib import Path


def test_core_payload_excludes_desktop_ui_and_qwen_weights(tmp_path):
    payload = tmp_path / "payload"
    (payload / "silence_core").mkdir(parents=True)
    (payload / "desktop").mkdir()
    (payload / "models").mkdir()

    # The packaging validator must reject both forbidden payload classes.
    from silence_core.packaging import validate_payload

    report = validate_payload(payload)

    assert report["status"] == "FAIL"
    assert "desktop" in report["forbidden"]
    assert "models" in report["forbidden"]


def test_clean_core_payload_passes(tmp_path):
    payload = tmp_path / "payload"
    (payload / "silence_core").mkdir(parents=True)
    (payload / "tools").mkdir()
    (payload / "tools" / "ffmpeg.exe").write_bytes(b"ffmpeg")
    (payload / "tools" / "ffprobe.exe").write_bytes(b"ffprobe")

    from silence_core.packaging import validate_payload

    assert validate_payload(payload)["status"] == "PASS"
