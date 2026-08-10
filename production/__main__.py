from __future__ import annotations

import argparse
import json
from pathlib import Path

from .pipeline import ProductionRuntime


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="High-recall production silence cutter")
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--analysis-only", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--known-gaps", type=Path)
    return parser


def main() -> None:
    args = _parser().parse_args()
    result = ProductionRuntime().process(
        args.input,
        args.output,
        analysis_only=args.analysis_only,
        debug=args.debug,
        report_path=args.report,
        known_gap_path=args.known_gaps,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
