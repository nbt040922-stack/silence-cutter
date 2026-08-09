from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import SilenceCutterConfig
from .pipeline import cut_silence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Remove long non-speech gaps from video.")
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--min-silence", type=float, default=1.0)
    parser.add_argument("--pad-before", type=float, default=0.25)
    parser.add_argument("--pad-after", type=float, default=0.30)
    parser.add_argument("--vad-threshold", type=float, default=0.50)
    parser.add_argument("--merge-gap", type=float, default=0.35)
    return parser


def main() -> None:
    args = _parser().parse_args()
    config = SilenceCutterConfig(
        min_silence_duration=args.min_silence,
        speech_pad_before=args.pad_before,
        speech_pad_after=args.pad_after,
        vad_threshold=args.vad_threshold,
        merge_gap=args.merge_gap,
    )
    print(json.dumps(cut_silence(args.input, args.output, config), indent=2))


if __name__ == "__main__":
    main()
