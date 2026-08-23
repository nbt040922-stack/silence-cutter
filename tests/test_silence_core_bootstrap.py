import hashlib
import json
from pathlib import Path

import pytest

from silence_core.bootstrap import RuntimeArtifact, verify_file


def test_verify_file_accepts_matching_sha256(tmp_path):
    path = tmp_path / "runtime.bin"
    path.write_bytes(b"runtime")
    digest = hashlib.sha256(b"runtime").hexdigest()

    assert verify_file(path, digest) is True


def test_verify_file_rejects_corrupt_payload(tmp_path):
    path = tmp_path / "runtime.bin"
    path.write_bytes(b"corrupt")

    assert verify_file(path, "0" * 64) is False


def test_runtime_artifact_requires_url_and_checksum():
    with pytest.raises(ValueError):
        RuntimeArtifact.from_dict({"name": "torch", "url": "https://example.invalid/torch.whl"})


def test_runtime_artifact_round_trips_manifest():
    value = {
        "name": "uv",
        "url": "https://example.invalid/uv.exe",
        "sha256": "a" * 64,
        "size": 12,
    }
    artifact = RuntimeArtifact.from_dict(value)

    assert artifact.name == "uv"
    assert artifact.sha256 == "a" * 64
    assert artifact.size == 12
