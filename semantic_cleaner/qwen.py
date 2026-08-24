from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat

from installer_setup.downloads import ensure_qwen_model
from silence_cutter.runtime_paths import bundled_path, find_executable


FINE_PROMPT = """Conservative final semantic scan. Full source duration is {duration:.1f}s. Every contact-sheet cell shows absolute SOURCE TIME.
Output ONLY CSV lines: TYPE,START,END,CONFIDENCE. TYPE is INTRO, AD, or OUTRO. Output AD first, then INTRO, then OUTRO. If none, output NONE. No prose, JSON, header, reason, units, or CONTENT line. Merge contiguous evidence; maximum one line per type.
INTRO=branded sequence before real content, never an ordinary host already doing the video's main activity. AD=clear sponsor/offer/promo/URL/QR/discount/affiliate/commercial intent; ordinary product discussion is not AD. OUTRO=closing subscribe/follow/social CTA, thanks, end screen or credits, especially inside the final 120s; these closing signals are never AD. Position alone is not evidence. Use precise absolute times printed on cells. Weak evidence => NONE. False positives are worse than misses."""


def _windows(duration: float) -> list[tuple[str, float, float]]:
    """Legacy baseline windows retained for benchmark comparison."""
    windows: list[tuple[str, float, float]] = [("INTRO", 0.0, min(90.0, duration))]
    start = 0.0
    while start < duration:
        windows.append(("AD", start, min(duration, start + 60.0)))
        start += 45.0
    windows.append(("OUTRO", max(0.0, duration - 120.0), duration))
    seen = set()
    return [item for item in windows if item[2] > item[1] and not (item in seen or seen.add(item))]


