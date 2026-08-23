from pathlib import Path
from typing import Iterable, List

from .models import ModelRecord, ModelSpec


def _is_valid(path: Path, spec: ModelSpec) -> bool:
    return path.is_dir() and all((path / item).is_file() for item in spec.required_files)


def discover_models(specs: Iterable[ModelSpec], roots: Iterable[Path]) -> List[ModelRecord]:
    records: List[ModelRecord] = []
    root_list = [Path(root) for root in roots]
    for spec in specs:
        match = None
        for root in root_list:
            candidate = root / spec.name
            if _is_valid(candidate, spec):
                match = candidate
                break
        if match is not None:
            records.append(ModelRecord(spec, "verified", match))
        else:
            code = "MODEL_SOURCE_NOT_CONFIGURED" if not spec.source else "MODEL_MISSING"
            records.append(ModelRecord(spec, "missing", code=code))
    return records

