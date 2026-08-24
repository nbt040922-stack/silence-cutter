from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ModelFile:
    name: str
    size_bytes: int
    sha256: str
    url: str


@dataclass(frozen=True)
class ModelManifest:
    model: str
    revision: str
    files: tuple[ModelFile, ...]

    @classmethod
    def from_file(cls, path: Path) -> "ModelManifest":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8-sig")))

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ModelManifest":
        model = str(value.get("model") or value.get("name") or "").strip()
        revision = str(value.get("revision") or "").strip()
        raw_files = value.get("files")
        if not model or not revision or not isinstance(raw_files, list) or not raw_files:
            raise ValueError("manifest requires model, revision, and files")
        files: list[ModelFile] = []
        for raw in raw_files:
            if not isinstance(raw, dict):
                raise ValueError("manifest file entry must be an object")
            name = str(raw.get("name") or "").replace("\\", "/").strip("/")
            digest = str(raw.get("sha256") or "").lower()
            url = str(raw.get("url") or "").strip()
            try:
                size = int(raw.get("size_bytes"))
            except (TypeError, ValueError):
                size = -1
            if not name or Path(name).is_absolute() or ".." in Path(name).parts:
                raise ValueError("manifest contains unsafe file name")
            if size < 0 or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("manifest file requires size_bytes and sha256")
            if not (url.startswith("https://") or url.startswith("http://") or url.startswith("file://")):
                raise ValueError("manifest file requires an HTTPS download URL")
            files.append(ModelFile(name, size, digest, url))
        return cls(model, revision, tuple(files))


@dataclass(frozen=True)
class ModelInstallResult:
    status: str
    downloaded_bytes: int
    expected_bytes: int
    resume_supported: bool
    reason: str | None = None
    log_path: str | None = None


class ModelManager:
    def __init__(self, root: Path, log_path: Path | None = None):
        self.root = root.resolve()
        self.log_path = log_path

    def check_model(self, manifest: ModelManifest) -> bool:
        return all(self._valid_file(item) for item in manifest.files)

    def ensure_model(self, manifest: ModelManifest) -> ModelInstallResult:
        expected = sum(item.size_bytes for item in manifest.files)
        if self.check_model(manifest):
            self._write_log("MODEL DOWNLOAD = SKIPPED")
            return ModelInstallResult("SKIPPED", expected, expected, True)
        return self.repair_model(manifest)

    def repair_model(self, manifest: ModelManifest) -> ModelInstallResult:
        expected = sum(item.size_bytes for item in manifest.files)
        self.root.mkdir(parents=True, exist_ok=True)
        downloaded = 0
        resume_supported = True
        try:
            # Keep headroom for .part files and filesystem metadata; never
            # start a multi-gigabyte download that cannot finish safely.
            free_bytes = shutil.disk_usage(self.root).free
            if free_bytes < expected + 512 * 1024 * 1024:
                raise OSError(
                    f"insufficient disk space: free={free_bytes}, required={expected + 512 * 1024 * 1024}"
                )
            for item in manifest.files:
                target = self.root / item.name
                target.parent.mkdir(parents=True, exist_ok=True)
                if self._valid_file(item):
                    downloaded += item.size_bytes
                    continue
                downloaded += self._download(item, target)
            if not self.check_model(manifest):
                raise ValueError("post-download manifest verification failed")
            self._write_log(f"MODEL DOWNLOAD = COMPLETE ({downloaded}/{expected})")
            return ModelInstallResult("DOWNLOADED", downloaded, expected, resume_supported)
        except Exception as exc:
            self._write_log(
                "QWEN MODEL INSTALL FAILED\n"
                f"Reason: {type(exc).__name__}: {exc}\n"
                f"Downloaded: {downloaded}\nExpected: {expected}\n"
                f"Resume supported: {resume_supported}\nLog: {self.log_path or 'none'}"
            )
            return ModelInstallResult(
                "FAILED", downloaded, expected, resume_supported,
                f"{type(exc).__name__}: {exc}", str(self.log_path) if self.log_path else None,
            )

    def _valid_file(self, item: ModelFile) -> bool:
        path = self.root / item.name
        return self._valid_path(path, item)

    @staticmethod
    def _valid_path(path: Path, item: ModelFile) -> bool:
        if not path.is_file() or path.stat().st_size != item.size_bytes:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest() == item.sha256

    def _download(self, item: ModelFile, target: Path) -> int:
        partial = target.with_name(target.name + ".part")
        existing = partial.stat().st_size if partial.exists() else 0
        headers = {"Range": f"bytes={existing}-"} if existing else {}
        request = urllib.request.Request(item.url, headers=headers)
        try:
            response = urllib.request.urlopen(request, timeout=120)
        except urllib.error.HTTPError as exc:
            if existing and exc.code == 416:
                partial.unlink(missing_ok=True)
                return self._download(item, target)
            raise
        status = getattr(response, "status", None)
        append = bool(existing and status == 206)
        if existing and not append:
            existing = 0
            partial.unlink(missing_ok=True)
        mode = "ab" if append else "wb"
        total = existing
        with response, partial.open(mode) as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                total += len(chunk)
        if total != item.size_bytes:
            raise ValueError(f"size mismatch for {item.name}: {total} != {item.size_bytes}")
        if not self._valid_path(partial, item):
            raise ValueError(f"sha256 mismatch for {item.name}")
        os.replace(partial, target)
        return total

    def _write_log(self, message: str) -> None:
        if not self.log_path:
            return
        candidates = [self.log_path]
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(
                Path(local_app_data)
                / "ContentOps" / "SilenceCore" / "logs" / "installer" / "model.log"
            )
        for path in candidates:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as stream:
                    stream.write(message.rstrip() + "\n")
                return
            except OSError:
                continue
