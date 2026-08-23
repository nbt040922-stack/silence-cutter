from pathlib import Path

from silence_core.readiness import require_startup_ready, readiness_report


def test_all_pass_report_is_startup_ready(tmp_path):
    report = readiness_report(
        install_root=tmp_path,
        model_ready=True,
        gpu={"status": "PASS", "name": "RTX test", "vram_gib": 12},
        disk={"status": "PASS", "free_bytes": 100},
        ports={8780: {"status": "PASS"}, 8791: {"status": "PASS"}, 8792: {"status": "PASS"}},
        runtime={"status": "PASS", "ffmpeg": "PASS", "ffprobe": "PASS"},
    )

    require_startup_ready(report)
    assert report["status"] == "PASS"


def test_foreign_port_conflict_blocks_startup(tmp_path):
    report = readiness_report(
        install_root=tmp_path,
        model_ready=True,
        gpu={"status": "PASS"},
        disk={"status": "PASS", "free_bytes": 100},
        ports={8780: {"status": "FAIL", "reason": "PORT_CONFLICT", "pid": 99}},
        runtime={"status": "PASS", "ffmpeg": "PASS", "ffprobe": "PASS"},
    )

    assert report["status"] == "FAIL"
    try:
        require_startup_ready(report)
    except RuntimeError as exc:
        assert "PORT_CONFLICT" in str(exc)
    else:
        raise AssertionError("startup must fail on a foreign port conflict")
