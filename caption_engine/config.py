from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class CaptionConfig:
    model_size: str = "large-v3-turbo"
    device: str = "cuda"
    compute_type: str = "float16"
    language: str | None = None
    beam_size: int = 5
    word_timestamps: bool = True
    batch_enabled: bool = True
    batch_size: int = 8
    max_chars_per_line: int = 42
    max_lines: int = 2
    min_caption_duration: float = 0.7
    max_caption_duration: float = 5.0
    max_words_per_caption: int = 12
    max_gap_between_words: float = 0.8
    allow_cpu_fallback: bool = False
    fallback_compute_type: str = "int8"

    def __post_init__(self) -> None:
        if not self.model_size.strip():
            raise ValueError("model_size must not be empty")
        if not self.device.strip() or not self.compute_type.strip():
            raise ValueError("device and compute_type must not be empty")
        if not self.word_timestamps:
            raise ValueError("word_timestamps must remain enabled")
        for name in (
            "beam_size",
            "batch_size",
            "max_chars_per_line",
            "max_lines",
            "max_words_per_caption",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.min_caption_duration < 0 or self.max_gap_between_words < 0:
            raise ValueError("caption timing limits must be non-negative")
        if self.max_caption_duration <= 0:
            raise ValueError("max_caption_duration must be positive")
        if self.min_caption_duration > self.max_caption_duration:
            raise ValueError("min_caption_duration cannot exceed maximum")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
