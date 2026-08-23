from __future__ import annotations

from pathlib import Path


def validate_payload(root: Path) -> dict[str, object]:
    forbidden: list[str] = []
    for name in ("desktop", "models"):
        if (root / name).exists():
            forbidden.append(name)
    forbidden_paths = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            text = ""
        if any(token in text for token in ("d:\\silence_cutter", "c:\\users\\nbt04", ".venv")):
            forbidden_paths.append(str(path))
    tools_ok = (root / "tools" / "ffmpeg.exe").is_file() and (root / "tools" / "ffprobe.exe").is_file()
    status = "PASS" if not forbidden and not forbidden_paths and tools_ok else "FAIL"
    return {
        "status": status,
        "forbidden": forbidden,
        "developer_paths": forbidden_paths,
        "tools": "PASS" if tools_ok else "FAIL",
    }
