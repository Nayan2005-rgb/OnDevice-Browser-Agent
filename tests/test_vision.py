"""Tests for the vision pipeline (detection, capture)."""

import numpy as np
from vision.redaction import redact_regions


def test_redact_regions_black_box():
    image = np.ones((10, 10, 3), dtype=np.uint8) * 255
    redacted = redact_regions(image, [(2, 2, 6, 6)], method="black_box")
    assert (redacted[2:6, 2:6] == 0).all()
    assert (redacted[0, 0] == 255).all()


def test_redact_regions_blur_darkens_region():
    image = np.ones((10, 10, 3), dtype=np.uint8) * 200
    redacted = redact_regions(image, [(0, 0, 4, 4)], method="blur")
    assert redacted[0, 0, 0] < 200
