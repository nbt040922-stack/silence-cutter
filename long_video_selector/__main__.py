from __future__ import annotations

import argparse
import json
from pathlib import Path

from silence_cutter.audio import probe_media

from .selector import LongVideoSelectorConfig, run_long_video_selector


def main() -> None:
    parser = argparse.ArgumentParser(description="Select the best three ranges in a long video")
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    duration = float(probe_media(args.source)["duration"])
    result = run_long_video_selector(
        args.source, duration, args.output, config=LongVideoSelectorConfig.from_environment(),
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
