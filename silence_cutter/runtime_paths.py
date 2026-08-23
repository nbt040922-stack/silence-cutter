from __future__ import annotations

import os
import shutil
from pathlib import Path


def resource_dir() -> Path | None:
    value = os.environ.get("SILENCE_CUTTER_RESOURCE_DIR")
    return Path(value).expanduser().resolve() if value else None


def bundled_path(*parts: str) -> Path | None:
    root = resource_dir()
    if root is None:
        return None
    path = root.joinpath(*parts)
    return path if path.exists() else None


def find_executable(name: str) -> str | None:
    suffix = ".exe" if os.name == "nt" else ""
    bundled = bundled_path("bin", f"{name}{suffix}")
    return str(bundled) if bundled and bundled.is_file() else shutil.which(name)


def model_reference(directory: str, remote_reference: str) -> str:
    bundled = bundled_path("models", directory)
    if bundled and bundled.is_dir():
        return str(bundled)
    data_root = os.environ.get("SILENCE_CUTTER_DATA_DIR")
    if data_root:
        user_model = Path(data_root).expanduser() / "models" / directory
        if user_model.is_dir():
            return str(user_model)
    return remote_reference
