"""Tests for Milestone 2B face detection, unified regions, and visual metadata."""

from __future__ import annotations

import numpy as np
import pytest

from agent.perception import Perception
from server.app import create_app
from vision.face_detection import (
    FaceBox,
    FaceDetector,
    HaarCascadeFaceDetector,
    NullFaceDetector,
    create_face_detector,
)
from vision.privacy_pipeline import process_screenshot_privacy
from vision.redaction import (
    adaptive_face_blur_radius,
    expand_box_with_padding,
    redact_by_categories,
)
from vision.unified_regions import unify_sensitive_regions
from vision.visual_metadata import (
    assert_visual_context_is_safe,
    build_visual_context,
    visual_context_from_privacy_report,
)


class _StubFaceDetector(FaceDetector):
    """Deterministic detector for pipeline tests (no real vision needed)."""

    def __init__(self, boxes, min_confidence=0.6):
        super().__init__(min_confidence=min_confidence)
        self._boxes = boxes

    def load(self):
        self._loaded = True
        return self

    def _detect_raw(self, image):
        return list(self._boxes)


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


# --- Face detection interface ---


def test_detector_returns_structured_bounding_boxes():
    detector = _StubFaceDetector(
        [FaceBox(x=100, y=200, width=150, height=150, confidence=0.92)]
    ).load()
    image = np.zeros((400, 400, 3), dtype=np.uint8)
    faces = detector.detect(image)
    assert len(faces) == 1
    face = faces[0]
    assert face["x"] == 100
    assert face["y"] == 200
    assert face["width"] == 150
    assert face["height"] == 150
    assert face["confidence"] == pytest.approx(0.92)
    assert face["category"] == "face"
    assert face["source"] == "vision"
    # No identity / biometric fields
    assert "embedding" not in face
    assert "name" not in face
    assert "crop" not in face


def test_invalid_image_handled_safely():
    detector = create_face_detector(backend="haar", min_confidence=0.6)
    assert detector.detect(None) == []
    assert detector.detect(np.array([])) == []
    assert detector.detect(np.zeros((10,), dtype=np.uint8)) == []


def test_confidence_threshold_applied():
    detector = _StubFaceDetector(
        [
            FaceBox(10, 10, 40, 40, confidence=0.9),
            FaceBox(60, 60, 40, 40, confidence=0.4),
        ],
        min_confidence=0.6,
    ).load()
    faces = detector.detect(np.zeros((120, 120, 3), dtype=np.uint8))
    assert len(faces) == 1
    assert faces[0]["confidence"] == pytest.approx(0.9)


def test_null_detector_returns_empty():
    detector = NullFaceDetector().load()
    assert detector.detect(np.zeros((50, 50, 3), dtype=np.uint8)) == []


def test_haar_detector_on_sample_face_when_available():
    import os

    path = os.path.join("demo", "assets", "sample_face.png")
    if not os.path.exists(path):
        pytest.skip("sample face asset missing")
    from PIL import Image

    image = np.asarray(Image.open(path).convert("RGB"))
    detector = HaarCascadeFaceDetector(min_confidence=0.5).load()
    faces = detector.detect(image)
    assert isinstance(faces, list)
    if faces:
        f = faces[0]
        assert f["width"] > 0 and f["height"] > 0
        assert "embedding" not in f


# --- Unified privacy regions ---


def test_dom_and_face_boxes_merge():
    unified = unify_sensitive_regions(
        dom_boxes=[
            {"category": "email", "x": 100, "y": 200, "width": 250, "height": 30},
            {"category": "password", "x": 100, "y": 240, "width": 250, "height": 30},
        ],
        face_boxes=[
            {"x": 500, "y": 100, "width": 180, "height": 180, "confidence": 0.92},
        ],
    )
    regions = unified["regions"]
    cats = {r["category"] for r in regions}
    assert cats == {"email", "password", "face"}
    face = next(r for r in regions if r["category"] == "face")
    assert face["source"] == "vision"
    assert face["confidence"] == pytest.approx(0.92)
    email = next(r for r in regions if r["category"] == "email")
    assert email["source"] == "dom"
    assert unified["counts"]["total"] == 3


def test_overlapping_same_category_merged():
    unified = unify_sensitive_regions(
        face_boxes=[
            {"x": 10, "y": 10, "width": 50, "height": 50, "confidence": 0.7},
            {"x": 30, "y": 30, "width": 50, "height": 50, "confidence": 0.9},
        ],
        merge_overlaps=True,
    )
    faces = [r for r in unified["regions"] if r["category"] == "face"]
    assert len(faces) == 1
    assert faces[0]["x"] == 10
    assert faces[0]["y"] == 10
    assert faces[0]["width"] == 70
    assert faces[0]["height"] == 70
    assert faces[0]["confidence"] == pytest.approx(0.9)


