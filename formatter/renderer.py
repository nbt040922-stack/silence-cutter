from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from silence_cutter.audio import MediaProcessError, _require_executable, _run
from silence_cutter.renderer import _has_nvenc

from .preview import render_overlay


DEFAULT_AUDIO_PROFILE = {
    "voice_tone_shift": True,
    "pitch_ratio": 1.10,
    "voice_enhance": True,
    "highpass_hz": 90,
    "lowpass_hz": 14000,
    "noise_reduction": 18,
    "compressor": {
        "threshold": 0.12,
        "ratio": 2.2,
        "attack": 20,
        "release": 180,
        "makeup": 1.2,
    },
    "sample_rate": 48000,
    "eq": [
        {"frequency_hz": 250, "gain_db": -1.2, "q": 0.8},
        {"frequency_hz": 3000, "gain_db": 1.0, "q": 0.8},
        {"frequency_hz": 8000, "gain_db": 0.5, "q": 0.8},
    ],
    "limiter": 0.95,
}

CUDA_DECODERS = {
    "av1": "av1_cuvid",
    "h264": "h264_cuvid",
    "hevc": "hevc_cuvid",
}


class FormatterProgress:
    def __init__(
        self, durations: list[float], *, clock: Callable[[], float] = time.perf_counter,
        warmup_seconds: float = 7.0, alpha: float = 0.25, concurrent: bool = False,
    ) -> None:
        self.durations = durations
        self.total_duration = sum(durations)
        self.clock = clock
        self.warmup_seconds = warmup_seconds
        self.alpha = alpha
        self.concurrent = concurrent
        self.started = clock()
        self.part_started = self.started
        self.part_started_at = [self.started] * len(durations)
        self.current_part = 1
        self.smoothed_speed: float | None = None
        self.part_processed = [0.0] * len(durations)

    def start_part(self, part: int) -> dict[str, float | int | None]:
        if not self.concurrent:
            for index in range(part - 1):
                self.part_processed[index] = self.durations[index]
        if part != self.current_part:
            self.current_part = part
            self.part_started = self.clock()
        self.part_started_at[part - 1] = self.clock()
        return self.update(part, 0.0)

    def update(self, part: int, processed_seconds: float) -> dict[str, float | int | None]:
        now = self.clock()
        index = part - 1
        processed = min(self.durations[index], max(0.0, processed_seconds))
        self.part_processed[index] = max(self.part_processed[index], processed)
        total_processed = min(self.total_duration, sum(self.part_processed))
        elapsed = max(0.0, now - self.started)
        current_speed = total_processed / elapsed if elapsed > 0 and total_processed > 0 else None
        if current_speed:
            self.smoothed_speed = (
                current_speed if self.smoothed_speed is None
                else self.alpha * current_speed + (1 - self.alpha) * self.smoothed_speed
            )
        remaining = max(0.0, self.total_duration - total_processed)
        ready = elapsed >= self.warmup_seconds and bool(self.smoothed_speed)
        eta = 0.0 if remaining == 0 else (
            remaining / self.smoothed_speed if ready and self.smoothed_speed else None
        )
        part_remaining = max(0.0, self.durations[index] - self.part_processed[index])
        part_eta = 0.0 if part_remaining == 0 else (
            part_remaining / self.smoothed_speed if ready and self.smoothed_speed else None
        )
        return {
            "formatter_current_part": part,
            "formatter_progress": min(100.0, total_processed / self.total_duration * 100),
            "formatter_part_progress": min(1.0, self.part_processed[index] / self.durations[index]),
            "formatter_elapsed_seconds": elapsed,
            "formatter_eta_seconds": eta,
            "formatter_part_elapsed_seconds": max(0.0, now - self.part_started_at[index]),
            "formatter_part_eta_seconds": part_eta,
            "formatter_render_speed": self.smoothed_speed,
        }


def _progress_seconds(fields: dict[str, str]) -> float | None:
    for key in ("out_time_us", "out_time_ms"):
        try:
            return max(0.0, float(fields[key]) / 1_000_000)
        except (KeyError, ValueError):
            pass
    try:
        hours, minutes, seconds = fields["out_time"].split(":")
        return max(0.0, int(hours) * 3600 + int(minutes) * 60 + float(seconds))
    except (KeyError, ValueError):
        return None


