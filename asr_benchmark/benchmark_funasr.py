from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from benchmark_common import (
    AUDIO, OUT, audio_duration, clean_text, environment, gap_items, json_safe,
    text_from_result, write_chunk,
)


def main() -> None:
    from funasr import AutoModel

    started = time.perf_counter()
    load_started = time.perf_counter()
    model = AutoModel(
        model="FunAudioLLM/Fun-ASR-Nano-2512",
        hub="hf",
        trust_remote_code=True,
        vad_model="funasr/fsmn-vad",
        vad_kwargs={"max_single_segment_time": 30000},
        device="cuda:0",
    )
    load_time = time.perf_counter() - load_started

    def infer(path: str) -> tuple[object, str]:
        raw = model.generate(
            input=[path], cache={}, batch_size=1, language="日文", itn=True
        )
        return raw, clean_text(text_from_result(raw))

    inference_started = time.perf_counter()
    raw, text = infer(str(AUDIO))
    inference_time = time.perf_counter() - inference_started
    chunks = []
    gap_started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="funasr-gaps-", dir=OUT) as directory:
        directory_path = Path(directory)
        for index, gap in enumerate(gap_items(), start=1):
            context_start = max(0.0, float(gap["start"]) - 0.5)
            context_end = min(audio_duration(), float(gap["end"]) + 0.5)
            path = write_chunk(AUDIO, directory_path / f"gap-{index:03d}.wav", context_start, context_end)
            chunk_raw, chunk_text = infer(str(path))
            chunks.append({
                "gap_index": index, "start": gap["start"], "end": gap["end"],
                "context_start": context_start, "context_end": context_end,
                "text": chunk_text, "raw": json_safe(chunk_raw),
            })
    duration = audio_duration()
    report = {
        "model": "FunAudioLLM/Fun-ASR-Nano-2512", "language": "日文", "device": "cuda:0",
        "environment": environment(), "input_duration": duration,
        "model_load_time": load_time, "inference_time": inference_time,
        "total_time": time.perf_counter() - started,
        "x_realtime": duration / inference_time if inference_time else 0.0,
        "timestamps_available": False,
        "timestamp_note": "Official Fun-ASR-Nano-2512 inference path does not expose reliable word timestamps.",
        "text": text, "raw": json_safe(raw), "gap_inference_time": time.perf_counter() - gap_started,
        "gap_chunks": chunks,
    }
    (OUT / "funasr_raw.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "funasr.txt").write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
