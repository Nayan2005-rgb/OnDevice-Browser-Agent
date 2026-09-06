"""Tests for the vision pipeline (detection, capture, redaction)."""

import numpy as np

from vision.coordinate_mapping import map_box_to_screenshot
from vision.redaction import redact_regions, select_redaction_strategy


def test_redact_regions_black_box():
    image = np.ones((10, 10, 3), dtype=np.uint8) * 255
    redacted = redact_regions(image, [(2, 2, 6, 6)], method="black_box")
    assert (redacted[2:6, 2:6] == 0).all()
    assert (redacted[0, 0] == 255).all()


def test_redact_regions_blur_changes_image():
    image = np.zeros((24, 24, 3), dtype=np.uint8)
    image[0:12, :, :] = 255
    redacted = redact_regions(image, [(0, 0, 24, 24)], method="blur")
    assert int(redacted[12, 12, 0]) not in (0, 255)
    assert redacted.std() < image.std()


def test_map_box_example_from_spec():
    mapped = map_box_to_screenshot(
        {"x": 100, "y": 200, "width": 300, "height": 40},
        {"width": 1200, "height": 800},
        {"width": 2400, "height": 1600},
    )
    assert mapped["x"] == 200
    assert mapped["y"] == 400
    assert mapped["width"] == 600
    assert mapped["height"] == 80


def test_select_strategy():
    assert select_redaction_strategy("password") == "black_box"
    assert select_redaction_strategy("email") == "blur"
