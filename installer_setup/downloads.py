import json
import sys
from pathlib import Path
from typing import Callable

from .inventory import discover_models
from .models import ModelRecord, ModelSpec
from .recommendation import recommend_qwen


def _spec_from_dict(value: dict) -> ModelSpec:
    return ModelSpec(
        kind=str(value["kind"]), name=str(value["name"]),
        required_files=tuple(value.get("required_files") or ()),
        min_vram_gb=float(value.get("min_vram_gb") or 0),
        source=value.get("source"), revision=str(value.get("revision") or "main"),
    )


def download_model(
    spec: ModelSpec,
    root: Path,
    *,
    downloader: Callable[..., str] | None = None,
) -> ModelRecord:
    if not spec.source:
        return ModelRecord(spec, "missing", code="MODEL_SOURCE_NOT_CONFIGURED")
    if downloader is None:
        from huggingface_hub import snapshot_download
        downloader = snapshot_download
    target = Path(root) / spec.name
    downloader(
        repo_id=spec.source,
        revision=spec.revision,
        local_dir=str(target),
    )
    return discover_models([spec], [Path(root)])[0]


def ensure_qwen_model(
    manifest_path: Path,
    model_root: Path,
    *,
    hardware: dict | None = None,
    downloader: Callable[..., str] | None = None,
) -> ModelRecord:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    values = [_spec_from_dict(item) for item in manifest.get("models", [])]
    specs = [item for item in values if item.kind == "qwen"]
    if not specs:
        raise RuntimeError("Qwen model is not configured")
    selected = recommend_qwen(specs, hardware or _runtime_hardware())
    existing = discover_models([selected], [Path(model_root)])
    if existing[0].status == "verified":
        return existing[0]
    print(
        f"Qwen download: {selected.name} from {selected.source}",
        file=sys.stderr, flush=True,
    )
    return download_model(selected, Path(model_root), downloader=downloader)


def _runtime_hardware() -> dict:
    try:
        import torch
        if not torch.cuda.is_available():
            return {"cuda_available": False, "vram_gb": 0.0}
        props = torch.cuda.get_device_properties(0)
        return {"cuda_available": True, "vram_gb": props.total_memory / (1024 ** 3)}
    except Exception:
        return {"cuda_available": False, "vram_gb": 0.0}
