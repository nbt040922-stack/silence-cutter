from __future__ import annotations

import argparse
from pathlib import Path

from caption_engine.config import CaptionConfig
from silence_cutter.config import SilenceCutterConfig

from .models import TimelineConfig
from .pipeline import run_integrated_pipeline


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cut silence and remap captions to the output timeline."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--model", default="large-v3-turbo")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--compute-type", default="float16")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--language")
    parser.add_argument("--no-batch", action="store_true")
    parser.add_argument("--allow-cpu-fallback", action="store_true")
    parser.add_argument("--min-silence", type=float, default=1.0)
    parser.add_argument("--pad-before", type=float, default=0.25)
    parser.add_argument("--pad-after", type=float, default=0.30)
    parser.add_argument("--vad-threshold", type=float, default=0.50)
    parser.add_argument("--merge-gap", type=float, default=0.35)
    parser.add_argument("--min-surviving-word", type=float, default=0.03)
    return parser


def main() -> None:
    args = _parser().parse_args()
    result = run_integrated_pipeline(
        args.input,
        args.output,
        silence_config=SilenceCutterConfig(
            min_silence_duration=args.min_silence,
            speech_pad_before=args.pad_before,
            speech_pad_after=args.pad_after,
            vad_threshold=args.vad_threshold,
            merge_gap=args.merge_gap,
        ),
        caption_config=CaptionConfig(
            model_size=args.model,
            device=args.device,
            compute_type=args.compute_type,
            language=args.language,
            batch_enabled=not args.no_batch,
            batch_size=args.batch_size,
            allow_cpu_fallback=args.allow_cpu_fallback,
        ),
        timeline_config=TimelineConfig(
            min_surviving_word_duration=args.min_surviving_word
        ),
    )
    for label, key in (
        ("Input duration", "input_duration"),
        ("Output duration", "actual_output_duration"),
        ("Removed duration", "removed_duration"),
        ("Removed percentage", "removed_percentage"),
        ("KEEP count", "keep_count"),
        ("CUT count", "cut_count"),
        ("Words before", "words_before"),
        ("Words after", "words_after"),
        ("Captions before", "captions_before"),
        ("Captions after", "captions_after"),
        ("Boundary words clipped", "boundary_word_clipped_count"),
        ("Timeline mapping error", "timeline_mapping_error"),
        ("Total processing time", "total_processing_time"),
    ):
        print(f"{label}: {result[key]}")


if __name__ == "__main__":
    main()
