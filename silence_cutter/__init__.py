"""Silero VAD based video silence cutter."""

from .config import SilenceCutterConfig
from .pipeline import cut_silence

__all__ = ["SilenceCutterConfig", "cut_silence"]
