from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "asr_benchmark"
WHISPER = ROOT / "input.boundary3.captions.json"
AUDIO = OUT / "input_16k_mono.wav"


def _merged(items):
    result = []
    for start, end in sorted(items):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        elif end > start:
            result.append((start, end))
    return result


def _subtract(source, covered):
    result = []
    for start, end in source:
        cursor = start
        for left, right in covered:
            if right <= cursor:
                continue
            if left >= end:
                break
            if left > cursor:
                result.append((cursor, min(left, end)))
            cursor = max(cursor, right)
            if cursor >= end:
                break
        if cursor < end:
            result.append((cursor, end))
    return result


def prepare() -> None:
    sys.path.insert(0, str(ROOT))
    from silence_cutter.vad import detect_speech

    data = json.loads(WHISPER.read_text(encoding="utf-8"))
    words = [word for segment in data["segments"] for word in segment.get("words", [])]
    speech_raw = detect_speech(AUDIO)
    speech = _merged((float(item["start"]), float(item["end"])) for item in speech_raw)
    word_intervals = _merged((float(word["start"]), float(word["end"])) for word in words)
    gaps = []
    for start, end in _subtract(speech, word_intervals):
        if end - start < 0.30:
            continue
        previous = [word["text"] for word in words if float(word["end"]) <= start][-5:]
        following = [word["text"] for word in words if float(word["start"]) >= end][:5]
        gaps.append({
            "start": start, "end": end, "duration": end - start,
            "previous_whisper_text": "".join(previous),
            "next_whisper_text": "".join(following),
            "vad_speech_ratio": 1.0,
        })
    transcript = "".join(word["text"] for word in words)
    (OUT / "whisper_boundary3.txt").write_text(transcript + "\n", encoding="utf-8")
    (OUT / "whisper_boundary3_words.json").write_text(
        json.dumps(words, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "whisper_speech_gaps.json").write_text(
        json.dumps({"vad_speech_intervals": speech_raw, "gaps": gaps}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Prepared {len(gaps)} VAD-positive Whisper gaps")


def _content(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u3040-\u30ff\u3400-\u9fff]", "", text)


def _novel(candidate: str, previous: str, following: str) -> bool:
    candidate, previous, following = map(_content, (candidate, previous, following))
    for size in range(min(len(candidate), len(previous)), 1, -1):
        if candidate.startswith(previous[-size:]):
            candidate = candidate[size:]
            break
    for size in range(min(len(candidate), len(following)), 1, -1):
        if candidate.endswith(following[:size]):
            candidate = candidate[:-size]
            break
    return len(candidate) >= 2


def report() -> None:
    gaps_data = json.loads((OUT / "whisper_speech_gaps.json").read_text(encoding="utf-8"))
    gaps = gaps_data["gaps"]
    sense = json.loads((OUT / "sensevoice_raw.json").read_text(encoding="utf-8"))
    fun = json.loads((OUT / "funasr_raw.json").read_text(encoding="utf-8"))
    comparisons = []
    for index, gap in enumerate(gaps, start=1):
        sense_text = sense["gap_chunks"][index - 1]["text"]
        fun_text = fun["gap_chunks"][index - 1]["text"]
        comparisons.append({
            "start": gap["start"], "end": gap["end"], "duration": gap["duration"],
            "whisper": "", "sensevoice": sense_text, "funasr": fun_text,
            "sensevoice_recovered": _novel(sense_text, gap["previous_whisper_text"], gap["next_whisper_text"]),
            "funasr_recovered": _novel(fun_text, gap["previous_whisper_text"], gap["next_whisper_text"]),
        })
    (OUT / "gap_recovery_comparison.json").write_text(
        json.dumps(comparisons, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    sense_count = sum(item["sensevoice_recovered"] for item in comparisons)
    fun_count = sum(item["funasr_recovered"] for item in comparisons)
    best = "SenseVoiceSmall" if sense_count > fun_count else "Fun-ASR-Nano-2512" if fun_count > sense_count else "Tie"
    rows = [
        f"| {item['start']:.2f}-{item['end']:.2f} | MISS | {item['sensevoice'] or 'MISS'} | {item['funasr'] or 'MISS'} |"
        for item in comparisons
    ]
    env = sense["environment"]
    markdown = f"""# ASR Benchmark Report

## Environment

- Python: {env['python'].split()[0]}
- PyTorch: {env['torch']}
- FunASR: {env['funasr']}
- GPU: {env['gpu']}
- CUDA runtime: {env['torch_cuda_runtime']}
- Input duration: {sense['input_duration']:.3f}s

## Timing

| Model | Load | Inference | x realtime | Timestamps |
|---|---:|---:|---:|---|
| SenseVoiceSmall | {sense['model_load_time']:.3f}s | {sense['inference_time']:.3f}s | {sense['x_realtime']:.2f}x | {sense['timestamps_available']} |
| Fun-ASR-Nano-2512 | {fun['model_load_time']:.3f}s | {fun['inference_time']:.3f}s | {fun['x_realtime']:.2f}x | unavailable/reliable word timing not exposed |

## Whisper baseline and gap recovery

- Boundary3 words: {len(json.loads((OUT / 'whisper_boundary3_words.json').read_text(encoding='utf-8')))}
- VAD-positive Whisper gaps: {len(gaps)}
- SenseVoice recoveries: {sense_count}/{len(gaps)} ({sense_count / len(gaps) * 100 if gaps else 0:.1f}%)
- Fun-ASR recoveries: {fun_count}/{len(gaps)} ({fun_count / len(gaps) * 100 if gaps else 0:.1f}%)
- Best recovery model: {best}

Recovery is a conservative text-novelty heuristic on each gap plus 0.5s context; it does not claim semantic correctness.

| Gap | Whisper | SenseVoice | Fun-ASR |
|---|---|---|---|
{chr(10).join(rows)}
"""
    (OUT / "ASR_BENCHMARK_REPORT.md").write_text(markdown, encoding="utf-8")
    print(f"SenseVoice:\n- load time: {sense['model_load_time']:.3f}s\n- inference time: {sense['inference_time']:.3f}s\n- x realtime: {sense['x_realtime']:.2f}x\n- gap recoveries: {sense_count}/{len(gaps)}")
    print(f"\nFun-ASR:\n- load time: {fun['model_load_time']:.3f}s\n- inference time: {fun['inference_time']:.3f}s\n- x realtime: {fun['x_realtime']:.2f}x\n- gap recoveries: {fun_count}/{len(gaps)}")
    print(f"\nWhisper:\n- VAD-positive uncovered gap count: {len(gaps)}")
    print(f"\nBest recovery model: {best}\n\nReason:\nRecovered more VAD-confirmed speech gaps missed by Whisper.\n\n{OUT}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "report"))
    command = parser.parse_args().command
    prepare() if command == "prepare" else report()
