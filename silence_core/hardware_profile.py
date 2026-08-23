from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QwenProfile:
    name: str
    model_manifest: str
    model_directory: str


def select_qwen_profile(vram_mib: int) -> QwenProfile:
    if vram_mib >= 10 * 1024:
        return QwenProfile(
            "qwen7b",
            "core_model_manifest.json",
            "qwen2.5-vl-7b",
        )
    if vram_mib >= 6 * 1024:
        return QwenProfile(
            "qwen3b",
            "core_model_manifest_3b.json",
            "qwen2.5-vl-3b",
        )
    raise ValueError(f"GPU VRAM too low for Qwen: {vram_mib} MiB")
