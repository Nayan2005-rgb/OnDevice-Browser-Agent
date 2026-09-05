"""Detects interactable UI elements (buttons, inputs, links) from a
screenshot so the action planner can target them."""

from typing import List, Dict
import numpy as np


class UIElementDetector:
    def __init__(self, model_path: str = "vision/model/vision_model.onnx"):
        self.model_path = model_path

    def detect_elements(self, image: np.ndarray) -> List[Dict]:
        """Return a list of detected UI elements with type, bbox, and confidence.

        TODO: implement using the ONNX UI-element model, or fall back to
        DOM-based detection from privacy/dom_detector.py when available.
        """
        return []
