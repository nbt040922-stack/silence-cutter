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
    parser.add_argument("--no-batch", action="store_true")
    parser.add_argument("--allow-cpu-fallback", action="store_true")
    return parser


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
    )
    print(json.dumps(generate_captions(args.input, args.output, config=config), indent=2))


if __name__ == "__main__":
    main()
