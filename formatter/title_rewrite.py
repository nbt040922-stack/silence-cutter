from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any


TITLE_REWRITE_PROMPT = """Rewrite this video title into one concise, clean title in the SAME LANGUAGE. Preserve core topic, named entities, and useful numbers. Remove clickbait clutter, ALL-CAPS emphasis, emoji, hashtags, decorative punctuation, and filler. Do not translate, invent facts, add hooks, descriptions, SEO keywords, or sensational claims. Prefer 4-12 words and at most 80 characters, but keep required proper nouns. Output only valid JSON: {{\"rewritten_title\":\"...\"}}\nORIGINAL TITLE: {title}"""


def safe_filename_title(title: str, *, fallback_id: str | None = None, limit: int = 120) -> str:
    value = "".join(
        character for character in unicodedata.normalize("NFC", str(title))
        if unicodedata.category(character) not in {"Cc", "Cs"}
        and not unicodedata.category(character).startswith("So")
        and not (0x1F000 <= ord(character) <= 0x1FAFF or 0x2600 <= ord(character) <= 0x27BF)
    )
    value = re.sub(r'[<>:"/\\|?*]', "_", value)
    value = re.sub(r"\s+", " ", value).strip().rstrip(".")
    if len(value) > limit:
        shortened = value[:limit].rstrip()
        if " " in shortened and len(shortened.rsplit(" ", 1)[0]) >= limit // 2:
            shortened = shortened.rsplit(" ", 1)[0]
        value = shortened.rstrip(" .")
    if not value:
        identifier = re.sub(r"[^A-Za-z0-9_-]", "", str(fallback_id or ""))
        value = f"video_{identifier}" if identifier else "video"
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}
    if value.split(".", 1)[0].upper() in reserved:
        value = "_" + value
    return value


def _parse_title(text: str) -> str:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    value = json.loads(cleaned)
    title = value.get("rewritten_title") if isinstance(value, dict) else None
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Qwen returned no rewritten_title")
    return title.strip()


def _choose_base(output_dir: Path, title: str, source_id: str | None, part_count: int) -> str:
    base = safe_filename_title(title, fallback_id=source_id)
    if any((output_dir / f"{base}_PART_{index}.mp4").exists()
           for index in range(1, part_count + 1)):
        suffix = safe_filename_title(source_id or "video", limit=32)
        base = f"{safe_filename_title(base, fallback_id=source_id, limit=119 - len(suffix))}_{suffix}"
    return base


def rewrite_title_once(
    job_dir: str | Path, original_title: str, output_dir: str | Path, *,
    source_id: str | None = None, part_count: int = 3, client: Any | None = None,
) -> dict[str, Any]:
    artifact_path = Path(job_dir) / "title_rewrite.json"
    if artifact_path.is_file():
        try:
            cached = json.loads(artifact_path.read_text(encoding="utf-8"))
            if cached.get("filename_base"):
                return cached
        except (OSError, ValueError):
            pass
    started = time.perf_counter()
    queue_wait = generation = 0.0
    attempted = False
    status, rewritten, model, error = "FALLBACK", original_title, None, None
    try:
        if client is None:
            from qwen_worker.client import QwenWorkerClient
            client = QwenWorkerClient(timeout=float(os.getenv("TITLE_REWRITE_TIMEOUT", "10")))
            health = client.wait_ready(float(os.getenv("TITLE_REWRITE_READY_TIMEOUT", "1")))
            model = health.get("model")
        prompt = TITLE_REWRITE_PROMPT.format(title=original_title)
        attempted = True
        text = client.generate_text(
            [], prompt, max_new_tokens=32, task="title_rewrite", retry=False,
        )
        queue_wait = float(getattr(client, "last_queue_wait", 0.0))
        generation = float(getattr(client, "last_generation_time", 0.0))
        rewritten = _parse_title(text)
        status = "APPLIED"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    safe_rewritten = safe_filename_title(rewritten, fallback_id=source_id)
    filename_base = _choose_base(
        Path(output_dir), safe_rewritten, source_id, part_count,
    )
    artifact = {
        "schema_version": 1,
        "original_title": original_title,
        "rewritten_title": rewritten,
        "safe_rewritten_title": safe_rewritten,
        "filename_base": filename_base,
        "status": status,
        "model": model or "Qwen/Qwen2.5-VL-7B-Instruct-AWQ",
        "generation_count": int(attempted),
        "model_load_count": 0,
        "queue_wait_seconds": queue_wait,
        "generation_seconds": generation,
        "total_seconds": time.perf_counter() - started,
        "error": error,
    }
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = artifact_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, artifact_path)
    return artifact


def read_title_rewrite(job_dir: str | Path, original_title: str) -> dict[str, Any]:
    path = Path(job_dir) / "title_rewrite.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("filename_base"):
            return value
    except (OSError, ValueError):
        pass
    safe = safe_filename_title(original_title)
    return {
        "original_title": original_title, "rewritten_title": original_title,
        "filename_base": safe, "status": "SKIPPED", "total_seconds": 0.0,
    }
