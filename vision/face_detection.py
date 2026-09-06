"""On-device face detection for screenshot privacy redaction.

PRIVACY RULES:
  - Output is bounding boxes only (geometry + confidence).
  - Never return identity, names, embeddings, biometrics, or face crops.
  - Detection must run locally before any sanitized screenshot leaves the device.

Architecture:
  FaceDetector (interface)
    ├── HaarCascadeFaceDetector  (OpenCV cascade — default for Milestone 2B)
    ├── OnnxFaceDetector         (stub for future ONNX / WebGPU models)
    └── NullFaceDetector         (safe no-op when models unavailable)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

import numpy as np

ImageLike = Union[np.ndarray, None]


@dataclass
class FaceBox:
    """Structured face bounding box — no identity / biometric payload."""

    x: int
    y: int
    width: int
    height: int
    confidence: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "x": int(self.x),
            "y": int(self.y),
            "width": int(self.width),
            "height": int(self.height),
            "confidence": float(self.confidence),
            "category": "face",
            "source": "vision",
        }


class FaceDetector(ABC):
    """Abstract face detector — swap implementations without changing callers."""

    def __init__(
        self,
        min_confidence: float = 0.6,
        scale_factor: float = 1.1,
        min_neighbors: int = 5,
    ):
        self.min_confidence = float(min_confidence)
        self.scale_factor = float(scale_factor)
        self.min_neighbors = int(min_neighbors)
        self._loaded = False

    @abstractmethod
    def load(self) -> "FaceDetector":
        """Load model weights / cascade. Idempotent."""

    @abstractmethod
    def _detect_raw(self, image: np.ndarray) -> List[FaceBox]:
        """Backend-specific detection. Image is RGB or BGR HxWxC uint8."""

    def detect(self, image: ImageLike) -> List[Dict[str, Any]]:
        """Detect faces and return structured box dicts (no crops/embeddings)."""
        boxes = self.detect_faces(image)
        return [b.to_dict() for b in boxes]

    def detect_faces(self, image: ImageLike) -> List[FaceBox]:
        """Return FaceBox list filtered by confidence threshold."""
        if image is None:
            return []
        if not isinstance(image, np.ndarray) or image.size == 0:
            return []
        if image.ndim not in (2, 3):
            return []

        if not self._loaded:
            try:
                self.load()
            except Exception:
                return []

        try:
            raw = self._detect_raw(image)
        except Exception:
            return []

        return [b for b in raw if b.confidence >= self.min_confidence]


class NullFaceDetector(FaceDetector):
    """Safe no-op detector used when cascades/models are unavailable."""

    def load(self) -> "NullFaceDetector":
        self._loaded = True
        return self

    def _detect_raw(self, image: np.ndarray) -> List[FaceBox]:
        return []


class HaarCascadeFaceDetector(FaceDetector):
    """OpenCV Haar Cascade fallback — lightweight, local, no embeddings."""

    def __init__(
        self,
        cascade_path: Optional[str] = None,
        min_confidence: float = 0.6,
        scale_factor: float = 1.1,
        min_neighbors: int = 5,
        min_size: Sequence[int] = (30, 30),
    ):
        super().__init__(
            min_confidence=min_confidence,
            scale_factor=scale_factor,
            min_neighbors=min_neighbors,
        )
        self.cascade_path = cascade_path
        self.min_size = (int(min_size[0]), int(min_size[1]))
        self._cascade = None

    def load(self) -> "HaarCascadeFaceDetector":
        import cv2

        path = self.cascade_path
        if not path:
            path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        self._cascade = cv2.CascadeClassifier(path)
        if self._cascade.empty():
            raise RuntimeError(f"Failed to load Haar cascade from {path}")
        self.cascade_path = path
        self._loaded = True
        return self

    def _detect_raw(self, image: np.ndarray) -> List[FaceBox]:
        if self._cascade is None:
            self.load()

        gray = _to_gray(image)
        detections = None
        weights_list: List[float] = []

        # Prefer detectMultiScale3 for soft confidence from cascade weights.
        try:
            detections, _reject_levels, weights = self._cascade.detectMultiScale3(
                gray,
                scaleFactor=self.scale_factor,
                minNeighbors=self.min_neighbors,
                minSize=self.min_size,
                outputRejectLevels=True,
            )
            if weights is not None:
                weights_list = [float(w) for w in weights]
        except Exception:
            detections = self._cascade.detectMultiScale(
                gray,
                scaleFactor=self.scale_factor,
                minNeighbors=self.min_neighbors,
                minSize=self.min_size,
            )

        faces: List[FaceBox] = []
        if detections is None or len(detections) == 0:
            return faces

        for i, (x, y, w, h) in enumerate(detections):
            weight = (
                weights_list[i]
                if i < len(weights_list)
                else float(self.min_neighbors)
            )
            # Normalize cascade weight into a soft confidence score in [0, 1].
            confidence = min(
                1.0, max(0.0, weight / max(self.min_neighbors * 2.0, 1.0))
            )
            # Detections that passed minNeighbors should clear the default threshold.
            if confidence < self.min_confidence:
                confidence = self.min_confidence
            faces.append(
                FaceBox(
                    x=int(x),
                    y=int(y),
                    width=int(w),
                    height=int(h),
                    confidence=float(confidence),
                )
            )
        return faces


class OnnxFaceDetector(FaceDetector):
    """Placeholder for a future ONNX / WebGPU face detector.

    Kept as a stub so the rest of the pipeline never couples to Haar Cascade.
    """

    def __init__(
        self,
        model_path: str = "models/face_detection/face_model.onnx",
        min_confidence: float = 0.6,
    ):
        super().__init__(min_confidence=min_confidence)
        self.model_path = model_path
        self.session = None

    def load(self) -> "OnnxFaceDetector":
        # Deferred: requires an actual ONNX face model on disk.
        # Intentionally does not import onnxruntime until a model is present.
        raise FileNotFoundError(
            f"ONNX face model not available at {self.model_path}. "
            "Use HaarCascadeFaceDetector for Milestone 2B."
        )

    def _detect_raw(self, image: np.ndarray) -> List[FaceBox]:
        if self.session is None:
            raise RuntimeError("Call load() before detect().")
        return []


def create_face_detector(
    backend: str = "haar",
    min_confidence: float = 0.6,
    **kwargs: Any,
) -> FaceDetector:
    """Factory: 'haar' | 'onnx' | 'null'."""
    backend = (backend or "haar").lower().strip()
    if backend == "null":
        return NullFaceDetector(min_confidence=min_confidence).load()
    if backend == "onnx":
        return OnnxFaceDetector(min_confidence=min_confidence, **kwargs)
    try:
        return HaarCascadeFaceDetector(min_confidence=min_confidence, **kwargs).load()
    except Exception:
        return NullFaceDetector(min_confidence=min_confidence).load()


def _to_gray(image: np.ndarray) -> np.ndarray:
    import cv2

    if image.ndim == 2:
        return image
    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_RGBA2GRAY)
    # Assume RGB from PIL / pipeline; also accept BGR.
    try:
        return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    except Exception:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


# Backward-compatible alias used by older docs / imports
def detect_faces(
    image: ImageLike,
    min_confidence: float = 0.6,
) -> List[Dict[str, Any]]:
    """Convenience wrapper around the default Haar detector."""
    detector = create_face_detector(backend="haar", min_confidence=min_confidence)
    return detector.detect(image)
