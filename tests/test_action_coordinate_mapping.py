"""Tests for screenshot → CSS viewport action coordinate mapping."""

import pytest

from vision.action_coordinate_mapping import (
    CoordinateMappingError,
    clamp_css_point,
    compute_inverse_scale,
    map_action_coordinates,
    screenshot_box_center_to_css,
    screenshot_point_to_css,
    validate_dimensions,
)


def test_scale_1_identity():
    screenshot = {"width": 1200, "height": 800}
    viewport = {"width": 1200, "height": 800}
    scale_x, scale_y = compute_inverse_scale(1200, 800, 1200, 800)
    assert scale_x == pytest.approx(1.0)
    assert scale_y == pytest.approx(1.0)
    pt = screenshot_point_to_css(1000, 600, screenshot, viewport)
    assert pt == {"x": 1000, "y": 600}


def test_scale_2_retina():
    """Screenshot 2400×1600, viewport 1200×800 → point (1000,600) → (500,300)."""
    screenshot = {"width": 2400, "height": 1600}
    viewport = {"width": 1200, "height": 800}
    pt = screenshot_point_to_css(1000, 600, screenshot, viewport)
    assert pt == {"x": 500, "y": 300}

    mapped = map_action_coordinates(1000, 600, screenshot, viewport)
    assert mapped["css"] == {"x": 500, "y": 300}
    assert mapped["scale_x"] == pytest.approx(2.0)
    assert mapped["scale_y"] == pytest.approx(2.0)


def test_non_uniform_scale():
    screenshot = {"width": 2000, "height": 1000}
    viewport = {"width": 1000, "height": 800}
    pt = screenshot_point_to_css(1000, 400, screenshot, viewport)
    # scale_x=2, scale_y=1.25 → css (500, 320)
    assert pt["x"] == 500
    assert pt["y"] == 320


def test_invalid_dimensions_rejected():
    with pytest.raises(CoordinateMappingError):
        compute_inverse_scale(0, 100, 100, 100)
    with pytest.raises(CoordinateMappingError):
        map_action_coordinates(10, 10, {"width": 0, "height": 10}, {"width": 10, "height": 10})
    assert validate_dimensions({"width": 0, "height": 10}, {"width": 10, "height": 10}) is False
    assert validate_dimensions({"width": 100, "height": 100}, {"width": 50, "height": 50}) is True


def test_coordinate_clamping():
    screenshot = {"width": 100, "height": 100}
    viewport = {"width": 50, "height": 50}
    # Far outside → clamped to viewport edge
    pt = screenshot_point_to_css(9999, 9999, screenshot, viewport, clamp=True)
    assert pt["x"] == 49
    assert pt["y"] == 49

    clamped = clamp_css_point(-5, 1000, viewport)
    assert clamped == {"x": 0, "y": 49}


def test_box_center_mapping():
    box = {"x": 100, "y": 200, "width": 40, "height": 20}  # center (120, 210)
    screenshot = {"width": 200, "height": 400}
    viewport = {"width": 100, "height": 200}  # scale 2
    pt = screenshot_box_center_to_css(box, screenshot, viewport)
    assert pt == {"x": 60, "y": 105}