def _run_ffmpeg_progress(
    command: list[str], operation: str, on_progress: Callable[[float], None],
) -> None:
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    fields: dict[str, str] = {}
    output: list[str] = []
    assert process.stdout is not None
    for raw in process.stdout:
        line = raw.strip()
        output.append(line)
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key == "progress":
            processed = _progress_seconds(fields)
            if processed is not None:
                on_progress(processed)
            fields.clear()
        else:
            fields[key] = value
    returncode = process.wait()
    if returncode:
        detail = "\n".join(output[-80:]).strip() or f"exit code {returncode}"
        raise MediaProcessError(f"{operation} failed: {detail}")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def build_render_jobs(plan: dict[str, Any], output_dir: Path) -> list[dict[str, Any]]:
    if plan.get("formatter_status") not in {"PLANNED", "RENDERING", "FAILED"}:
        raise ValueError("formatter plan is not eligible for rendering")
    parts = plan.get("parts") or []
    part_count = int(plan.get("part_count") or len(parts))
    if part_count not in {2, 3} or len(parts) != part_count:
        raise ValueError("formatter plan part_count must match two or three parts")
    jobs = []
    filename_base = str(plan.get("filename_base") or "").strip()
    previous_end = 0.0
    for expected_index, part in enumerate(parts, start=1):
        start, end = float(part["clean_start"]), float(part["clean_end"])
        if int(part["index"]) != expected_index or abs(start - previous_end) > 1e-6 or end <= start:
            raise ValueError("formatter part boundaries must be ordered and contiguous")
        source_segments = (
            map_clean_range_to_source(start, end, plan.get("render_segments") or [])
            if plan.get("direct_source_render") else [{"start": start, "end": end}]
        )
        jobs.append({
            "index": expected_index,
            "label": str(part["label"]),
            "start": start,
            "end": end,
            "duration": end - start,
            "source_segments": source_segments,
            "input_seek": min(float(item["start"]) for item in source_segments),
            "latest_source_timestamp": max(float(item["end"]) for item in source_segments),
            "trim_branch_count": len(source_segments),
            "path": output_dir / (
                f"{filename_base}_PART_{expected_index}.mp4"
                if filename_base else f"PART_{expected_index}.mp4"
            ),
        })
        previous_end = end
    if abs(previous_end - float(plan["clean_video_duration"])) > 1e-3:
        raise ValueError("formatter parts do not cover the clean video")
    return jobs


def map_clean_range_to_source(
    clean_start: float, clean_end: float, mapping: list[dict[str, float]],
) -> list[dict[str, float]]:
    if clean_end <= clean_start:
        raise ValueError("clean range must have positive duration")
    segments = []
    for item in mapping:
        overlap_start = max(clean_start, float(item["output_start"]))
        overlap_end = min(clean_end, float(item["output_end"]))
        if overlap_end <= overlap_start:
            continue
        source_start = float(item["source_start"]) + overlap_start - float(item["output_start"])
        segments.append({
            "start": source_start,
            "end": source_start + overlap_end - overlap_start,
        })
    mapped_duration = sum(item["end"] - item["start"] for item in segments)
    if not segments or abs(mapped_duration - (clean_end - clean_start)) > 1e-4:
        raise ValueError("clean part range is not fully covered by source KEEP mapping")
    return segments


