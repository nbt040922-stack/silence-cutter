import pytest

from qwen_part_policy import (
    cap_part_range, cap_source_segments, part_role,
    should_inspect_with_qwen, subtract_source_ranges,
)


@pytest.mark.parametrize("duration", [0.0, 1499.9, 1500.0])
def test_videos_at_or_below_25_minutes_skip_qwen(duration):
    assert should_inspect_with_qwen(duration) is False


def test_videos_over_25_minutes_allow_qwen():
    assert should_inspect_with_qwen(1500.1) is True


def test_part_roles_are_fixed_to_the_three_requested_checks():
    assert [part_role(index) for index in (1, 2, 3, 4)] == ["INTRO", "AD", "OUTTRO", None]


def test_part_under_ten_minutes_keeps_its_timestamp_range():
    assert cap_part_range(120.0, 500.0, 2000.0) == {"start": 120.0, "end": 500.0}


def test_part_over_ten_minutes_is_hard_capped_at_eight_minutes():
    assert cap_part_range(100.0, 900.0, 2000.0) == {"start": 100.0, "end": 580.0}


def test_part_range_must_be_inside_source():
    with pytest.raises(ValueError):
        cap_part_range(800.0, 1500.0, 1100.0)


def test_qwen_removals_are_subtracted_from_source_segments():
    assert subtract_source_ranges(
        [{"start": 0.0, "end": 900.0}],
        [{"start": 120.0, "end": 180.0}],
    ) == [{"start": 0.0, "end": 120.0}, {"start": 180.0, "end": 900.0}]


def test_long_part_is_render_capped_to_eight_minutes():
    segments = cap_source_segments([{"start": 0.0, "end": 700.0}])
    assert segments == [{"start": 0.0, "end": 480.0}]
