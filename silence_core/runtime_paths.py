from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CorePaths:
    install_root: Path
    data_root: Path

    @classmethod
    def from_environment(cls) -> "CorePaths":
        packaged = os.getenv("SILENCE_CORE_PACKAGED", "").strip() == "1"
        if packaged:
            install = Path(
                os.getenv(
                    "SILENCE_CORE_INSTALL_ROOT",
                    r"C:\Program Files\ContentOps\SilenceCore",
                )
            )
            data = Path(
                os.getenv(
                    "SILENCE_CORE_DATA_ROOT",
                    r"C:\ProgramData\ContentOps\SilenceCore",
                )
            )
        else:
            install = Path(os.getenv("SILENCE_CORE_INSTALL_ROOT", Path(__file__).resolve().parents[1]))
            data = Path(os.getenv("SILENCE_CORE_DATA_ROOT", install / "workspace"))
        return cls(install.expanduser().resolve(), data.expanduser().resolve())

    @property
    def model_root(self) -> Path:
        return self.data_root / "models"

    @property
    def model_path(self) -> Path:
        override = os.getenv("SILENCE_CORE_MODEL_DIR", "").strip()
        return Path(override).resolve() if override else self.model_root / "qwen2.5-vl-7b"

    @property
    def config_root(self) -> Path:
        return self.data_root / "config"

    @property
    def state_root(self) -> Path:
        return self.data_root / "state"

    @property
    def queue_root(self) -> Path:
        return self.data_root / "queue"

    @property
    def log_root(self) -> Path:
        return self.data_root / "logs"

    @property
    def workspace_root(self) -> Path:
        return self.data_root / "workspace"

    @property
    def ffmpeg(self) -> Path:
        return self.install_root / "tools" / "ffmpeg.exe"

    @property
    def ffprobe(self) -> Path:
        return self.install_root / "tools" / "ffprobe.exe"

    def ensure_data_layout(self) -> None:
        for path in (
            self.config_root,
            self.state_root,
            self.queue_root,
            self.log_root,
            self.workspace_root,
            self.model_root,
        ):
            path.mkdir(parents=True, exist_ok=True)
