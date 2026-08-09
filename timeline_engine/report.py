from pathlib import Path
from typing import Any

from caption_engine.report import write_caption_report


def write_timeline_report(path: Path, report: dict[str, Any]) -> Path:
    return write_caption_report(path, report)
