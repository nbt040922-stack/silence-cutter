from __future__ import annotations

import argparse
import json
from pathlib import Path

from .cleaner import apply_semantic_cleaner


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Qwen semantic timeline cleaner")
    parser.add_argument("source", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(
        apply_semantic_cleaner(args.source, args.report, args.output),
        ensure_ascii=False,
    ))


if __name__ == "__main__":
    main()
