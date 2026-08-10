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
    adaptive_segmentation: bool = True
    speech_rate_window_seconds: float = 5.0
    target_reading_cps: float = 17.0
    max_reading_cps: float = 22.0
    adaptive_reference_cps: float = 7.0
    adaptive_reference_wps: float = 2.4
    adaptive_base_target_duration: float = 2.8
    adaptive_min_target_duration: float = 1.2
    adaptive_max_target_duration: float = 4.2
    absolute_max_caption_duration: float = 6.0
    adaptive_pause_boundary: float = 0.45
    boundary_scoring_enabled: bool = True
    weak_pause_seconds: float = 0.12
    medium_pause_seconds: float = 0.25
    strong_pause_seconds: float = 0.45
    preferred_cjk_chars: int = 22
    soft_max_cjk_chars: int = 26
    absolute_max_cjk_chars: int = 30
    absolute_min_caption_duration: float = 0.35
    boundary_lookahead_tokens: int = 16
    boundary_preserve_score: float = 8.25
    caption_bridge_gap: float = 0.20
    caption_hold_max: float = 0.35

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
            "preferred_cjk_chars",
            "soft_max_cjk_chars",
            "absolute_max_cjk_chars",
            "boundary_lookahead_tokens",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.min_caption_duration < 0 or self.max_gap_between_words < 0:
            raise ValueError("caption timing limits must be non-negative")
        if self.max_caption_duration <= 0:
            raise ValueError("max_caption_duration must be positive")
        if self.min_caption_duration > self.max_caption_duration:
            raise ValueError("min_caption_duration cannot exceed maximum")
        for name in (
            "speech_rate_window_seconds",
            "target_reading_cps",
            "max_reading_cps",
            "adaptive_reference_cps",
            "adaptive_reference_wps",
            "adaptive_base_target_duration",
            "adaptive_min_target_duration",
            "adaptive_max_target_duration",
            "absolute_max_caption_duration",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.adaptive_pause_boundary < 0:
            raise ValueError("adaptive_pause_boundary must be non-negative")
        if self.target_reading_cps > self.max_reading_cps:
            raise ValueError("target_reading_cps cannot exceed maximum")
        if self.adaptive_min_target_duration > self.adaptive_max_target_duration:
            raise ValueError("adaptive target duration limits are invalid")
        if self.absolute_max_caption_duration < self.adaptive_max_target_duration:
            raise ValueError("absolute caption duration must cover adaptive maximum")
        if not (
            0 <= self.weak_pause_seconds
            <= self.medium_pause_seconds
            <= self.strong_pause_seconds
        ):
            raise ValueError("pause thresholds must be ordered and non-negative")
        if not (
            self.preferred_cjk_chars
            <= self.soft_max_cjk_chars
            <= self.absolute_max_cjk_chars
        ):
            raise ValueError("CJK character limits must be ordered")
        if self.absolute_min_caption_duration <= 0:
            raise ValueError("absolute_min_caption_duration must be positive")
        if self.boundary_preserve_score < 0:
            raise ValueError("boundary_preserve_score must be non-negative")
        if not 0 <= self.caption_bridge_gap <= self.caption_hold_max:
            raise ValueError("caption bridge timing limits are invalid")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
