from __future__ import annotations

import argparse
import json
from pathlib import Path

from .content_boundary import BoundaryConfig
from .pipeline import ProductionRuntime


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="High-recall production silence cutter")
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--analysis-only", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--known-gaps", type=Path)
    parser.add_argument("--content-start", type=float)
    parser.add_argument("--content-end", type=float)
    parser.add_argument("--keep-intro-outro", action="store_true")
    parser.add_argument("--intro-search-window", type=float, default=120.0)
    parser.add_argument("--outro-search-window", type=float, default=60.0)
    parser.add_argument("--post-intro-trim", type=float, default=2.0)
    parser.add_argument("--allowed-ranges-json", type=Path)
    return parser


def main() -> None:
    args = _parser().parse_args()
    allowed_ranges = None
    if args.allowed_ranges_json:
        selection = json.loads(args.allowed_ranges_json.read_text(encoding="utf-8"))
        allowed_ranges = selection.get("selected_ranges")
    result = ProductionRuntime().process(
        args.input,
        args.output,
        analysis_only=args.analysis_only,
        debug=args.debug,
        report_path=args.report,
        known_gap_path=args.known_gaps,
        content_start=args.content_start,
        content_end=args.content_end,
        keep_intro_outro=args.keep_intro_outro,
        boundary_config=BoundaryConfig(
            intro_search_window=args.intro_search_window,
            outro_search_window=args.outro_search_window,
            post_intro_trim=args.post_intro_trim,
        ),
        allowed_ranges=allowed_ranges,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
