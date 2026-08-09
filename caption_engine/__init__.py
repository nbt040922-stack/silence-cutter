"""Faster-Whisper caption generation."""

from .config import CaptionConfig
from .pipeline import generate_captions

__all__ = ["CaptionConfig", "generate_captions"]
