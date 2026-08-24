import hashlib
import json
from pathlib import Path

import pytest

from silence_core.model_manager import ModelManifest, ModelManager


def _manifest(source: Path, digest: str, size: int) -> ModelManifest:
    return ModelManifest.from_dict({
        "model": "test-model",
        "revision": "test",
        "files": [{
            "name": "weights.bin",
            "size_bytes": size,
            "sha256": digest,
            "url": source.as_uri(),
        }],
    })


def test_valid_existing_model_is_skipped(tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"valid model")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    target = tmp_path / "model"
    target.mkdir()
    (target / "weights.bin").write_bytes(source.read_bytes())

    result = ModelManager(target).ensure_model(_manifest(source, digest, source.stat().st_size))

    assert result.status == "SKIPPED"
    assert result.downloaded_bytes == result.expected_bytes == source.stat().st_size


def test_invalid_payload_is_replaced(tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"correct")
    target = tmp_path / "model"
    target.mkdir()
    (target / "weights.bin").write_bytes(b"corrupt")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()

    result = ModelManager(target).ensure_model(_manifest(source, digest, source.stat().st_size))

    assert result.status == "DOWNLOADED"
    assert (target / "weights.bin").read_bytes() == b"correct"


def test_manifest_rejects_invalid_hash():
    with pytest.raises(ValueError, match="sha256"):
        ModelManifest.from_dict({
            "model": "x", "revision": "r",
            "files": [{"name": "x", "size_bytes": 1, "sha256": "bad", "url": "https://example/x"}],
        })


def test_manifest_accepts_utf8_bom_file(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({
        "model": "x", "revision": "r",
        "files": [{
            "name": "x", "size_bytes": 1,
            "sha256": "0" * 64, "url": "https://example/x",
        }],
    }), encoding="utf-8-sig")

    assert ModelManifest.from_file(path).model == "x"


def test_log_write_falls_back_when_programdata_log_is_denied(tmp_path, monkeypatch):
    primary = tmp_path / "programdata" / "model.log"
    fallback = tmp_path / "localappdata" / "ContentOps" / "SilenceCore" / "logs" / "installer" / "model.log"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    original_open = Path.open

    def open_with_denied_primary(path, *args, **kwargs):
        if path == primary:
            raise PermissionError("denied")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_with_denied_primary)
    ModelManager(tmp_path / "model", primary)._write_log("ok")

    assert fallback.read_text(encoding="utf-8") == "ok\n"
