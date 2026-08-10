from __future__ import annotations

import time
from pathlib import Path

from .config import HighRecallConfig
from .models import SpeechInterval


class SenseVoiceDetector:
    def __init__(self, config: HighRecallConfig) -> None:
        self.config = config
        self._model = None
        self.model_load_time = 0.0
        self.requested_device = config.sensevoice_device
        self.active_device = config.sensevoice_device
        self.cuda_fallback = False
        self.cuda_error: str | None = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _create_model(self, device: str):
        from funasr import AutoModel

        return AutoModel(
            model=self.config.sensevoice_model,
            trust_remote_code=True,
            remote_code="./model.py",
            vad_model=self.config.sensevoice_vad_model,
            vad_kwargs={"max_single_segment_time": 30000},
            device=device,
            disable_update=True,
        )

    def _load(self):
        if self._model is None:
            started = time.perf_counter()
            try:
                self._model = self._create_model(self.requested_device)
                self.active_device = self.requested_device
            except Exception as exc:
                if not self.requested_device.lower().startswith("cuda"):
                    raise
                self.cuda_error = str(exc)
                self.cuda_fallback = True
                self.active_device = "cpu"
                self._model = self._create_model("cpu")
            self.model_load_time += time.perf_counter() - started
        return self._model

    def _infer(self, model, audio_path: Path):
        return model.inference(
            str(audio_path),
            model=model.vad_model,
            kwargs=model.vad_kwargs,
            max_end_silence_time=self.config.sensevoice_end_silence_ms,
        )

    def detect(
        self, audio_path: Path, duration: float
    ) -> tuple[list[SpeechInterval], float, dict[str, float | int | str]]:
        model = self._load()
        started = time.perf_counter()
        try:
            vad_result = self._infer(model, audio_path)
        except Exception as exc:
            if not self.active_device.lower().startswith("cuda"):
                raise
            self.cuda_error = str(exc)
            self.cuda_fallback = True
            self._model = None
            self.active_device = "cpu"
            load_started = time.perf_counter()
            self._model = self._create_model("cpu")
            self.model_load_time += time.perf_counter() - load_started
            vad_result = self._infer(self._model, audio_path)
        inference_time = time.perf_counter() - started
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
            "sensevoice_requested_device": self.requested_device,
            "sensevoice_active_device": self.active_device,
            "sensevoice_cuda_fallback": self.cuda_fallback,
            "sensevoice_cuda_error": self.cuda_error or "",
            "sensevoice_timing_source": "raw_fsmn_vad",
            "sensevoice_raw_asr_segment_count": 0,
            "sensevoice_raw_asr_segment_duration": 0.0,
            "sensevoice_fine_speech_interval_count": len(fine),
            "sensevoice_fine_speech_duration": sum(i.end - i.start for i in fine),
            "largest_sensevoice_asr_segment": 0.0,
            "largest_sensevoice_fine_speech_interval": max(
                (i.end - i.start for i in fine), default=0.0
            ),
        }
        return fine, inference_time, diagnostics