def test_overlapping_different_categories_kept():
    unified = unify_sensitive_regions(
        dom_boxes=[{"category": "email", "x": 10, "y": 10, "width": 40, "height": 40}],
        face_boxes=[{"x": 20, "y": 20, "width": 40, "height": 40, "confidence": 0.8}],
    )
    assert len(unified["regions"]) == 2


# --- Face redaction ---


def test_face_regions_receive_blur_strategy():
    image = np.zeros((200, 200, 3), dtype=np.uint8)
    image[:, :] = 255
    # High-contrast strip inside face box so blur changes pixels
    image[40:120, 40:120] = 0
    image[40:80, 40:120] = 255
    regions = [
        {
            "category": "face",
            "source": "vision",
            "x": 40,
            "y": 40,
            "width": 80,
            "height": 80,
            "confidence": 0.9,
        }
    ]
    redacted, report = redact_by_categories(image, regions)
    assert report["vision"]["faces_redacted"] == 1
    assert report["redactions"][0]["strategy"] == "blur"
    # Blur should change the face region
    assert not np.array_equal(redacted[50:110, 50:110], image[50:110, 50:110])


def test_face_padding_applied():
    padded = expand_box_with_padding(
        {"x": 100, "y": 100, "width": 100, "height": 100},
        padding_percent=10,
        image_width=400,
        image_height=400,
    )
    assert padded["x"] == 90
    assert padded["y"] == 90
    assert padded["width"] == 120
    assert padded["height"] == 120


def test_adaptive_blur_scales_with_face_size():
    small = adaptive_face_blur_radius(30, 30)
    large = adaptive_face_blur_radius(200, 200)
    assert small < large


def test_non_face_areas_remain_unchanged():
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    image[:] = 128
    image[10:40, 10:40] = 255  # face region contrast
    regions = [
        {
            "category": "face",
            "x": 10,
            "y": 10,
            "width": 30,
            "height": 30,
            "confidence": 1.0,
        }
    ]
    redacted, _ = redact_by_categories(
        image, regions, face_config={"padding_percent": 0}
    )
    # Corner far from face should be unchanged
    assert np.array_equal(redacted[90, 90], image[90, 90])


# --- Visual metadata ---


def test_visual_metadata_generated_without_raw_image():
    ctx = build_visual_context(
        screenshot_available=True,
        width=1920,
        height=1080,
        faces_detected=2,
        faces_redacted=2,
        sensitive_regions=5,
        redactions_applied=5,
        privacy_safe=True,
    )
    assert ctx["screenshot_available"] is True
    assert ctx["faces_detected"] == 2
    assert ctx["privacy_safe"] is True
    assert "image" not in ctx
    assert "embedding" not in ctx
    assert assert_visual_context_is_safe(ctx)


def test_visual_context_from_report():
    report = {
        "screenshot_width": 800,
        "screenshot_height": 600,
        "total_redactions": 4,
        "categories": {"email": 1, "face": 2, "password": 1},
        "vision": {"faces_detected": 2, "faces_redacted": 2},
    }
    ctx = visual_context_from_privacy_report(report)
    assert ctx["faces_detected"] == 2
    assert ctx["sensitive_regions"] == 4
    assert ctx["dimensions"]["width"] == 800


def test_unsafe_visual_context_keys_rejected():
    assert not assert_visual_context_is_safe({"image": "data:..."})
    assert not assert_visual_context_is_safe({"embedding": [0.1, 0.2]})


# --- Privacy pipeline ---


def test_privacy_pipeline_unifies_and_redacts():
    image = np.ones((120, 120, 3), dtype=np.uint8) * 200
    image[20:60, 20:60] = 40
    detector = _StubFaceDetector(
        [FaceBox(20, 20, 40, 40, confidence=0.95)]
    ).load()
    result = process_screenshot_privacy(
        image,
        dom_boxes=[{"category": "email", "x": 70, "y": 10, "width": 40, "height": 12}],
        detector=detector,
    )
    assert result["ok"] is True
    assert result["sanitized_data_url"].startswith("data:image/png;base64,")
    assert result["privacy_report"]["vision"]["faces_detected"] == 1
    assert result["privacy_report"]["total_redactions"] == 2
    assert result["visual_context"]["privacy_safe"] is True
    assert "face_detection_ms" in result["timing"]
    assert assert_visual_context_is_safe(result["visual_context"])


# --- Perception ---


