from dataclasses import asdict, dataclass, field

from silence_cutter.config import SilenceCutterConfig
from silence_cutter.runtime_paths import model_reference


def _sensevoice_model() -> str:
    return model_reference("SenseVoiceSmall", "iic/SenseVoiceSmall")


def _sensevoice_vad_model() -> str:
    return model_reference(
        "fsmn-vad", "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch"
    )


@dataclass(frozen=True, slots=True)
class HighRecallConfig:
    sample_rate: int = 16_000
    vad_threshold: float = 0.50
    speech_pad_before: float = 0.0
    speech_pad_after: float = 0.0
    merge_gap: float = 0.15
    min_silence_duration: float = 0.50
    min_keep_duration: float = 0.0
    natural_silence_min: float = 0.1
    natural_silence_max: float = 0.3
    natural_silence_seed: int | None = None
    sensevoice_model: str = field(default_factory=_sensevoice_model)
    sensevoice_vad_model: str = field(default_factory=_sensevoice_vad_model)
    sensevoice_device: str = "cuda:0"
    sensevoice_language: str = "ja"
    sensevoice_end_silence_ms: int = 200
    parallel_detectors: bool = True

    def __post_init__(self) -> None:
        self.silence_config()
        if not self.sensevoice_model or not self.sensevoice_vad_model or not self.sensevoice_device:
            raise ValueError("SenseVoice model, VAD model and device must not be empty")

    def silence_config(self) -> SilenceCutterConfig:
        return SilenceCutterConfig(
            sample_rate=self.sample_rate,
            vad_threshold=self.vad_threshold,
            min_silence_duration=self.min_silence_duration,
            speech_pad_before=self.speech_pad_before,
            speech_pad_after=self.speech_pad_after,
            merge_gap=self.merge_gap,
            min_keep_duration=self.min_keep_duration,
            natural_silence_min=self.natural_silence_min,
            natural_silence_max=self.natural_silence_max,
            natural_silence_seed=self.natural_silence_seed,
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
