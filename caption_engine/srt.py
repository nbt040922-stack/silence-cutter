from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .models import CaptionSegment


def milliseconds(seconds: float) -> int:
    return max(0, int(round(seconds * 1000)))


def format_timestamp(seconds: float) -> str:
    total = milliseconds(seconds)
    hours, remainder = divmod(total, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def captions_to_srt(captions: list[CaptionSegment]) -> str:
    blocks: list[str] = []
    previous_end = 0.0
    for index, caption in enumerate(captions, start=1):
        if caption.start < previous_end:
            raise ValueError("SRT captions must not overlap")
        if caption.end < caption.start:
            raise ValueError("SRT caption end must not precede start")
        blocks.append(
            f"{index}\n{format_timestamp(caption.start)} --> "
            f"{format_timestamp(caption.end)}\n{caption.text}"
        )
        previous_end = caption.end
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def write_srt(path: Path, captions: list[CaptionSegment]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        prefix=f".{path.stem}-",
        suffix=path.suffix,
        dir=path.parent,
        delete=False,
    ) as temporary:
        temporary.write(captions_to_srt(captions))
        temporary_path = Path(temporary.name)
    try:
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return path
