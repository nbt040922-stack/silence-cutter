from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any


TITLE_REWRITE_PROMPT = """Rewrite this video title into one high-engagement, natural, truthful title in the SAME LANGUAGE. Do not summarize it into a plain topic. First identify its dominant pattern internally (list, question, how-to, warning, personal story, contrarian claim, money, comparison, or reveal), then preserve or improve its strongest reason to click: curiosity, stakes, benefit, surprise, contrast, consequence, open loop, specificity, or personal transformation. Preserve named entities and useful numbers. Refine clickbait, but never invent facts, numbers, results, danger, controversy, urgency, people, or conclusions. Avoid spammy ALL CAPS, emoji, hashtags, markdown, SEO lists, SHOCKING, YOU WON'T BELIEVE, MUST WATCH, and excessive punctuation. Prefer 6-14 words and about 90 characters maximum. Output only valid JSON: {{\"rewritten_title\":\"...\"}}.

Style calibration:
- 50 *NEW* Dollar Tree Deals you NEED to buy! -> 50 Dollar Tree Finds Actually Worth Buying
- I Live Alone in Retirement and It's a Game Changer! -> Why Living Alone in Retirement Changed Everything
- Why Leasing a Car in Retirement ACTUALLY Works -> Why Leasing a Car in Retirement Might Actually Make Sense
- THIS is How Much it Costs to Build a House in Colombia -> What It Really Costs to Build a House in Colombia

ORIGINAL TITLE: {title}"""

_HOOK_WORDS = {
    "actually", "avoid", "before", "bigger", "changed", "changes", "consequence",
    "don't", "expected", "facing", "happens", "how", "left", "make", "miss",
    "need", "reason", "really", "serious", "surprising", "truth", "unexpected",
    "warning", "what", "why", "works", "worth",
}
_VIETNAMESE = set("ăâđêôơưĂÂĐÊÔƠƯ") | set(
    "àáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ"
    "ÀÁẢÃẠẰẮẲẴẶẦẤẨẪẬÈÉẺẼẸỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌỒỐỔỖỘỜỚỞỠỢÙÚỦŨỤỪỨỬỮỰỲÝỶỸỴ"
)


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
    cleaned = text.strip()
    if "```" in cleaned or re.search(r"(?m)^\s*(?:#|[-*]\s)", cleaned):
        raise ValueError("markdown output")
    value = json.loads(cleaned)
    title = value.get("rewritten_title") if isinstance(value, dict) else None
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Qwen returned no rewritten_title")
    return title.strip()


def _numbers(text: str) -> set[str]:
    return {
        re.sub(r"[^0-9.%kKmM]", "", value).lower().replace(",", "")
        for value in re.findall(r"(?<!\w)(?:[$€£¥]\s*)?\d[\d,.]*(?:%|[kKmM])?", text)
    }


def _has_hook(text: str) -> bool:
    words = {word.lower() for word in re.findall(r"[^\W_]+(?:'[^\W_]+)?", text)}
    return "?" in text or bool(words & _HOOK_WORDS)


def _language_guard(original: str, rewritten: str) -> str | None:
    scripts = (
        (r"[\u3040-\u30ff]", "Japanese"),
        (r"[\u4e00-\u9fff]", "CJK"),
        (r"[\uac00-\ud7af]", "Korean"),
        (r"[\u0400-\u04ff]", "Cyrillic"),
    )
    for pattern, name in scripts:
        if re.search(pattern, original) and not re.search(pattern, rewritten):
            return f"title changes {name} source language"
    if any(character in _VIETNAMESE for character in original) and not any(
        character in _VIETNAMESE for character in rewritten
    ):
        return "title changes Vietnamese source language"
    return None


def _guard_title(original: str, rewritten: str) -> str | None:
    language_error = _language_guard(original, rewritten)
    if language_error:
        return language_error
    if len(rewritten) > 100:
        return "title exceeds 100 characters"
    if "\n" in rewritten or "```" in rewritten or re.search(r"(?:\*\*|__|^\s*#)", rewritten):
        return "title contains markdown or explanation formatting"
    if re.match(r"(?i)^\s*(?:here(?:'s| is)|sure[,!:]|rewritten title|output)\b", rewritten):
        return "title contains model explanation"
    if re.search(r"[!?]{2,}", rewritten):
        return "title contains spam punctuation"

    original_numbers, rewritten_numbers = _numbers(original), _numbers(rewritten)
    if rewritten_numbers - original_numbers:
        return "title changes numeric facts"
    if original_numbers and not rewritten_numbers:
        return "title removes all useful numbers"

    letters = [character for character in rewritten if character.isalpha()]
    uppercase_words = re.findall(r"\b[A-Z]{3,}\b", rewritten)
    if letters and (len(uppercase_words) >= 2 or sum(c.isupper() for c in letters) / len(letters) > 0.65):
        return "title contains excessive uppercase"

    words = re.findall(r"[^\W_]+", rewritten)
    ascii_letters = sum(character.isascii() and character.isalpha() for character in rewritten)
    ascii_dominant = not letters or ascii_letters / len(letters) >= 0.7
    if ascii_dominant and len(words) <= 3 and not _has_hook(rewritten) and not rewritten_numbers:
        return "title is only a generic topic"
    original_has_emphasis = (
        _has_hook(original) or "!" in original
        or bool(re.search(r"\b[A-Z]{3,}\b", original))
    )
    if ascii_dominant and original_has_emphasis and len(words) <= 5 and not _has_hook(rewritten):
        return "title loses the original engagement hook"
    return None


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
    attempts = 0
    guard_rejections: list[str] = []
    status, rewritten, model, error = "FALLBACK", original_title, None, None
    try:
        if client is None:
            from qwen_worker.client import QwenWorkerClient
            request_timeout = float(os.getenv("TITLE_REWRITE_TIMEOUT", "30"))
            client = QwenWorkerClient(timeout=min(0.25, request_timeout))
            health = client.health()
            if health.get("status") != "READY":
                raise RuntimeError(f"Qwen worker not READY: {health.get('status')}")
            client.timeout = request_timeout
            model = health.get("model")
        prompt = TITLE_REWRITE_PROMPT.format(title=original_title)
        for attempt in range(2):
            attempts += 1
            text = client.generate_text(
                [], prompt, max_new_tokens=32, task="title_rewrite", retry=False,
            )
            queue_wait += float(getattr(client, "last_queue_wait", 0.0))
            generation += float(getattr(client, "last_generation_time", 0.0))
            try:
                candidate = _parse_title(text)
                rejection = _guard_title(original_title, candidate)
                if rejection:
                    raise ValueError(rejection)
            except (ValueError, json.JSONDecodeError) as exc:
                guard_rejections.append(str(exc))
                if attempt == 0:
                    prompt += (
                        "\nYour previous response was rejected because: " + str(exc)
                        + ". Return a stronger truthful title and valid JSON only."
                    )
                    continue
                raise
            rewritten = candidate
            status = "APPLIED"
            break
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
        "generation_count": attempts,
        "retry_count": max(0, attempts - 1),
        "guard_rejections": guard_rejections,
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
