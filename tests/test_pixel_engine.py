"""Tests for the lightweight pixel analyzer facade."""

from core.pixel_engine.pixel_analyzer import FrameDiff, PixelAnalyzer


def test_first_frame_returns_zero_diff():
    analyzer = PixelAnalyzer()
    result = analyzer.process_frame({"hash": "a"})

    assert isinstance(result, FrameDiff)
    assert result.changed_ratio == 0.0
    assert not result.motion_detected


def test_duplicate_frame_is_deduped():
    analyzer = PixelAnalyzer()
    analyzer.process_frame({"hash": "a"})

    assert analyzer.process_frame({"hash": "a"}) is None
    assert analyzer.dedup_stats["skipped"] == 1


def test_activity_level_stays_zero_without_rust_diff():
    analyzer = PixelAnalyzer()
    for idx in range(3):
        analyzer.process_frame({"hash": str(idx)})

    assert analyzer.get_activity_level() == 0.0
    assert not analyzer.detect_loading_state()
