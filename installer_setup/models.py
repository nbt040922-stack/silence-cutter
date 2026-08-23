from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


@dataclass(frozen=True)
class ModelSpec:
    kind: str
    name: str
    required_files: Tuple[str, ...] = ()
    min_vram_gb: float = 0.0
    source: Optional[str] = None
    revision: str = "main"


@dataclass(frozen=True)
class ModelRecord:
    spec: ModelSpec
    status: str
    path: Optional[Path] = None
    code: Optional[str] = None
