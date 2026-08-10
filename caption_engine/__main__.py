from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import CaptionConfig
from .pipeline import generate_captions


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate word-timed captions.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--model", default="large-v3-turbo")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--compute-type", default="float16")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--language")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--no-batch", action="store_true")
    parser.add_argument("--allow-cpu-fallback", action="store_true")
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--no-adaptive", action="store_true")
    return parser


def _benchmark_summary(result: dict[str, object], model: str) -> str:
    actual_device = str(result.get("actual_device") or "unknown").upper()
    actual_compute = str(result.get("actual_compute_type") or "unknown")
    lines = [
        f"Model: {model}",
        f"Language: {result.get('language') or 'unknown'}",
        f"Backend: {actual_device} / {actual_compute}",
        f"Batch size: {result['batch_size']}",
        f"Model cache: {'hit' if result['model_initialization_cached'] else 'miss'}",
    ]
    cuda_runtime = result.get("cuda_runtime")
    if isinstance(cuda_runtime, dict) and cuda_runtime.get("applicable"):
        lines.extend([
            f"CUDA runtime: {'OK' if cuda_runtime.get('available') else 'FAILED'}",
            f"cuBLAS 12: {'found' if cuda_runtime.get('cublas_found') else 'missing'}",
            f"cuDNN 9: {'found' if cuda_runtime.get('cudnn_found') else 'missing'}",
        ])
    lines.extend([
        "",
        f"Audio: {result['audio_duration']:.1f} s",
        f"Audio extraction: {result['audio_extraction_time']:.1f} s",
        f"Model load: {result['model_initialization_time']:.1f} s",
        f"Inference: {result['transcription_inference_time']:.1f} s",
        f"Caption processing: {result['caption_processing_time']:.1f} s",
        f"Output write: {result['output_write_time']:.1f} s",
        f"Total: {result['total_processing_time']:.1f} s",
        "",
        f"Realtime factor: {result['realtime_factor']:.3f}",
        f"Speed: {result['x_realtime']:.2f}x realtime",
        f"Words/tokens: {result['word_count']}",
        f"Captions: {result['caption_count']}",
    ])
    diagnostics = result.get("segmentation_diagnostics")
    if isinstance(diagnostics, dict):
        lines.extend([
            "",
            f"Average speech rate: {diagnostics['average_speech_rate']:.2f} chars/s",
            f"Average word rate: {diagnostics['average_words_per_second']:.2f} words/s",
            f"Median caption: {diagnostics['median_caption_duration']:.2f} s",
            f"Caption duration p10/p50/p90: {diagnostics['p10_caption_duration']:.2f} / "
            f"{diagnostics['p50_caption_duration']:.2f} / "
            f"{diagnostics['p90_caption_duration']:.2f} s",
            f"Average chars/caption: {diagnostics['average_visible_characters_per_caption']:.2f}",
            f"Reading load avg/max: {diagnostics['average_reading_load']:.2f} / "
            f"{diagnostics['maximum_reading_load']:.2f} chars/s",
        ])
    return "\n".join(lines)


def main() -> None:
    args = _parser().parse_args()
    config = CaptionConfig(
        model_size=args.model,
        device=args.device,
        compute_type=args.compute_type,
        language=args.language,
        batch_enabled=not args.no_batch,
        batch_size=args.batch_size,
        allow_cpu_fallback=args.allow_cpu_fallback,
        adaptive_segmentation=not args.no_adaptive,
    )
    result = generate_captions(
        args.input, args.output, config=config, report_path=args.report
    )
    if args.benchmark:
        print(_benchmark_summary(result, args.model))
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