def _command(
    ffmpeg: str, source: Path, overlay: Path, output: Path,
    part: dict[str, Any], layout: dict[str, Any], codec: str,
    audio_profile: dict[str, Any] | None = None,
    cuda_decoder: str | None = None,
) -> list[str]:
    crop = layout["crop_geometry"]
    video = layout["video_placement"]
    source_segments = part.get("source_segments") or [
        {"start": part["start"], "end": part["end"]}
    ]
    profile = audio_profile or DEFAULT_AUDIO_PROFILE
    sample_rate = int(profile["sample_rate"])
    input_seek = min(float(segment["start"]) for segment in source_segments)
    trims = []
    concat_inputs = []
    for index, segment in enumerate(source_segments):
        start = float(segment["start"]) - input_seek
        end = float(segment["end"]) - input_seek
        trims.extend([
            f"[0:v:0]setpts=PTS-STARTPTS,trim=start={start:.9f}:end={end:.9f},setpts=PTS-STARTPTS[v{index}]",
            f"[0:a:0]asetpts=PTS-STARTPTS,atrim=start={start:.9f}:end={end:.9f},asetpts=PTS-STARTPTS[a{index}]",
        ])
        concat_inputs.append(f"[v{index}][a{index}]")
    clean = (
        f"{''.join(concat_inputs)}concat=n={len(source_segments)}:v=1:a=1[cleanv][cleana]"
    )
    audio = f"[cleana]aresample={sample_rate}"
    if profile.get("voice_tone_shift"):
        ratio = float(profile["pitch_ratio"])
        audio += (
            f",asetrate={sample_rate}*{ratio:.9f},aresample={sample_rate},"
            f"atempo={1 / ratio:.9f}"
        )
        if profile.get("voice_enhance"):
            compressor = profile.get("compressor") or {}
            audio += (
                f",highpass=f={float(profile.get('highpass_hz', 90)):.9g}"
                f",afftdn=nr={float(profile.get('noise_reduction', 18)):.9g}:nf=-25:tn=1"
                f",lowpass=f={float(profile.get('lowpass_hz', 14000)):.9g}"
            )
        for setting in profile["eq"]:
            audio += (
                f",equalizer=f={setting['frequency_hz']}:t=q:w={setting['q']}:"
                f"g={setting['gain_db']}"
            )
        if profile.get("voice_enhance"):
            compressor = profile.get("compressor") or {}
            audio += (
                f",acompressor=threshold={float(compressor.get('threshold', 0.12)):.9g}"
                f":ratio={float(compressor.get('ratio', 2.2)):.9g}"
                f":attack={float(compressor.get('attack', 20)):.9g}"
                f":release={float(compressor.get('release', 180)):.9g}"
                f":makeup={float(compressor.get('makeup', 1.2)):.9g}"
            )
        audio += f",alimiter=limit={profile['limiter']}:attack=5:release=50:level=false"
    audio += f",aresample={sample_rate}[aout]"
    if cuda_decoder:
        video_graph = (
            f"[cleanv]scale_cuda={video['width']}:{video['height']}:"
            "interp_algo=lanczos:format=yuv420p:reset_sar=1,"
            f"pad_cuda={layout['canvas']['width']}:{layout['canvas']['height']}:"
            f"{video['x']}:{video['y']}:color=black,setsar=1[base];"
            "[1:v:0]format=yuva420p,hwupload_cuda[overlay];"
            "[base][overlay]overlay_cuda=0:0:eof_action=repeat:shortest=1[vout]"
        )
    else:
        video_graph = (
            f"[cleanv]crop={crop['width']}:{crop['height']}:{crop['x']}:{crop['y']},"
            f"scale={video['width']}:{video['height']}:flags=lanczos,"
            f"pad={layout['canvas']['width']}:{layout['canvas']['height']}:"
            f"{video['x']}:{video['y']}:color=black[base];"
            "[1:v:0]format=rgba[overlay];"
            "[base][overlay]overlay=0:0:eof_action=repeat:shortest=1,"
            "format=yuv420p[vout]"
        )
    graph = ";".join(trims) + ";" + clean + ";" + video_graph + ";" + audio
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-progress", "pipe:1", "-nostats",
    ]
    if cuda_decoder:
        crop_bottom = int(crop["source_height"]) - int(crop["y"]) - int(crop["height"])
        crop_right = int(crop["source_width"]) - int(crop["x"]) - int(crop["width"])
        command += [
            "-hwaccel", "cuda", "-hwaccel_output_format", "cuda",
            "-c:v", cuda_decoder, "-crop",
            f"{int(crop['y'])}x{crop_bottom}x{int(crop['x'])}x{crop_right}",
        ]
    command += [
        "-ss", f"{input_seek:.9f}", "-i", str(source),
        "-loop", "1", "-i", str(overlay),
        "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]",
        "-c:v", codec,
    ]
    if codec == "h264_nvenc":
        command += ["-preset", "p4", "-cq", "23"]
    else:
        command += ["-preset", "medium", "-crf", "23"]
    command += [
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        "-t", f"{part['duration']:.9f}",
    ]
    if cuda_decoder:
        alignment_crop = (-int(layout["canvas"]["width"])) % 16
        if alignment_crop:
            command += ["-bsf:v", f"h264_metadata=crop_right={alignment_crop}"]
    command.append(str(output))
    return command


