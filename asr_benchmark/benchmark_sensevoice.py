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
    from funasr.utils.postprocess_utils import rich_transcription_postprocess

    started = time.perf_counter()
    load_started = time.perf_counter()
    model = AutoModel(
        model="iic/SenseVoiceSmall",
        trust_remote_code=True,
        remote_code="./model.py",
        vad_model="fsmn-vad",
        vad_kwargs={"max_single_segment_time": 30000},
        device="cuda:0",
    )
    load_time = time.perf_counter() - load_started

    def infer(path: str) -> tuple[object, str]:
        raw = model.generate(
            input=path,
            cache={},
            language="ja",
            use_itn=True,
            batch_size_s=60,
            merge_vad=True,
            merge_length_s=15,
            sentence_timestamp=True,
        )
        return raw, clean_text(rich_transcription_postprocess(text_from_result(raw)))

    inference_started = time.perf_counter()
    raw, text = infer(str(AUDIO))
    inference_time = time.perf_counter() - inference_started
    chunks = []
    gap_started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="sensevoice-gaps-", dir=OUT) as directory:
        for index, gap in enumerate(gap_items(), start=1):
            context_start = max(0.0, float(gap["start"]) - 0.5)
            context_end = min(audio_duration(), float(gap["end"]) + 0.5)
            path = write_chunk(AUDIO, OUT / Path(directory).name / f"gap-{index:03d}.wav", context_start, context_end)
            chunk_raw, chunk_text = infer(str(path))
            chunks.append({
                "gap_index": index, "start": gap["start"], "end": gap["end"],
                "context_start": context_start, "context_end": context_end,
                "text": chunk_text, "raw": json_safe(chunk_raw),
            })
    duration = audio_duration()
    report = {
        "model": "iic/SenseVoiceSmall", "language": "ja", "device": "cuda:0",
        "environment": environment(), "input_duration": duration,
        "model_load_time": load_time, "inference_time": inference_time,
        "total_time": time.perf_counter() - started,
        "x_realtime": duration / inference_time if inference_time else 0.0,
        "timestamps_available": any(
            isinstance(item, dict) and any(key in item for key in ("timestamp", "sentence_info"))
            for item in (raw if isinstance(raw, list) else [raw])
        ),
        "text": text, "raw": json_safe(raw), "gap_inference_time": time.perf_counter() - gap_started,
        "gap_chunks": chunks,
    }
    (OUT / "sensevoice_raw.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "sensevoice.txt").write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
