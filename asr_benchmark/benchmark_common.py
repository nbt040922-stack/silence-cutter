from __future__ import annotations

import json
import platform
import re
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "asr_benchmark"
AUDIO = OUT / "input_16k_mono.wav"
GAPS = OUT / "whisper_speech_gaps.json"


def audio_duration(path: Path = AUDIO) -> float:
    with wave.open(str(path), "rb") as source:
        return source.getnframes() / source.getframerate()


def write_chunk(source: Path, target: Path, start: float, end: float) -> Path:
    with wave.open(str(source), "rb") as wav:
        rate = wav.getframerate()
        wav.setpos(max(0, min(wav.getnframes(), round(start * rate))))
        frames = wav.readframes(max(0, round((end - start) * rate)))
        params = wav.getparams()
    with wave.open(str(target), "wb") as chunk:
        chunk.setparams(params)
        chunk.writeframes(frames)
    return target


def gap_items() -> list[dict[str, object]]:
    data = json.loads(GAPS.read_text(encoding="utf-8"))
    return data["gaps"] if isinstance(data, dict) else data


def text_from_result(result: object) -> str:
    if isinstance(result, dict):
        return str(result.get("text") or result.get("sentence") or "")
    if isinstance(result, list):
        return "\n".join(filter(None, (text_from_result(item) for item in result)))
    return str(result or "")


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<\|[^|]+\|>", "", text)).strip()


def json_safe(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if hasattr(value, "tolist"):
        return json_safe(value.tolist())
    return str(value)


def environment() -> dict[str, object]:
    import funasr
    import huggingface_hub
    import modelscope
    import torch

    return {
        "python": sys.version,
        "platform": platform.platform(),
        "torch": torch.__version__,
        "funasr": getattr(funasr, "__version__", "unknown"),
        "modelscope": modelscope.__version__,
        "huggingface_hub": huggingface_hub.__version__,
        "cuda_available": torch.cuda.is_available(),
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "torch_cuda_runtime": torch.version.cuda,
    }
