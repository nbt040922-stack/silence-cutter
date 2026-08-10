from __future__ import annotations

import argparse
import json
from pathlib import Path

from .planner import plan_done_job


def main() -> None:
    parser = argparse.ArgumentParser(description="Plan TikTok three-part formatting")
    parser.add_argument("job", type=Path, help="DONE job directory or job.json")
    parser.add_argument("--output", type=Path, default=Path("format_plan.json"))
    parser.add_argument("--preview", type=Path, default=Path("part1_preview.png"))
    parser.add_argument("--format-anyway", action="store_true")
    args = parser.parse_args()
    plan = plan_done_job(
        args.job, output_path=args.output, preview_path=args.preview,
        format_anyway=args.format_anyway,
    )
    print(json.dumps({
        "format_plan": str(args.output.resolve()),
        "formatter_status": plan["formatter_status"],
        "preview": plan["preview_path"],
        "parts": plan["parts"],
    }, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
