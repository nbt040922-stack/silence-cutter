from __future__ import annotations

import argparse
from pathlib import Path

from .pipeline import analyze_speech
from .review import export_review_clips


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="High-recall speech detector")
    commands = parser.add_subparsers(dest="command", required=True)
    analyze = commands.add_parser("analyze")
    analyze.add_argument("input", type=Path)
    analyze.add_argument("--output", type=Path, default=Path("high_recall_speech.json"))
    analyze.add_argument("--disagreements", type=Path, default=Path("speech_disagreements.json"))
    analyze.add_argument("--known-gaps", type=Path, default=Path("asr_benchmark/whisper_speech_gaps.json"))
    review = commands.add_parser("review")
    review.add_argument("input", type=Path)
    review.add_argument("--disagreements", type=Path, default=Path("speech_disagreements.json"))
    review.add_argument("--output-dir", type=Path, default=Path("speech_review"))
    review.add_argument("--limit", type=int, default=30)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "review":
        outputs = export_review_clips(
            args.input.resolve(), args.disagreements.resolve(), args.output_dir.resolve(),
            limit=args.limit,
        )
        print(f"Exported {len(outputs)} review clips to {args.output_dir.resolve()}")
        return
    report = analyze_speech(
        args.input, output_path=args.output, disagreement_path=args.disagreements,
        known_gap_path=args.known_gaps,
    )
    metrics = report["metrics"]
    print(f"Silero speech duration: {metrics['silero_speech_duration']:.3f}s")
    print(f"SenseVoice speech duration: {metrics['sensevoice_speech_duration']:.3f}s")
    print(f"Union speech duration: {metrics['union_speech_duration']:.3f}s")
    print("\nFirst 30 disagreement ranges:")
    import json
    disagreements = json.loads(args.disagreements.read_text(encoding="utf-8"))
    for item in disagreements[:30]:
        print(f"{item['start']:.2f}-{item['end']:.2f} {item['detector']} ({item['duration']:.2f}s)")


if __name__ == "__main__":
    main()
