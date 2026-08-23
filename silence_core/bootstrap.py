from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RuntimeArtifact:
    name: str
    url: str
    sha256: str
    size: int | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RuntimeArtifact":
        name = str(value.get("name") or "").strip()
        url = str(value.get("url") or "").strip()
        sha256 = str(value.get("sha256") or "").strip().lower()
        if not name or not url or len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise ValueError("runtime artifact requires name, url and SHA256")
        size = value.get("size")
        return cls(name=name, url=url, sha256=sha256, size=int(size) if size is not None else None)


def verify_file(path: Path, expected_sha256: str, expected_size: int | None = None) -> bool:
    if not path.is_file():
        return False
    if expected_size is not None and path.stat().st_size != expected_size:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower() == expected_sha256.lower()


def load_manifest(path: Path) -> list[RuntimeArtifact]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [RuntimeArtifact.from_dict(item) for item in payload.get("artifacts", [])]


def download_artifact(artifact: RuntimeArtifact, destination: Path) -> Path:
    """Download with HTTP resume, then atomically publish after verification."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    offset = partial.stat().st_size if partial.exists() else 0
    request = urllib.request.Request(artifact.url)
    if offset:
        request.add_header("Range", f"bytes={offset}-")
    try:
        response = urllib.request.urlopen(request, timeout=60)
        mode = "ab" if offset and getattr(response, "status", 200) == 206 else "wb"
        if mode == "wb":
            offset = 0
        with partial.open(mode) as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
    except Exception:
        raise
    if not verify_file(partial, artifact.sha256, artifact.size):
        raise RuntimeError(f"checksum mismatch: {artifact.name}")
    os.replace(partial, destination)
    return destination
