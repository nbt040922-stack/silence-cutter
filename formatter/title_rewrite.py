from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any


TITLE_REWRITE_PROMPT = """You are the mandatory TikTok title editor.

Always rewrite the input title. Never return the original title unchanged; never return the original title, leave the field empty, or answer that no rewrite is needed.

Language and meaning:
- Detect the source title's primary language and writing system.
- Write the rewritten title in the same language and writing system (SAME LANGUAGE as the source); never translate it.
- Preserve the core subject, named entities, useful numbers, event and strongest truthful reason to watch.
- Never invent facts, change the meaning, or turn the title into a vague generic topic.

TikTok Community Guidelines:
- Make the title suitable for the TikTok Community Guidelines.
- Do not promote hate, threats, violence, sexual exploitation of minors, explicit sexual content, self-harm, dangerous illegal acts, harassment, defamation, or dangerous medical/financial/legal claims.
- If the source contains unsafe or disallowed wording, rewrite it neutrally while preserving the legitimate topic.
- Do not use deceptive clickbait, fabricated urgency, false certainty, or claims such as SHOCKING, YOU WON'T BELIEVE, MUST WATCH, or 100%% GUARANTEED.

Style and layout:
- Keep it concise, natural, compelling and truthful; prefer 6-12 words or the equivalent in the source language.
- Stay under 60 characters when the language allows; Japanese/Chinese/Korean should stay under 42 characters.
- Make it fit a three-line vertical mobile banner.
- Remove greetings, filler, repetition, decorative brackets, hashtags, markdown, SEO lists, excessive emoji, ALL CAPS and repeated punctuation.
- Keep a clear hook based on the real content, not an invented promise.

Output contract:
- Return only valid JSON. No markdown, explanation, apology or extra keys.
- The rewritten_title field is mandatory and must contain a newly rewritten title.
- Use this exact schema: {{\"rewritten_title\":\"...\"}}

ORIGINAL TITLE: {title}"""
TITLE_REWRITE_PROMPT_VERSION = 2

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
    cjk = bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", original))
    if len(rewritten) > (42 if cjk else 72):
        return "title is too long for the mobile banner"
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


def _compact_title(title: str) -> str:
    """Create a deterministic short fallback without changing the title's subject."""
    value = re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(title))).strip()
    value = re.sub(r"[【】\[\]{}]", "", value)
    value = re.sub(r"([!?！？。])\1+", r"\1", value)
    has_cjk = bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", value))
    limit = 42 if has_cjk else 72
    if len(value) <= limit:
        return value
    if has_cjk:
        prefix = value[: limit - 1]
        boundaries = [prefix.rfind(mark) for mark in "。！？!?、"]
        boundary = max(boundaries)
        if boundary >= 18:
            prefix = prefix[: boundary + 1]
        return prefix.rstrip(" 　、・:：-—") + "…"
    words = value.split()
    compact: list[str] = []
    length = 0
    for word in words:
        added = len(word) if not compact else len(word) + 1
        if length + added > limit - 1:
            break
        compact.append(word)
        length += added
    return (" ".join(compact) or value[: limit - 1]).rstrip(" .,!?;:") + "…"


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
    allow_qwen: bool = True,
) -> dict[str, Any]:
    artifact_path = Path(job_dir) / "title_rewrite.json"
    if artifact_path.is_file():
        try:
            cached = json.loads(artifact_path.read_text(encoding="utf-8"))
            cached_title = str(cached.get("rewritten_title") or "")
            max_cached = 42 if re.search(
                r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", original_title
            ) else 72
            if (
                cached.get("prompt_version") == TITLE_REWRITE_PROMPT_VERSION
                and cached.get("filename_base") and cached_title
                and len(cached_title) <= max_cached
            ):
                return cached
        except (OSError, ValueError):
            pass
    started = time.perf_counter()
    queue_wait = generation = 0.0
    attempts = 0
    guard_rejections: list[str] = []
    status, rewritten, model, error = "FALLBACK", original_title, None, None
    try:
        if not allow_qwen:
            raise RuntimeError("Qwen title rewrite disabled for safe per-part pipeline")
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
    if status != "APPLIED":
        rewritten = _compact_title(rewritten)
    safe_rewritten = safe_filename_title(rewritten, fallback_id=source_id)
    filename_base = _choose_base(
        Path(output_dir), safe_rewritten, source_id, part_count,
    )
    artifact = {
        "schema_version": 2,
        "prompt_version": TITLE_REWRITE_PROMPT_VERSION,
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
