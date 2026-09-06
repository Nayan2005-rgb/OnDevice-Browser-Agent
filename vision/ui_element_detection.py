"""Detects interactable UI elements (buttons, inputs, links) from a
screenshot so the action planner can target them.

Milestone 3A: ContourBasedUIDetector is the default local backend.
Future: OnnxUIDetector / WebGPUUIDetector / TransformerUIDetector via
``create_ui_detector(backend=...)`` without changing callers.
"""

from typing import Any, Dict, List, Optional

import numpy as np

from vision.ui_detector import (
    ContourBasedUIDetector,
    DOMGeometryDetector,
    UIElementDetector,
    create_ui_detector,
)

__all__ = [
    "UIElementDetector",
    "ContourBasedUIDetector",
    "DOMGeometryDetector",
    "create_ui_detector",
    "detect_ui_elements",
]


def detect_ui_elements(
    image: Optional[np.ndarray] = None,
    *,
    backend: str = "contour",
    dom_elements: Optional[List[Dict[str, Any]]] = None,
    **kwargs: Any,
) -> List[Dict[str, Any]]:
    """Run a UI detector and return element dicts."""
    detector = create_ui_detector(backend, **kwargs)
    if isinstance(detector, DOMGeometryDetector):
        return [el.to_dict() for el in detector.from_dom_elements(dom_elements or [])]
    return detector.detect_elements(image)