def test_perception_accepts_visual_context():
    perception = Perception()
    page = {
        "url": "https://example.com",
        "title": "Example",
        "visibleText": "Hello",
        "elements": [{"tag": "button", "text": "Go", "selector": "#go", "sensitive": False}],
    }
    visual = build_visual_context(
        screenshot_available=True,
        width=100,
        height=80,
        faces_detected=1,
        sensitive_regions=2,
        redactions_applied=2,
        privacy_safe=True,
    )
    obs = perception.observe(page, "Hello", visual_context=visual)
    assert obs["visual_context"]["faces_detected"] == 1
    assert obs["privacy_safe"] is True
    assert obs["screenshot"] is None
    assert len(obs["ui_elements"]) == 1


def test_perception_dom_only_still_works():
    perception = Perception()
    page = {
        "url": "https://example.com",
        "title": "Example",
        "visibleText": "Hello",
        "elements": [],
    }
    obs = perception.observe(page, "Hello")
    assert obs["visual_context"] is None
    assert obs["privacy_safe"] is True
    assert obs["sanitized_text"] == "Hello"


def test_perception_visual_context_none_works():
    perception = Perception()
    obs = perception.observe({"url": "", "title": "", "visibleText": "", "elements": []}, "")
    assert obs["visual_context"] is None


def test_perception_strips_unsafe_visual_payload():
    perception = Perception()
    obs = perception.observe(
        {"url": "", "title": "", "visibleText": "x", "elements": []},
        "x",
        visual_context={"image": "data:raw", "faces_detected": 1},
    )
    assert obs["visual_context"]["privacy_safe"] is False
    assert "image" not in obs["visual_context"]


# --- API ---


def test_agent_step_accepts_visual_context(client):
    payload = {
        "task": "Click the Submit button",
        "page": {
            "url": "https://example.com",
            "title": "Example",
            "visibleText": "Submit",
            "elements": [
                {
                    "tag": "button",
                    "text": "Submit",
                    "selector": "#submit-button",
                    "sensitive": False,
                }
            ],
        },
        "privacy_report": {"total_redactions": 3},
        "visual_context": {
            "screenshot_available": True,
            "faces_detected": 1,
            "sensitive_regions": 3,
            "redactions_applied": 3,
            "privacy_safe": True,
            "dimensions": {"width": 800, "height": 600},
        },
    }
    res = client.post("/api/agent/step", json=payload)
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "success"
    assert data["visual_context"]["faces_detected"] == 1
    assert data["observation"]["visual_context"]["privacy_safe"] is True


def test_detect_faces_endpoint_returns_boxes_only(client):
    # Tiny blank image as PNG data URL
    from vision.privacy_pipeline import encode_png_data_url

    img = np.zeros((64, 64, 3), dtype=np.uint8)
    data_url = encode_png_data_url(img)
    res = client.post(
        "/api/privacy/detect-faces",
        json={"image": data_url, "minConfidence": 0.6},
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert "faces" in data
    blob = str(data)
    assert "embedding" not in blob
    assert "crop" not in blob


def test_sanitize_screenshot_endpoint(client):
    from vision.privacy_pipeline import encode_png_data_url

    img = np.ones((80, 80, 3), dtype=np.uint8) * 180
    res = client.post(
        "/api/privacy/sanitize-screenshot",
        json={
            "image": encode_png_data_url(img),
            "dom_boxes": [
                {"category": "email", "x": 5, "y": 5, "width": 30, "height": 10}
            ],
            "faceDetection": True,
        },
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert data["sanitized_data_url"].startswith("data:image/png")
    assert data["visual_context"]["privacy_safe"] is True
    assert "raw" not in str(data).lower() or "raw" not in data


def test_screenshot_post_stores_visual_context(client):
    report = {
        "total_redactions": 2,
        "categories": {"face": 1, "email": 1},
        "vision": {"faces_detected": 1, "faces_redacted": 1},
        "screenshot_width": 100,
        "screenshot_height": 80,
        "timing": {"face_detection_ms": 12},
    }
    vc = {
        "screenshot_available": True,
        "faces_detected": 1,
        "privacy_safe": True,
        "sensitive_regions": 2,
        "redactions_applied": 2,
        "dimensions": {"width": 100, "height": 80},
    }
    res = client.post(
        "/api/agent/screenshot",
        json={
            "image": "data:image/png;base64,SANITIZED",
            "privacy_mode": True,
            "sanitized": True,
            "screenshot_privacy_report": report,
            "visual_context": vc,
        },
    )
    assert res.status_code == 200
    got = client.get("/api/agent/screenshot").get_json()
    assert got["visual_context"]["faces_detected"] == 1
    assert got["screenshot_privacy_report"]["vision"]["faces_detected"] == 1
