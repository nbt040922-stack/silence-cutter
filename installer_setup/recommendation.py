from typing import Iterable

from .models import ModelSpec


def recommend_qwen(specs: Iterable[ModelSpec], hardware: dict) -> ModelSpec:
    vram = float(hardware.get("vram_gb") or 0)
    cuda = bool(hardware.get("cuda_available"))
    nvenc = bool(hardware.get("nvenc_available"))
    candidates = [spec for spec in specs if spec.kind == "qwen" and spec.min_vram_gb <= vram]
    if not candidates:
        candidates = [spec for spec in specs if spec.kind == "qwen"]
    candidates.sort(key=lambda spec: spec.min_vram_gb, reverse=True)
    if not candidates:
        raise ValueError("no Qwen model candidates configured")
    if cuda and nvenc:
        return candidates[0]
    return min(candidates, key=lambda spec: spec.min_vram_gb)

