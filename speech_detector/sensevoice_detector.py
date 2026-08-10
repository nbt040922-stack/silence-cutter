from __future__ import annotations

import time
from pathlib import Path

from .config import HighRecallConfig
from .fusion import normalize_intervals
from .models import SpeechInterval


class SenseVoiceDetector:
    def __init__(self, config: HighRecallConfig) -> None:
        self.config = config
        self._model = None
        self.model_load_time = 0.0

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _load(self):
        if self._model is None:
            from funasr import AutoModel

            started = time.perf_counter()
            self._model = AutoModel(
                model=self.config.sensevoice_model,
                trust_remote_code=True,
                remote_code="./model.py",
                vad_model="fsmn-vad",
                vad_kwargs={"max_single_segment_time": 30000},
                device=self.config.sensevoice_device,
                disable_update=True,
            )
            self.model_load_time = time.perf_counter() - started
        return self._model

    def detect(
        self, audio_path: Path, duration: float
    ) -> tuple[list[SpeechInterval], float, dict[str, float | int | str]]:
        model = self._load()
        started = time.perf_counter()
        asr_result = model.generate(
            input=str(audio_path),
            cache={},
            language=self.config.sensevoice_language,
            use_itn=True,
            batch_size_s=60,
            sentence_timestamp=True,
            max_end_silence_time=800,
        )
        vad_result = model.inference(
            str(audio_path),
            model=model.vad_model,
            kwargs=model.vad_kwargs,
            max_end_silence_time=self.config.sensevoice_end_silence_ms,
        )
        inference_time = time.perf_counter() - started
        asr_intervals = []
        for item in asr_result if isinstance(asr_result, list) else [asr_result]:
            if not isinstance(item, dict):
                continue
            for sentence in item.get("sentence_info") or []:
                try:
                    asr_intervals.append(
                        SpeechInterval(
                            float(sentence["start"]) / 1000,
                            float(sentence["end"]) / 1000,
                            "sensevoice_asr",
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    continue
        coarse = normalize_intervals(asr_intervals, duration, "sensevoice_asr")
        fine = []
        for item in vad_result if isinstance(vad_result, list) else [vad_result]:
            if not isinstance(item, dict):
                continue
            for segment in item.get("value") or []:
                try:
                    fine.append(
                        SpeechInterval(
                            float(segment[0]) / 1000,
                            float(segment[1]) / 1000,
                            "sensevoice",
                        )
                    )
                except (IndexError, TypeError, ValueError):
                    continue
        fine = sorted(
            (
                SpeechInterval(
                    max(0.0, item.start), min(duration, item.end), "sensevoice"
                )
                for item in fine
                if item.end > 0 and item.start < duration
            ),
            key=lambda item: (item.start, item.end),
        )
        diagnostics = {
            "sensevoice_timing_source": "raw_fsmn_vad",
            "sensevoice_raw_asr_segment_count": len(coarse),
            "sensevoice_raw_asr_segment_duration": sum(i.end - i.start for i in coarse),
            "sensevoice_fine_speech_interval_count": len(fine),
            "sensevoice_fine_speech_duration": sum(i.end - i.start for i in fine),
            "largest_sensevoice_asr_segment": max(
                (i.end - i.start for i in coarse), default=0.0
            ),
            "largest_sensevoice_fine_speech_interval": max(
                (i.end - i.start for i in fine), default=0.0
            ),
        }
        return fine, inference_time, diagnostics