def _select_cuda_decoder(ffmpeg: str, source_codec: str | None) -> str | None:
    decoder = CUDA_DECODERS.get(str(source_codec or "").lower())
    if not decoder:
        return None
    try:
        decoders = _run([ffmpeg, "-hide_banner", "-decoders"], "FFmpeg decoder probe").stdout
        filters = _run([ffmpeg, "-hide_banner", "-filters"], "FFmpeg filter probe").stdout
    except MediaProcessError:
        return None
    required_filters = ("scale_cuda", "pad_cuda", "overlay_cuda", "hwupload_cuda")
    return decoder if decoder in decoders and all(name in filters for name in required_filters) else None


def _probe(path: Path) -> dict[str, Any]:
    ffprobe = _require_executable("ffprobe")
    result = _run([
        ffprobe, "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,codec_name,width,height,duration,sample_rate,"
        "sample_aspect_ratio,display_aspect_ratio",
        "-of", "json", str(path),
    ], "formatted output probe")
    data = json.loads(result.stdout)
    streams = data.get("streams") or []
    video = next(item for item in streams if item.get("codec_type") == "video")
    audio = next(item for item in streams if item.get("codec_type") == "audio")
    return {
        "duration": float(data["format"]["duration"]),
        "width": int(video["width"]), "height": int(video["height"]),
        "video_codec": video.get("codec_name"), "audio_codec": audio.get("codec_name"),
        "audio_sample_rate": int(audio["sample_rate"]),
        "sample_aspect_ratio": video.get("sample_aspect_ratio"),
        "display_aspect_ratio": video.get("display_aspect_ratio"),
        "video_duration": float(video.get("duration") or data["format"]["duration"]),
        "audio_duration": float(audio.get("duration") or data["format"]["duration"]),
    }


def _update_source_job(plan: dict[str, Any], **values: Any) -> None:
    path = Path(str(plan.get("source_job_path") or ""))
    if not path.is_file():
        return
    job = json.loads(path.read_text(encoding="utf-8"))
    job.update(values)
    _write_json(path, job)


def _update_pipeline_report(source_job: dict[str, Any], **values: Any) -> None:
    path = Path(str(source_job.get("report_path") or ""))
    if not path.is_file():
        return
    report = json.loads(path.read_text(encoding="utf-8"))
    report.update(values)
    _write_json(path, report)


def _persist_progress(
    path: Path, plan: dict[str, Any], snapshot: dict[str, float | int | None],
) -> None:
    plan.update(snapshot)
    _write_json(path, plan)
    _update_source_job(plan, **snapshot)


