"""Applies redaction (blur/mask) to regions flagged by PII or face
detectors before a screenshot is persisted or transmitted."""

from typing import List, Tuple
import numpy as np


def redact_regions(image: np.ndarray, boxes: List[Tuple[int, int, int, int]],
                    method: str = "blur") -> np.ndarray:
    """Redact the given bounding boxes in the image.

    Args:
        image: RGB image array (H, W, 3).
        boxes: list of (x1, y1, x2, y2) regions to redact.
        method: "blur" or "black_box".

    Returns:
        The redacted image (copy, does not mutate input).
    """
    output = image.copy()
    for (x1, y1, x2, y2) in boxes:
        if method == "black_box":
            output[y1:y2, x1:x2] = 0
        else:
            # TODO: apply a real gaussian blur (e.g. via cv2.GaussianBlur)
            output[y1:y2, x1:x2] = output[y1:y2, x1:x2] // 4
    return output
