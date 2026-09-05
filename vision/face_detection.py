"""On-device face detection used to blur faces in screenshots before
they are stored, displayed, or sent to any remote service."""

from typing import List, Tuple
import onnxruntime as ort
import numpy as np


class FaceDetector:
    def __init__(self, model_path: str = "models/face_detection/face_model.onnx"):
        self.model_path = model_path
        self.session = None

    def load(self):
        self.session = ort.InferenceSession(self.model_path)
        return self

    def detect_faces(self, image: np.ndarray) -> List[Tuple[int, int, int, int]]:
        """Return a list of (x1, y1, x2, y2) bounding boxes for detected faces."""
        if self.session is None:
            raise RuntimeError("Call load() before detect_faces().")
        # TODO: preprocess, run inference, postprocess to bboxes
        return []
