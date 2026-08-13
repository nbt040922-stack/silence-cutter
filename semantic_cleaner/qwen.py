from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from silence_cutter.runtime_paths import bundled_path, find_executable


PROMPT = """You are a conservative video segment classifier. Analyze only source time {start:.3f} to {end:.3f} seconds ({role} scan).
Return JSON only: {{"segments":[{{"type":"INTRO|AD|OUTRO|CONTENT","start":0.0,"end":0.0,"confidence":0.0,"reason":"brief evidence"}}]}}.
Timestamps must be absolute source-video seconds inside this window.
The supplied images are chronological samples distributed evenly across the window. Each image has its approximate absolute SOURCE TIME printed at the top. Inspect every image; surrounding CONTENT must not hide a brief frame with clear promotional evidence. Use the printed times and adjacent samples to estimate interval boundaries.
INTRO means a branded opening, bumper, montage, title sequence, or recurring pre-content introduction; do not label substantive early content.
AD means clear promotional intent: sponsor/product/service promotion, offer URL/QR, discount/coupon/pricing/free trial, affiliate CTA, link in description, today's sponsor, or dedicated commercial insert. Ordinary product discussion is CONTENT.
OUTRO means thanks/subscribe/follow/next-video CTA, end screen, credits, social links, or closing branded sequence; substantive conclusions are CONTENT.
When evidence is weak, return CONTENT or no segment. False positives are worse than misses.
"""


def _windows(duration: float) -> list[tuple[str, float, float]]:
    windows: list[tuple[str, float, float]] = [("INTRO", 0.0, min(90.0, duration))]
    start = 0.0
    while start < duration:
        windows.append(("AD", start, min(duration, start + 60.0)))
        start += 45.0
    windows.append(("OUTRO", max(0.0, duration - 120.0), duration))
    seen = set()
    return [item for item in windows if item[2] > item[1] and not (item in seen or seen.add(item))]


def _extract_frames(source: Path, start: float, end: float, folder: Path) -> list[Path]:
    ffmpeg = find_executable("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is unavailable")
    pattern = folder / "frame-%02d.jpg"
    sample_rate = 8.0 / (end - start)
    completed = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{end - start:.3f}",
            "-vf", f"fps={sample_rate:.9f},scale=640:-2", "-frames:v", "8", str(pattern),
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or "frame extraction failed")
    frames = sorted(folder.glob("frame-*.jpg"))
    if not frames:
        raise RuntimeError("frame extraction returned no images")
    return frames


def _json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}|\[.*\]", cleaned, flags=re.DOTALL)
        if not match:
            raise ValueError("Qwen returned no JSON value") from None
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            segments = []
            for item in re.findall(r"\{[^{}]+\}", match.group(0)):
                fields = {}
                for name in ("type", "start", "end", "confidence"):
                    pattern = rf'"{name}"\s*:\s*(?:"([^"]+)"|([-+]?\d+(?:\.\d+)?))'
                    field = re.search(pattern, item, flags=re.IGNORECASE)
                    if not field:
                        break
                    fields[name] = field.group(1) or field.group(2)
                else:
                    fields["reason"] = "recovered from malformed model JSON"
                    segments.append(fields)
            if not segments:
                raise ValueError("Qwen returned invalid JSON") from None
            value = {"segments": segments}
    if isinstance(value, list):
        value = {"segments": value}
    if not isinstance(value, dict) or not isinstance(value.get("segments"), list):
        raise ValueError(f"Qwen JSON has no segments list: {text[:500]}")
    return value


class QwenSemanticDetector:
    def __init__(self, model_reference: str | None = None) -> None:
        bundled = bundled_path("models", "Qwen2.5-VL-7B-Instruct-AWQ")
        self.model_reference = model_reference or os.environ.get("SEMANTIC_QWEN_MODEL") or (
            str(bundled) if bundled else ""
        )
        if not self.model_reference:
            raise RuntimeError("local Qwen model is not configured; set SEMANTIC_QWEN_MODEL")
        if not Path(self.model_reference).expanduser().exists():
            raise RuntimeError("SEMANTIC_QWEN_MODEL must point to a local model directory")
        loaded = time.perf_counter()
        import torch
        from transformers import AutoConfig, AutoProcessor, Qwen2_5_VLForConditionalGeneration

        if not torch.cuda.is_available():
            raise RuntimeError("Qwen semantic cleaner requires CUDA")
        torch.cuda.reset_peak_memory_stats()
        self.torch = torch
        self.processor = AutoProcessor.from_pretrained(
            self.model_reference, local_files_only=True,
        )
        model_config = AutoConfig.from_pretrained(self.model_reference, local_files_only=True)
        model_config.dtype = torch.float16
        model_config.text_config.dtype = torch.float16
        model_config.vision_config.dtype = torch.float16
        model_config.quantization_config["backend"] = "torch_awq"
        model_config.quantization_config["modules_to_not_convert"] = ["model.visual"]
        self.model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            self.model_reference, dtype=torch.float16, local_files_only=True,
            device_map="auto", config=model_config,
        ).eval().to(dtype=torch.float16)
        self.model_load_time = time.perf_counter() - loaded

    @staticmethod
    def _annotate_times(images: list[Image.Image], start: float, end: float) -> None:
        step = (end - start) / len(images)
        try:
            font = ImageFont.truetype("arial.ttf", 28)
        except OSError:
            font = ImageFont.load_default()
        for index, frame in enumerate(images):
            label = f"SOURCE TIME {start + index * step:.3f}s"
            draw = ImageDraw.Draw(frame)
            draw.rectangle((0, 0, 340, 42), fill="black")
            draw.text((8, 6), label, fill="yellow", font=font)

    def _classify(
        self, images: list[Image.Image], prompt: str, *, start: float, end: float,
    ) -> list[dict[str, Any]]:
        self._annotate_times(images, start, end)
        messages = [{
            "role": "user",
            "content": [
                *({"type": "image", "image": image} for image in images),
                {"type": "text", "text": prompt},
            ],
        }]
        text = self.processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True,
        )
        inputs = self.processor(
            text=[text], images=images, padding=True, return_tensors="pt",
        ).to(self.model.device)
        with self.torch.inference_mode():
            generated = self.model.generate(**inputs, max_new_tokens=512, do_sample=False)
        generated = generated[:, inputs.input_ids.shape[1]:]
        response = self.processor.batch_decode(
            generated, skip_special_tokens=True, clean_up_tokenization_spaces=False,
        )[0]
        return _json_object(response)["segments"]

    def detect(self, source: Path, duration: float) -> dict[str, Any]:
        started = time.perf_counter()
        segments: list[dict[str, Any]] = []
        for index, (role, start, end) in enumerate(_windows(duration)):
            with tempfile.TemporaryDirectory(prefix=f"semantic-{index:03d}-") as directory:
                paths = _extract_frames(source, start, end, Path(directory))
                images = []
                try:
                    images = [Image.open(path).convert("RGB") for path in paths]
                    segments.extend(self._classify(
                        images, PROMPT.format(start=start, end=end, role=role),
                        start=start, end=end,
                    ))
                finally:
                    for image in images:
                        image.close()
        return {
            "model": self.model_reference,
            "runtime": "transformers + GPTQModel AWQ_TORCH (CUDA, local-only)",
            "segments": segments,
            "model_load_time": self.model_load_time,
            "semantic_scan_time": time.perf_counter() - started,
            "peak_vram_bytes": self.torch.cuda.max_memory_allocated(),
        }
