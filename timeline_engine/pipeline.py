from __future__ import annotations

import json
import math
import tempfile
import time
from pathlib import Path
from typing import Any

from caption_engine.config import CaptionConfig
from caption_engine.models import CaptionSegment, WordTimestamp
from caption_engine.pipeline import generate_captions
from caption_engine.report import write_caption_report
from caption_engine.srt import write_srt
from silence_cutter.config import SilenceCutterConfig
from silence_cutter.pipeline import cut_silence

from .captions import remap_captions
from .mapper import build_timeline_segments
from .models import TimelineConfig
from .report import write_timeline_report


def _load_source_captions(path: Path) -> list[CaptionSegment]:
    report = json.loads(path.read_text(encoding="utf-8"))
    captions: list[CaptionSegment] = []
    for raw in report.get("captions", []):
        words = [WordTimestamp(**word) for word in raw.get("words", [])]
        captions.append(
            CaptionSegment(
                start=raw["start"],
                end=raw["end"],
                text=raw["text"],
                words=words,
            )
        )
    return captions


def _output_paths(source: Path, output_video: str | Path | None) -> dict[str, Path]:
    video = (
        Path(output_video).expanduser().resolve()
        if output_video is not None
        else source.with_name(f"{source.stem}.cut{source.suffix}")
    )
    paths = {
        "video": video,
        "srt": video.with_suffix(".srt"),
        "captions": video.with_suffix(".captions.json"),
        "timeline": video.with_suffix(".timeline.json"),
    }
    if source in paths.values() or len(set(paths.values())) != len(paths):
        raise ValueError("Phase 3 output paths must differ from input and each other")
    return paths


def run_integrated_pipeline(
    input_video: str | Path,
    output_video: str | Path | None = None,
    *,
    silence_config: SilenceCutterConfig | None = None,
    caption_config: CaptionConfig | None = None,
    timeline_config: TimelineConfig | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    source = Path(input_video).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"input video does not exist: {source}")
    silence_config = silence_config or SilenceCutterConfig()
    caption_config = caption_config or CaptionConfig()
    timeline_config = timeline_config or TimelineConfig()
    paths = _output_paths(source, output_video)

    with tempfile.TemporaryDirectory(prefix="timeline-engine-") as directory:
        temporary = Path(directory)
        caption_result = generate_captions(
            source,
            temporary / "source.srt",
            config=caption_config,
            report_path=temporary / "source.captions.json",
        )
        source_captions = _load_source_captions(Path(caption_result["report_path"]))
        cut_result = cut_silence(source, paths["video"], silence_config)

    mapping_started = time.perf_counter()
    timeline = build_timeline_segments(
        cut_result["keep"], epsilon=timeline_config.epsilon
    )
    mapped = remap_captions(
        source_captions,
        timeline,
        timeline_config=timeline_config,
        caption_config=caption_config,
    )
    timeline_mapping_time = time.perf_counter() - mapping_started

    write_srt(paths["srt"], list(mapped.captions))
    word_mapping = mapped.word_mapping
    captions_report = {
        "timeline": "cut",
        "words_before": word_mapping.words_before,
        "words_after": word_mapping.words_after,
        "captions_before": len(source_captions),
        "captions_after": len(mapped.captions),
        "boundary_word_clipped_count": word_mapping.boundary_word_clipped_count,
        "boundary_word_multi_keep_count": word_mapping.boundary_word_multi_keep_count,
        "captions": [caption.to_dict() for caption in mapped.captions],
    }
    write_caption_report(paths["captions"], captions_report)

    expected_output_duration = timeline[-1].output_end if timeline else 0.0
    timeline_keep_duration = math.fsum(
        segment["end"] - segment["start"] for segment in cut_result["keep"]
    )
    timeline_mapping_error = abs(expected_output_duration - timeline_keep_duration)
    actual_output_duration = float(cut_result["actual_output_duration"])
    duration_error = abs(actual_output_duration - expected_output_duration)
    if duration_error > timeline_config.render_duration_tolerance:
        raise ValueError(
            "rendered output duration differs from timeline beyond tolerance"
        )
    if mapped.captions and (
        mapped.captions[-1].end
        > actual_output_duration + timeline_config.render_duration_tolerance
    ):
        raise ValueError("subtitle timestamp exceeds rendered video duration")

    total_processing_time = time.perf_counter() - started
    input_duration = float(cut_result["input_duration"])
    removed_duration = float(cut_result["removed_duration"])
    report = {
        "input_duration": input_duration,
        "expected_output_duration": expected_output_duration,
        "actual_output_duration": actual_output_duration,
        "removed_duration": removed_duration,
        "removed_percentage": removed_duration / input_duration * 100,
        "keep_count": len(cut_result["keep"]),
        "cut_count": len(cut_result["cut"]),
        "words_before": word_mapping.words_before,
        "words_after": word_mapping.words_after,
        "captions_before": len(source_captions),
        "captions_after": len(mapped.captions),
        "boundary_word_clipped_count": word_mapping.boundary_word_clipped_count,
        "boundary_word_multi_keep_count": word_mapping.boundary_word_multi_keep_count,
        "timeline_mapping_time": timeline_mapping_time,
        "timeline_mapping_error": timeline_mapping_error,
        "duration_error": duration_error,
        "caption_processing_time": caption_result["total_processing_time"],
        "silence_cut_processing_time": cut_result["processing_time"],
        "total_processing_time": total_processing_time,
        "timeline_segments": [segment.to_dict() for segment in timeline],
        "keep_segments": cut_result["keep"],
        "cut_segments": cut_result["cut"],
    }
    write_timeline_report(paths["timeline"], report)
    return {
        "output_video": str(paths["video"]),
        "output_srt": str(paths["srt"]),
        "captions_report": str(paths["captions"]),
        "timeline_report": str(paths["timeline"]),
        **report,
    }
