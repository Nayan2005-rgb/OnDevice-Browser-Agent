"""Lightweight on-device object detection over captured screenshots,
used to ground the agent's perception of on-screen elements."""

import onnxruntime as ort
import numpy as np


class ObjectDetector:
    def __init__(self, model_path: str = "vision/model/vision_model.onnx"):
        self.model_path = model_path
        self.session = None

    def load(self):
        self.session = ort.InferenceSession(self.model_path)
        return self

    def detect(self, image: np.ndarray):
        """Run detection on an RGB image array.

        Returns:
            List[dict]: [{"label": str, "bbox": [x1,y1,x2,y2], "score": float}, ...]
        """
        if self.session is None:
            raise RuntimeError("Call load() before detect().")
        # TODO: preprocess image, run session.run(...), postprocess outputs
        return []