def _extract_sampled_frames(
    source: Path, start: float, end: float, interval: float, folder: Path,
) -> tuple[list[Path], list[float]]:
    ffmpeg = find_executable("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is unavailable")
    folder.mkdir(parents=True, exist_ok=True)
    pattern = folder / "frame-%05d.jpg"
    probe = find_executable("ffprobe")
    hardware_command = None
    if probe:
        metadata = subprocess.run(
            [probe, "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=codec_name,avg_frame_rate", "-of", "json", str(source)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        try:
            stream = json.loads(metadata.stdout)["streams"][0]
            numerator, denominator = map(float, stream["avg_frame_rate"].split("/"))
            step = max(1, round(interval * numerator / denominator))
            decoder = {"av1": "av1_cuvid", "h264": "h264_cuvid", "hevc": "hevc_cuvid"}.get(
                stream["codec_name"],
            )
            if decoder:
                hardware_command = [
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                    "-hwaccel", "cuda", "-hwaccel_output_format", "cuda", "-c:v", decoder,
                    "-resize", "320x180", "-ss", f"{start:.3f}", "-i", str(source),
                    "-t", f"{end - start:.3f}", "-vf",
                    f"framestep={step},hwdownload,format=nv12,format=yuvj420p",
                    "-q:v", "3", str(pattern),
                ]
        except (KeyError, IndexError, ValueError, ZeroDivisionError, json.JSONDecodeError):
            pass
    command = hardware_command or [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{end - start:.3f}",
        "-vf", f"fps=1/{interval:.6f},scale=480:-2", "-q:v", "3", str(pattern),
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if completed.returncode and hardware_command:
        completed = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
             "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{end - start:.3f}",
             "-vf", f"fps=1/{interval:.6f},scale=480:-2", "-q:v", "3", str(pattern)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or "frame extraction failed")
    paths = sorted(folder.glob("frame-*.jpg"))
    if not paths:
        raise RuntimeError("frame extraction returned no images")
    timestamps = [min(end, start + index * interval) for index in range(len(paths))]
    return paths, timestamps


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _contact_sheets(
    paths: list[Path], timestamps: list[float], *, cells: int = 16,
    columns: int = 4, cell_width: int = 240, cell_height: int = 150,
) -> list[Image.Image]:
    sheets: list[Image.Image] = []
    for offset in range(0, len(paths), cells):
        chunk_paths = paths[offset:offset + cells]
        chunk_times = timestamps[offset:offset + cells]
        rows = math.ceil(len(chunk_paths) / columns)
        sheet = Image.new("RGB", (columns * cell_width, rows * cell_height), "black")
        for local, (path, timestamp) in enumerate(zip(
            chunk_paths, chunk_times, strict=True,
        )):
            with Image.open(path) as source:
                frame = source.convert("RGB")
                frame.thumbnail((cell_width, cell_height - 28))
            x, y = local % columns * cell_width, local // columns * cell_height
            sheet.paste(frame, (x + (cell_width - frame.width) // 2, y + 28))
            draw = ImageDraw.Draw(sheet)
            draw.text(
                (x + 4, y + 3), f"TIME {timestamp:.1f}s", fill="yellow",
                font=_font(18 if cell_width >= 240 else 13),
            )
        sheets.append(sheet)
    return sheets


def _candidate_windows(
    segments: list[dict[str, Any]], duration: float, context: float = 15.0,
) -> list[tuple[float, float]]:
    candidates = []
    for item in segments:
        try:
            label = str(item["type"]).upper()
            start, end = float(item["start"]), float(item["end"])
            score = float(item["confidence"])
        except (KeyError, TypeError, ValueError):
            continue
        if label not in {"INTRO", "AD", "OUTRO"} or score < 0.5 or start >= end:
            continue
        candidates.append((max(0.0, start - context), min(duration, end + context)))
    merged: list[list[float]] = []
    for start, end in sorted(candidates):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _visual_candidates(
    paths: list[Path], timestamps: list[float], duration: float,
) -> tuple[list[dict[str, Any]], list[tuple[float, float]]]:
    changes: list[tuple[float, float]] = []
    previous = None
    for path, timestamp in zip(paths, timestamps, strict=True):
        with Image.open(path) as image:
            current = image.convert("L").resize((96, 54))
        if previous is not None:
            score = ImageStat.Stat(ImageChops.difference(previous, current)).mean[0]
            changes.append((timestamp, score))
        previous = current
    if not changes:
        return [], []
    ordered = sorted(score for _, score in changes)
    median = ordered[(len(ordered) - 1) // 2]
    threshold = max(18.0, median * 1.8)
    eligible = [(timestamp, score) for timestamp, score in changes if score >= threshold]
    selected = sorted(eligible, key=lambda item: item[1], reverse=True)[:4]
    for region in (
        [item for item in eligible if item[0] <= 90.0],
        [item for item in eligible if item[0] >= duration - 120.0],
    ):
        if region:
            selected.append(max(region, key=lambda item: item[1]))
    selected = list({timestamp: (timestamp, score) for timestamp, score in selected}.values())
    coarse = [{
        "type": "VISUAL_CANDIDATE", "start": max(0.0, timestamp - 10.0),
        "end": min(duration, timestamp + 10.0), "confidence": max(0.5, min(1.0, score / 64.0)),
        "reason": "coarse visual transition",
    } for timestamp, score in sorted(selected)]
    windows = _candidate_windows([
        {**item, "type": "AD"} for item in coarse
    ], duration, context=15.0)
    return coarse, windows


def _align_to_visual_transitions(
    segments: list[dict[str, Any]], coarse: list[dict[str, Any]], tolerance: float,
) -> list[dict[str, Any]]:
    transitions = [(float(item["start"]) + float(item["end"])) / 2 for item in coarse]
    for segment in segments:
        for key in ("start", "end"):
            value = float(segment[key])
            nearest = min(transitions, key=lambda point: abs(point - value), default=value)
            if abs(nearest - value) <= tolerance:
                segment[key] = nearest
    return segments


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
                    field = re.search(
                        rf'"{name}"\s*:\s*(?:"([^"]+)"|([-+]?\d+(?:\.\d+)?))',
                        item, flags=re.IGNORECASE,
                    )
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


def _semantic_response(text: str) -> list[dict[str, Any]]:
    if text.strip().upper() == "NONE":
        return []
    segments = []
    for line in text.strip().splitlines():
        match = re.fullmatch(
            r"\s*(INTRO|AD|OUTRO)\s*,\s*([0-9.]+)s?\s*,\s*([0-9.]+)s?\s*,\s*(0(?:\.\d+)?|1(?:\.0+)?)\s*",
            line, flags=re.IGNORECASE,
        )
        if match:
            label, start, end, confidence = match.groups()
            segments.append({
                "type": label.upper(), "start": float(start), "end": float(end),
                "confidence": float(confidence), "reason": "Qwen visual semantic evidence",
            })
    if segments:
        return segments
    try:
        return _json_object(text)["segments"]
    except ValueError:
        raise ValueError(f"Qwen returned invalid semantic response: {text[:500]!r}") from None


class QwenSemanticDetector:
    def __init__(self, model_reference: str | None = None) -> None:
        bundled = bundled_path("models", "Qwen2.5-VL-7B-Instruct-AWQ")
        self.model_reference = model_reference or os.environ.get("SEMANTIC_QWEN_MODEL") or (
            str(bundled) if bundled else ""
        )
        if not self.model_reference or not Path(self.model_reference).expanduser().exists():
            resource_root = os.environ.get("SILENCE_CUTTER_RESOURCE_DIR")
            data_root = os.environ.get("SILENCE_CUTTER_DATA_DIR")
            manifest = Path(resource_root) / "model_manifest.json" if resource_root else None
            model_root = Path(data_root) / "models" if data_root else None
            if manifest and model_root and manifest.is_file():
                try:
                    record = ensure_qwen_model(manifest, model_root)
                except Exception as exc:
                    raise RuntimeError(f"Qwen model download failed: {exc}") from exc
                if record.status == "verified" and record.path:
                    self.model_reference = str(record.path)
        if not self.model_reference or not Path(self.model_reference).expanduser().exists():
            raise RuntimeError("local Qwen model is not configured")
        loaded = time.perf_counter()
        import torch
        from transformers import AutoConfig, AutoProcessor, Qwen2_5_VLForConditionalGeneration

        if not torch.cuda.is_available():
            raise RuntimeError("Qwen semantic cleaner requires CUDA")
        torch.cuda.reset_peak_memory_stats()
        self.torch = torch
        self.processor = AutoProcessor.from_pretrained(self.model_reference, local_files_only=True)
        config = AutoConfig.from_pretrained(self.model_reference, local_files_only=True)
        config.dtype = config.text_config.dtype = config.vision_config.dtype = torch.float16
        config.quantization_config["backend"] = "torch_awq"
        config.quantization_config["modules_to_not_convert"] = ["model.visual"]
        self.model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            self.model_reference, dtype=torch.float16, local_files_only=True,
            device_map="auto", config=config,
        ).eval().to(dtype=torch.float16)
        self.model_load_time = time.perf_counter() - loaded
        self.generation_count = 0

    def generate_text(
        self, images: list[Image.Image], prompt: str, *, max_new_tokens: int | None = None,
        task: str = "semantic_cleaner", retry: bool = True,
    ) -> str:
        messages = [{"role": "user", "content": [
            *({"type": "image", "image": image} for image in images),
            {"type": "text", "text": prompt},
        ]}]
        text = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        processor_args = {"text": [text], "padding": True, "return_tensors": "pt"}
        if images:
            processor_args["images"] = images
        inputs = self.processor(**processor_args).to(self.model.device)
        self.generation_count += 1
        max_tokens = max_new_tokens or int(os.environ.get("SEMANTIC_MAX_NEW_TOKENS", "32"))
        with self.torch.inference_mode():
            generated = self.model.generate(**inputs, max_new_tokens=max_tokens, do_sample=False)
        return self.processor.batch_decode(
            generated[:, inputs.input_ids.shape[1]:], skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )[0]

    def _classify(self, images: list[Image.Image], prompt: str) -> list[dict[str, Any]]:
        return _semantic_response(self.generate_text(images, prompt))

    def _classify_with_oom_fallback(
        self, images: list[Image.Image], prompt: str,
    ) -> list[dict[str, Any]]:
        try:
            return self._classify(images, prompt)
        except self.torch.OutOfMemoryError:
            self.torch.cuda.empty_cache()
            segments: list[dict[str, Any]] = []
            for image in images:
                segments.extend(self._classify([image], prompt))
            return segments

    def detect(self, source: Path, duration: float) -> dict[str, Any]:
        started = time.perf_counter()
        extraction_time = coarse_time = fine_time = 0.0
        coarse_interval = float(os.environ.get("SEMANTIC_COARSE_INTERVAL", "10"))
        fine_interval = float(os.environ.get("SEMANTIC_FINE_INTERVAL", "4"))
        with tempfile.TemporaryDirectory(prefix="semantic-video-") as directory:
            root = Path(directory)
            extraction_started = time.perf_counter()
            coarse_paths, coarse_timestamps = _extract_sampled_frames(
                source, 0.0, duration, coarse_interval, root / "coarse",
            )
            extraction_time += time.perf_counter() - extraction_started
            coarse_started = time.perf_counter()
            coarse_segments, candidates = _visual_candidates(
                coarse_paths, coarse_timestamps, duration,
            )
            coarse_time = time.perf_counter() - coarse_started
            final_segments: list[dict[str, Any]] = []
            fine_frame_count = 0
            if candidates:
                fine_paths: list[Path] = []
                fine_times: list[float] = []
                extraction_started = time.perf_counter()
                for index, (start, end) in enumerate(candidates):
                    paths, timestamps = _extract_sampled_frames(
                        source, start, end, fine_interval, root / f"fine-{index:03d}",
                    )
                    fine_paths.extend(paths)
                    fine_times.extend(timestamps)
                extraction_time += time.perf_counter() - extraction_started
                fine_frame_count = len(fine_paths)
                fine_sheets = _contact_sheets(fine_paths, fine_times)
                try:
                    fine_started = time.perf_counter()
                    final_segments = self._classify_with_oom_fallback(
                        fine_sheets, FINE_PROMPT.format(duration=duration),
                    )
                    final_segments = _align_to_visual_transitions(
                        final_segments, coarse_segments, coarse_interval,
                    )
                    fine_time = time.perf_counter() - fine_started
                finally:
                    for sheet in fine_sheets:
                        sheet.close()

            if duration > 240.0:
                final_segments = [item for item in final_segments if not (
                    (str(item.get("type", "")).upper() == "AD"
                     and float(item.get("start", 0.0)) >= duration - 120.0)
                    or (str(item.get("type", "")).upper() == "INTRO"
                        and float(item.get("end", duration)) > 90.0)
                    or (str(item.get("type", "")).upper() == "OUTRO"
                        and float(item.get("start", 0.0)) < duration - 120.0)
                )]

        return {
            "model": self.model_reference,
            "runtime": "transformers + GPTQModel AWQ_TORCH (CUDA, local-only)",
            "segments": final_segments,
            "coarse_segments": coarse_segments,
            "candidate_windows": [{"start": start, "end": end} for start, end in candidates],
            "model_load_time": self.model_load_time,
            "frame_extraction_time": extraction_time,
            "coarse_inference_time": coarse_time,
            "fine_inference_time": fine_time,
            "semantic_scan_time": time.perf_counter() - started,
            "coarse_frame_count": len(coarse_paths),
            "fine_frame_count": fine_frame_count,
            "contact_sheet_count": math.ceil(fine_frame_count / 16),
            "candidate_count": len(candidates),
            "generation_count": self.generation_count,
            "peak_vram_bytes": self.torch.cuda.max_memory_allocated(),
            "allocated_vram_bytes": self.torch.cuda.memory_allocated(),
            "reserved_vram_bytes": self.torch.cuda.memory_reserved(),
        }

    def detect_ranges(
        self, source: Path, duration: float, ranges: list[dict[str, float]],
        cache_root: Path | None = None,
        reusable_frames: list[dict[str, Any]] | None = None,
        role: str | None = None,
    ) -> dict[str, Any]:
        """Run one semantic generation over selected ranges, preserving source timestamps."""
        started = time.perf_counter()
        generation_start = self.generation_count
        extraction_time = coarse_time = fine_time = 0.0
        coarse_interval = float(os.environ.get("SEMANTIC_COARSE_INTERVAL", "10"))
        fine_interval = float(os.environ.get("SEMANTIC_FINE_INTERVAL", "4"))
        temporary = None
        if cache_root is None:
            temporary = tempfile.TemporaryDirectory(prefix="semantic-selected-")
            root = Path(temporary.name)
        else:
            root = Path(cache_root)
            root.mkdir(parents=True, exist_ok=True)
        try:
            coarse_segments: list[dict[str, Any]] = []
            candidates: list[tuple[float, float]] = []
            coarse_frame_count = 0
            extraction_started = time.perf_counter()
            reused_frame_count = 0
            for index, scope in enumerate(ranges):
                start, end = float(scope["start"]), float(scope["end"])
                cached = [
                    item for item in (reusable_frames or [])
                    if start <= float(item["timestamp"]) <= end and Path(item["path"]).is_file()
                ]
                if cached:
                    paths = [Path(item["path"]) for item in cached]
                    timestamps = [float(item["timestamp"]) for item in cached]
                    reused_frame_count += len(paths)
                else:
                    paths, timestamps = _extract_sampled_frames(
                        source, start, end, coarse_interval, root / f"coarse-{index:02d}",
                    )
                coarse_frame_count += len(paths)
                local_segments, local_candidates = _visual_candidates(paths, timestamps, duration)
                coarse_segments.extend(local_segments)
                candidates.extend(
                    (max(start, left), min(end, right))
                    for left, right in local_candidates if max(start, left) < min(end, right)
                )
            extraction_time += time.perf_counter() - extraction_started
            coarse_started = time.perf_counter()
            candidates = sorted(set(candidates))
            coarse_time = time.perf_counter() - coarse_started
            fine_paths: list[Path] = []
            fine_times: list[float] = []
            extraction_started = time.perf_counter()
            for index, (start, end) in enumerate(candidates):
                paths, timestamps = _extract_sampled_frames(
                    source, start, end, fine_interval, root / f"fine-{index:03d}",
                )
                fine_paths.extend(paths)
                fine_times.extend(timestamps)
            extraction_time += time.perf_counter() - extraction_started
            final_segments: list[dict[str, Any]] = []
            sheets = _contact_sheets(fine_paths, fine_times) if fine_paths else []
            try:
                if sheets:
                    fine_started = time.perf_counter()
                    prompt = FINE_PROMPT.format(duration=duration)
                    if role:
                        prompt = (
                            f"Inspect only the {role} role for this bounded video part. "
                            "Ignore all other categories.\n" + prompt
                        )
                    final_segments = self._classify_with_oom_fallback(sheets, prompt)
                    final_segments = _align_to_visual_transitions(
                        final_segments, coarse_segments, coarse_interval,
                    )
                    fine_time = time.perf_counter() - fine_started
            finally:
                for sheet in sheets:
                    sheet.close()
            if duration > 240.0:
                final_segments = [item for item in final_segments if not (
                    (str(item.get("type", "")).upper() == "AD"
                     and float(item.get("start", 0.0)) >= duration - 120.0)
                    or (str(item.get("type", "")).upper() == "INTRO"
                        and float(item.get("end", duration)) > 90.0)
                    or (str(item.get("type", "")).upper() == "OUTRO"
                        and float(item.get("start", 0.0)) < duration - 120.0)
                )]
        finally:
            if temporary:
                temporary.cleanup()
        return {
            "model": getattr(self, "model_reference", "Qwen worker"),
            "runtime": "persistent localhost Qwen worker",
            "segments": final_segments,
            "coarse_segments": coarse_segments,
            "candidate_windows": [{"start": a, "end": b} for a, b in candidates],
            "model_load_time": self.model_load_time,
            "frame_extraction_time": extraction_time,
            "coarse_inference_time": coarse_time,
            "fine_inference_time": fine_time,
            "semantic_scan_time": time.perf_counter() - started,
            "coarse_frame_count": coarse_frame_count,
            "fine_frame_count": len(fine_paths),
            "contact_sheet_count": len(sheets),
            "candidate_count": len(candidates),
            "generation_count": self.generation_count - generation_start,
            "peak_vram_bytes": 0,
            "selected_ranges": ranges,
            "reused_frame_count": reused_frame_count,
        }


class QwenWorkerDetector(QwenSemanticDetector):
    """Semantic detector using resident worker instead of loading local weights."""

    def __init__(self, client: Any | None = None) -> None:
        from qwen_worker.client import QwenWorkerClient

        self.client = client or QwenWorkerClient()
        health = self.client.wait_ready(float(os.getenv("QWEN_WORKER_READY_TIMEOUT", "180")))
        self.model_reference = str(health.get("model") or "Qwen worker")
        self.model_load_time = 0.0
        self.generation_count = 0
        self.torch = self.client.torch

    def generate_text(
        self, images: list[Image.Image], prompt: str, *, max_new_tokens: int | None = None,
        task: str = "semantic_cleaner", retry: bool = True,
    ) -> str:
        text = self.client.generate_text(
            images, prompt, max_new_tokens=max_new_tokens, task=task, retry=retry,
        )
        self.generation_count += 1
        self.last_queue_wait = self.client.last_queue_wait
        self.last_generation_time = self.client.last_generation_time
        return text
