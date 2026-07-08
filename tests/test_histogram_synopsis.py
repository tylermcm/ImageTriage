from __future__ import annotations

from image_triage.review_tools import EMPTY_INSPECTION_STATS, InspectionStats, histogram_synopsis


def _stats(
    ranges: tuple[tuple[int, int, int], ...],
    *,
    shadow_clip_pct: float = 0.0,
    highlight_clip_pct: float = 0.0,
) -> InspectionStats:
    histogram = [0 for _ in range(256)]
    for start, end, value in ranges:
        for index in range(start, end):
            histogram[index] = value
    total = sum(histogram)
    mean = sum(index * count for index, count in enumerate(histogram)) / max(1, total)
    return InspectionStats(
        width=100,
        height=100,
        mean_luminance=mean,
        median_luminance=mean,
        shadow_clip_pct=shadow_clip_pct,
        highlight_clip_pct=highlight_clip_pct,
        detail_score=50.0,
        histogram_luma=tuple(histogram),
        histogram_red=tuple(histogram),
        histogram_green=tuple(histogram),
        histogram_blue=tuple(histogram),
    )


def test_histogram_synopsis_handles_missing_stats() -> None:
    assert histogram_synopsis(None) == "Not analyzed"
    assert histogram_synopsis(EMPTY_INSPECTION_STATS) == "Not analyzed"


def test_histogram_synopsis_neutral_midtones() -> None:
    stats = _stats(((90, 166, 10), (122, 136, 20)))

    assert histogram_synopsis(stats).startswith("Neutral:")


def test_histogram_synopsis_extreme_contrast() -> None:
    stats = _stats(((24, 62, 10), (190, 228, 10)))

    assert histogram_synopsis(stats).startswith("Extreme contrast:")


def test_histogram_synopsis_exposed_left_without_clipping() -> None:
    stats = _stats(((28, 84, 10), (100, 145, 2)))

    assert histogram_synopsis(stats).startswith("Exposed left:")


def test_histogram_synopsis_exposed_right_without_clipping() -> None:
    stats = _stats(((112, 155, 2), (174, 230, 10)))

    assert histogram_synopsis(stats).startswith("Exposed right:")


def test_histogram_synopsis_underexposed_clipped_shadows() -> None:
    stats = _stats(((0, 8, 100), (42, 86, 2)), shadow_clip_pct=5.0)

    assert histogram_synopsis(stats).startswith("Underexposed:")


def test_histogram_synopsis_overexposed_clipped_highlights() -> None:
    stats = _stats(((180, 222, 2), (248, 256, 100)), highlight_clip_pct=3.0)

    assert histogram_synopsis(stats).startswith("Overexposed:")
