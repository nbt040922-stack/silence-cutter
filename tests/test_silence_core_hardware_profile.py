import pytest

from silence_core.hardware_profile import select_qwen_profile


def test_3060_profile_uses_qwen_7b():
    profile = select_qwen_profile(12 * 1024)
    assert profile.name == "qwen7b"
    assert profile.model_directory == "qwen2.5-vl-7b"


def test_2080_super_profile_uses_qwen_3b():
    profile = select_qwen_profile(8 * 1024)
    assert profile.name == "qwen3b"
    assert profile.model_directory == "qwen2.5-vl-3b"


def test_small_gpu_is_rejected():
    with pytest.raises(ValueError):
        select_qwen_profile(5 * 1024)
