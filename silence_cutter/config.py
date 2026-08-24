from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class SilenceCutterConfig:
    sample_rate: int = 16_000
    vad_threshold: float = 0.50
    min_silence_duration: float = 1.0
    speech_pad_before: float = 0.25
    speech_pad_after: float = 0.30
    merge_gap: float = 0.35
    min_keep_duration: float = 0.50
    natural_silence_min: float = 0.0
    natural_silence_max: float = 0.0
    natural_silence_seed: int | None = None

    def __post_init__(self) -> None:
        if self.sample_rate != 16_000:
            raise ValueError("Silero VAD requires sample_rate=16000 in this module")
        if not 0 <= self.vad_threshold <= 1:
            raise ValueError("vad_threshold must be between 0 and 1")
        for name in (
            "min_silence_duration",
            "speech_pad_before",
            "speech_pad_after",
            "merge_gap",
            "min_keep_duration",
            "natural_silence_min",
            "natural_silence_max",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.natural_silence_max < self.natural_silence_min:
            raise ValueError("natural_silence_max must be >= natural_silence_min")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