def render_format_plan(plan_path: str | Path) -> dict[str, Any]:
    path = Path(plan_path).expanduser().resolve()
    plan = json.loads(path.read_text(encoding="utf-8"))
    direct_source_render = bool(plan.get("direct_source_render"))
    source_value = (
        plan.get("source_video_path") if direct_source_render
        else plan.get("clean_video_path")
    )
    source = Path(str(source_value or ""))
    if not source.is_file():
        raise FileNotFoundError("formatter render source is missing")
    source_job = Path(str(plan.get("source_job_path") or ""))
    source_job_data = (
        json.loads(source_job.read_text(encoding="utf-8")) if source_job.is_file() else {}
    )
    output_dir = Path(source_job_data.get("output_folder") or path.parent / "formatted")
    output_dir.mkdir(parents=True, exist_ok=True)
    jobs = build_render_jobs(plan, output_dir)
    part_count = len(jobs)
    audio_profile = {**DEFAULT_AUDIO_PROFILE, **(plan.get("audio_profile") or {})}
    render_concurrency = max(1, min(
        part_count,
        int(plan.get("render_concurrency") or os.getenv("FORMATTER_RENDER_CONCURRENCY", "1")),
    ))
    tracker = FormatterProgress(
        [part["duration"] for part in jobs], concurrent=render_concurrency > 1,
    )
    started_at = datetime.now(timezone.utc).isoformat()
    initial_progress = tracker.update(1, 0.0)
    plan.update(
        formatter_status="RENDERING", formatted_outputs=[], formatter_error=None,
        formatter_part_count=part_count,
        formatter_started_at=started_at, audio_profile=audio_profile,
        intermediate_render_skipped=direct_source_render,
        audio_effect_enabled=bool(audio_profile["voice_tone_shift"]),
        pitch_ratio=float(audio_profile["pitch_ratio"]),
        voice_enhance_enabled=bool(audio_profile.get("voice_enhance")),
        audio_filter_profile={
            "highpass_hz": audio_profile.get("highpass_hz"),
            "lowpass_hz": audio_profile.get("lowpass_hz"),
            "noise_reduction": audio_profile.get("noise_reduction"),
            "compressor": audio_profile.get("compressor"),
        },
        eq_settings=audio_profile["eq"],
        final_audio_sample_rate=int(audio_profile["sample_rate"]),
        final_audio_codec="aac", final_audio_bitrate="192k",
        formatter_render_concurrency=render_concurrency,
        **initial_progress,
    )
    _write_json(path, plan)
    _update_source_job(
        plan, formatter_status="RENDERING", formatted_outputs=[], formatter_error=None,
        formatter_part_count=part_count,
        formatter_started_at=started_at, audio_profile=audio_profile,
        intermediate_render_skipped=direct_source_render,
                        audio_effect_enabled=bool(audio_profile["voice_tone_shift"]),
                        pitch_ratio=float(audio_profile["pitch_ratio"]),
                        voice_enhance_enabled=bool(audio_profile.get("voice_enhance")),
                        audio_filter_profile={
                            "highpass_hz": audio_profile.get("highpass_hz"),
                            "lowpass_hz": audio_profile.get("lowpass_hz"),
                            "noise_reduction": audio_profile.get("noise_reduction"),
                            "compressor": audio_profile.get("compressor"),
                        },
                        eq_settings=audio_profile["eq"],
        final_audio_sample_rate=int(audio_profile["sample_rate"]),
        final_audio_codec="aac", final_audio_bitrate="192k",
        formatter_render_concurrency=render_concurrency,
        **initial_progress,
    )
    ffmpeg = _require_executable("ffmpeg")
    codec = "h264_nvenc"
    outputs: list[dict[str, Any]] = []
    total_start = time.perf_counter()
    last_progress_write = total_start
    try:
        if not _has_nvenc(ffmpeg):
            raise MediaProcessError("formatter requires working h264_nvenc")
        source_codec = _probe(source)["video_codec"]
        cuda_decoder = _select_cuda_decoder(ffmpeg, source_codec)
        cuda_fallback_error: str | None = None
        plan.update(
            source_codec=source_codec,
            decoder=cuda_decoder or f"{source_codec}_software",
            video_filter_device="CUDA" if cuda_decoder else "CPU",
            encoder=codec,
        )
        _write_json(path, plan)
        _update_source_job(
            plan,
            source_codec=source_codec,
            decoder=plan["decoder"],
            video_filter_device=plan["video_filter_device"],
            encoder=codec,
        )
        with tempfile.TemporaryDirectory(prefix="formatter-overlays-", dir=path.parent) as temporary:
            overlay_dir = Path(temporary)
            overlays = {
                part["index"]: render_overlay(
                    plan, overlay_dir / f"part_{part['index']}.png", part["label"],
                ) for part in jobs
            }
            progress_lock = threading.Lock()

            def render_part(part: dict[str, Any]) -> dict[str, Any]:
                nonlocal last_progress_write, cuda_decoder
                with progress_lock:
                    snapshot = tracker.start_part(part["index"])
                    _persist_progress(path, plan, snapshot)

                def report_progress(processed: float) -> None:
                    nonlocal last_progress_write
                    with progress_lock:
                        progress = tracker.update(part["index"], processed)
                        now = time.perf_counter()
                        if now - last_progress_write >= 1.5:
                            _persist_progress(path, plan, progress)
                            last_progress_write = now

                overlay = overlays[part["index"]]
                temporary_output = output_dir / f".PART_{part['index']}-{uuid.uuid4().hex}.mp4"
                started = time.perf_counter()
                part_decoder = cuda_decoder
                part_fallback_error: str | None = None
                try:
                    command = _command(
                        ffmpeg, source, overlay, temporary_output, part,
                        plan["layout"], codec, audio_profile, part_decoder,
                    )
                    try:
                        _run_ffmpeg_progress(
                            command, f"formatter part {part['index']} render with {codec}",
                            report_progress,
                        )
                    except MediaProcessError as exc:
                        if not part_decoder:
                            raise
                        part_fallback_error = str(exc)
                        part_decoder = None
                        with progress_lock:
                            cuda_decoder = None
                        command = _command(
                            ffmpeg, source, overlay, temporary_output, part,
                            plan["layout"], codec, audio_profile, None,
                        )
                        _run_ffmpeg_progress(
                            command, f"formatter part {part['index']} CPU filter fallback with NVENC",
                            report_progress,
                        )
                    os.replace(temporary_output, part["path"])
                finally:
                    temporary_output.unlink(missing_ok=True)
                media = _probe(part["path"])
                part_render_time = time.perf_counter() - started
                return {
                    "index": part["index"], "label": part["label"],
                    "path": str(part["path"].resolve()),
                    "planned_start": part["start"], "planned_end": part["end"],
                    "planned_duration": part["duration"],
                    "input_seek": part["input_seek"],
                    "latest_source_timestamp": part["latest_source_timestamp"],
                    "trim_branch_count": part["trim_branch_count"],
                    **media,
                    "duration_error": abs(media["duration"] - part["duration"]),
                    "av_delta": abs(media["video_duration"] - media["audio_duration"]),
                    "render_time": part_render_time,
                    "render_speed": part["duration"] / part_render_time,
                    "codec_requested": "h264_nvenc", "codec_used": codec,
                    "source_codec": source_codec,
                    "decoder": part_decoder or f"{source_codec}_software",
                    "video_filter_path": "nvdec_cuda" if part_decoder else "cpu_nvenc",
                    "video_filter_device": "CUDA" if part_decoder else "CPU",
                    "encoder": codec,
                    "cuda_filter_fallback_error": part_fallback_error,
                }

            with ThreadPoolExecutor(max_workers=render_concurrency) as pool:
                futures = {pool.submit(render_part, part): part for part in jobs}
                for future in as_completed(futures):
                    item = future.result()
                    outputs.append(item)
                    outputs.sort(key=lambda value: value["index"])
                    if item["cuda_filter_fallback_error"]:
                        cuda_fallback_error = item["cuda_filter_fallback_error"]
                    with progress_lock:
                        _persist_progress(
                            path, plan, tracker.update(item["index"], item["planned_duration"])
                        )
                        plan["formatted_outputs"] = outputs
                        _write_json(path, plan)
        total_render_time = time.perf_counter() - total_start
        part_render_times = [{
            "index": item["index"], "duration": item["planned_duration"],
            "render_time": item["render_time"],
            "speed": item["planned_duration"] / item["render_time"],
        } for item in outputs]
        final_progress = tracker.update(part_count, jobs[-1]["duration"])
        final_progress.update(formatter_progress=100.0, formatter_eta_seconds=0.0)
        validation = {
            "max_duration_error": max(item["duration_error"] for item in outputs),
            "max_av_delta": max(item["av_delta"] for item in outputs),
        }
        validation["passed"] = (
            validation["max_duration_error"] <= 0.15
            and validation["max_av_delta"] <= 0.15
        )
        estimated_time_saved = (
            tracker.total_duration / (tracker.total_duration / total_render_time)
            if direct_source_render else 0.0
        )
        input_duration = float(plan.get("input_duration") or tracker.total_duration)
        estimated_disk_saved = (
            int(source.stat().st_size * tracker.total_duration / input_duration)
            if direct_source_render and input_duration > 0 else 0
        )
        decoders_used = {item["decoder"] for item in outputs}
        filter_devices_used = {item["video_filter_device"] for item in outputs}
        decoder_used = next(iter(decoders_used)) if len(decoders_used) == 1 else "mixed"
        filter_device_used = (
            next(iter(filter_devices_used)) if len(filter_devices_used) == 1 else "MIXED"
        )
        plan.update(
            formatter_status="DONE", formatted_outputs=outputs,
            formatter_render_time=total_render_time,
            total_format_render_time=total_render_time,
            part_render_times=part_render_times,
            average_render_speed=tracker.total_duration / total_render_time,
            source_codec=source_codec,
            decoder=decoder_used,
            video_filter_device=filter_device_used,
            encoder=codec,
            formatter_video_filter_path=(
                "nvdec_cuda" if all(item["video_filter_path"] == "nvdec_cuda" for item in outputs)
                else "cpu_nvenc"
            ),
            formatter_cuda_filter_fallback_error=cuda_fallback_error,
            formatter_render_concurrency=render_concurrency,
            audio_sync_duration_validation=validation,
            intermediate_render_skipped=direct_source_render,
            old_estimated_pipeline_seconds=total_render_time + estimated_time_saved,
            new_pipeline_seconds=total_render_time,
            estimated_time_saved=estimated_time_saved,
            estimated_disk_saved=estimated_disk_saved,
            **final_progress,
        )
        _write_json(path, plan)
        primary_output = source_job_data.get("output_path") or outputs[0]["path"]
        _update_source_job(
            plan, formatter_status="DONE", formatted_outputs=outputs,
            format_plan=str(path), formatter_error=None, output_path=primary_output,
            formatter_render_time=total_render_time,
            total_format_render_time=total_render_time,
            part_render_times=part_render_times,
            average_render_speed=tracker.total_duration / total_render_time,
            source_codec=source_codec,
            decoder=decoder_used,
            video_filter_device=filter_device_used,
            encoder=codec,
            formatter_video_filter_path=(
                "nvdec_cuda" if all(item["video_filter_path"] == "nvdec_cuda" for item in outputs)
                else "cpu_nvenc"
            ),
            formatter_cuda_filter_fallback_error=cuda_fallback_error,
            formatter_render_concurrency=render_concurrency,
            audio_sync_duration_validation=validation,
            intermediate_render_skipped=direct_source_render,
            old_estimated_pipeline_seconds=total_render_time + estimated_time_saved,
            new_pipeline_seconds=total_render_time,
            estimated_time_saved=estimated_time_saved,
            estimated_disk_saved=estimated_disk_saved,
            clean_master_required=not direct_source_render,
            clean_master_rendered=not direct_source_render,
            **final_progress,
        )
        _update_pipeline_report(
            source_job_data,
            intermediate_render_skipped=direct_source_render,
            old_estimated_pipeline_seconds=total_render_time + estimated_time_saved,
            new_pipeline_seconds=total_render_time,
            estimated_time_saved=estimated_time_saved,
            estimated_disk_saved=estimated_disk_saved,
            source_codec=source_codec,
            decoder=decoder_used,
            video_filter_device=filter_device_used,
            encoder=codec,
            clean_master_required=not direct_source_render,
            clean_master_rendered=not direct_source_render,
        )
        return plan
    except Exception as exc:
        failed_index = len(outputs) + 1
        plan.update(
            formatter_status="FAILED", formatted_outputs=outputs,
            formatter_failed_part=failed_index, formatter_error=str(exc),
            formatter_render_time=time.perf_counter() - total_start,
        )
        _write_json(path, plan)
        _update_source_job(
            plan, formatter_status="FAILED", formatted_outputs=outputs,
            format_plan=str(path), formatter_failed_part=failed_index,
            formatter_error=str(exc),
        )
        return plan
