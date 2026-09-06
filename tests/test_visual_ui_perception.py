"""Milestone 3A — Hybrid Visual UI Perception tests."""

from __future__ import annotations

import numpy as np
import pytest

from agent.perception import Perception
from server.app import create_app
from vision.layout_analyzer import LayoutAnalyzer
from vision.ui_detector import ContourBasedUIDetector, DOMGeometryDetector
from vision.visual_element import Box, VisualElement, box_from_mapping, iou
from vision.visual_fusion import VisualFusionEngine
from vision.visual_metadata_validator import (
    enforce_privacy_on_map,
    validate_visual_ui_map,
)
from vision.visual_perception_pipeline import build_visual_ui_map_from_inputs
from vision.visual_ui_map import build_visual_ui_map


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def test_valid_boxes():
    box = box_from_mapping({"x": 10, "y": 20, "width": 100, "height": 40})
    assert box is not None
    assert box.is_valid()
    assert box.to_dict()["width"] == 100


def test_invalid_boxes_rejected():
    assert box_from_mapping({"x": 0, "y": 0, "width": 0, "height": 10}) is None
    assert box_from_mapping({"x": 0, "y": 0, "width": -5, "height": 10}) is None
    assert box_from_mapping({"x": "bad", "y": 0, "width": 5, "height": 5}) is None


def test_overlapping_boxes_iou():
    a = Box(0, 0, 100, 100)
    b = Box(50, 50, 100, 100)
    assert 0.14 < iou(a, b) < 0.15
    c = Box(200, 200, 10, 10)
    assert iou(a, c) == 0.0


def test_coordinates_remain_valid_after_dict_roundtrip():
    el = VisualElement(
        id="ui_001",
        type="button",
        box=Box(820, 600, 180, 50),
        interactive=True,
        source="dom",
        confidence=1.0,
        text="Submit",
    )
    d = el.to_dict()
    assert d["box"] == {"x": 820, "y": 600, "width": 180, "height": 50}
    restored = VisualElement.from_dict(d)
    assert restored is not None
    assert restored.box.width == 180


# ---------------------------------------------------------------------------
# Visual detection
# ---------------------------------------------------------------------------


def _rect_image(w=400, h=300):
    """Synthetic UI-like image with clear rectangular controls."""
    img = np.full((h, w, 3), 240, dtype=np.uint8)
    # Button-like dark rectangle
    img[200:250, 250:380] = 40
    # Input-like horizontal bar
    img[80:110, 40:300] = 255
    img[80:110, 40:300] = (img[80:110, 40:300] * 0 + 220).astype(np.uint8)
    # Draw borders
    img[79:81, 40:300] = 0
    img[109:111, 40:300] = 0
    img[80:110, 39:41] = 0
    img[80:110, 299:301] = 0
    img[199:201, 250:380] = 0
    img[249:251, 250:380] = 0
    img[200:250, 249:251] = 0
    img[200:250, 379:381] = 0
    return img


def test_contour_detector_finds_rectangular_regions():
    detector = ContourBasedUIDetector(min_area=200)
    els = detector.detect(_rect_image())
    assert len(els) >= 1
    assert all(e.source == "vision" for e in els)
    assert all(0.0 < e.confidence <= 0.7 for e in els)


def test_tiny_noisy_regions_filtered():
    img = np.random.randint(0, 255, (100, 100, 3), dtype=np.uint8)
    # Sprinkle tiny dots
    img[10:12, 10:12] = 0
    detector = ContourBasedUIDetector(min_area=400)
    els = detector.detect(img)
    # Should not explode with noise; tiny regions filtered
    assert all(e.box.area >= 400 for e in els)


def test_invalid_image_handled():
    detector = ContourBasedUIDetector()
    assert detector.detect(None) == []
    assert detector.detect(np.array([])) == []


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def _els_for_layout():
    return [
        VisualElement("a", "input", Box(10, 10, 200, 30), True, "dom", 1.0),
        VisualElement("b", "input", Box(10, 50, 200, 30), True, "dom", 1.0),
        VisualElement("c", "button", Box(10, 100, 80, 30), True, "dom", 1.0),
        VisualElement("d", "button", Box(220, 10, 80, 30), True, "dom", 1.0),
        VisualElement("container", "container", Box(0, 0, 320, 150), False, "vision", 0.5),
    ]


def test_layout_horizontal_alignment():
    layout = LayoutAnalyzer().analyze(_els_for_layout())
    horiz = layout["alignments"]["horizontal"]
    assert any(set(p["elements"]) == {"a", "d"} for p in horiz)


def test_layout_vertical_alignment():
    layout = LayoutAnalyzer().analyze(_els_for_layout())
    vert = layout["alignments"]["vertical"]
    assert any("a" in p["elements"] and "b" in p["elements"] for p in vert)


def test_layout_containment():
    layout = LayoutAnalyzer().analyze(_els_for_layout())
    parents = {p["parent"] for p in layout["containment"]}
    assert "container" in parents


def test_layout_grouping():
    layout = LayoutAnalyzer().analyze(_els_for_layout())
    assert len(layout["groups"]) >= 1


# ---------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------


def test_fusion_high_iou_merges():
    dom = [
        VisualElement(
            "dom_0",
            "button",
            Box(820, 600, 180, 50),
            True,
            "dom",
            1.0,
            text="Submit",
            selector="#submit",
        )
    ]
    vision = [
        VisualElement(
            "vision_0", "unknown", Box(822, 598, 178, 52), False, "vision", 0.68
        )
    ]
    fused = VisualFusionEngine(iou_threshold=0.5).fuse(dom, vision)
    assert len(fused) == 1
    assert fused[0].source == "dom+vision"
    assert fused[0].type == "button"
    assert fused[0].text == "Submit"
    assert fused[0].confidence > 0.8


def test_fusion_low_iou_remain_separate():
    dom = [VisualElement("d", "button", Box(0, 0, 50, 20), True, "dom", 1.0)]
    vision = [VisualElement("v", "unknown", Box(400, 400, 50, 20), False, "vision", 0.5)]
    fused = VisualFusionEngine(iou_threshold=0.5).fuse(dom, vision)
    assert len(fused) == 2
    sources = {e.source for e in fused}
    assert sources == {"dom", "vision"}


def test_fusion_dom_only_preserved():
    dom = [VisualElement("d", "input", Box(10, 10, 100, 30), True, "dom", 1.0)]
    fused = VisualFusionEngine().fuse(dom, [])
    assert len(fused) == 1
    assert fused[0].source == "dom"


def test_fusion_vision_only_preserved():
    vision = [VisualElement("v", "unknown", Box(10, 10, 100, 30), False, "vision", 0.4)]
    fused = VisualFusionEngine().fuse([], vision)
    assert len(fused) == 1
    assert fused[0].source == "vision"


def test_fusion_confidence_calculated():
    dom = [VisualElement("d", "button", Box(0, 0, 100, 40), True, "dom", 1.0)]
    vision = [VisualElement("v", "button", Box(0, 0, 100, 40), True, "vision", 0.6)]
    fused = VisualFusionEngine().fuse(dom, vision)
    assert len(fused) == 1
    assert 0.7 <= fused[0].confidence <= 0.98


# ---------------------------------------------------------------------------
# Privacy
# ---------------------------------------------------------------------------


def test_sensitive_text_becomes_redacted():
    els = DOMGeometryDetector().from_dom_elements(
        [
            {
                "tag": "input",
                "type": "email",
                "text": "user@example.com",
                "box": {"x": 10, "y": 10, "width": 200, "height": 30},
                "sensitive": True,
            }
        ]
    )
    assert els[0].text == "[REDACTED]"
    assert els[0].sensitive is True


def test_password_never_appears():
    els = DOMGeometryDetector().from_dom_elements(
        [
            {
                "tag": "input",
                "type": "password",
                "value": "SuperSecret123!",
                "text": "SuperSecret123!",
                "box": {"x": 10, "y": 50, "width": 200, "height": 30},
                "sensitive": True,
            }
        ]
    )
    blob = str(els[0].to_dict())
    assert "SuperSecret123!" not in blob
    assert els[0].text == "[REDACTED]"


def test_email_phone_never_in_safe_map():
    ui_map = build_visual_ui_map_from_inputs(
        image=_rect_image(),
        dom_elements=[
            {
                "tag": "input",
                "type": "email",
                "text": "leak@evil.com",
                "box": {"x": 40, "y": 80, "width": 260, "height": 30},
                "sensitive": True,
            },
            {
                "tag": "input",
                "type": "tel",
                "text": "555-123-4567",
                "box": {"x": 40, "y": 120, "width": 260, "height": 30},
                "sensitive": True,
            },
        ],
    )
    blob = str(ui_map)
    assert "leak@evil.com" not in blob
    assert "555-123-4567" not in blob
    assert ui_map["privacy_safe"] is True


def test_raw_screenshot_base64_rejected():
    bad = {
        "elements": [],
        "privacy_safe": True,
        "image": "data:image/png;base64," + ("A" * 300),
    }
    ok, reasons = validate_visual_ui_map(bad)
    assert ok is False
    assert any("forbidden" in r or "raw_image" in r or "base64" in r for r in reasons)


def test_privacy_safe_validation_enforced():
    bad = {
        "elements": [
            {
                "id": "ui_0",
                "type": "input",
                "box": {"x": 1, "y": 1, "width": 10, "height": 10},
                "interactive": True,
                "source": "dom",
                "confidence": 1.0,
                "text": "user@example.com",
                "sensitive": True,
            }
        ],
        "privacy_safe": True,
        "summary": {},
        "layout": {"groups": []},
    }
    enforced = enforce_privacy_on_map(bad)
    assert enforced["elements"][0]["text"] == "[REDACTED]"
    ok, _ = validate_visual_ui_map(enforced)
    assert ok is True


def test_embeddings_rejected():
    bad = {"elements": [], "privacy_safe": True, "embedding": [0.1, 0.2]}
    ok, reasons = validate_visual_ui_map(bad)
    assert ok is False


# ---------------------------------------------------------------------------
# Perception
# ---------------------------------------------------------------------------


def test_perception_accepts_visual_ui_map():
    ui_map = build_visual_ui_map(
        elements=[
            VisualElement(
                "ui_001",
                "button",
                Box(10, 10, 80, 30),
                True,
                "dom+vision",
                0.96,
                text="Submit",
            )
        ],
        width=400,
        height=300,
        privacy_safe=True,
    )
    obs = Perception().observe(
        {
            "url": "https://example.com",
            "title": "T",
            "visibleText": "Submit",
            "elements": [],
        },
        visual_ui_map=ui_map,
    )
    assert obs["visual_ui_map"] is not None
    assert obs["visual_ui_map"]["privacy_safe"] is True


def test_perception_visual_ui_map_none_works():
    obs = Perception().observe(
        {
            "url": "https://example.com",
            "title": "T",
            "visibleText": "Hello",
            "elements": [{"tag": "button", "text": "Go", "selector": "#go"}],
        },
        visual_ui_map=None,
    )
    assert obs["visual_ui_map"] is None
    assert len(obs["ui_elements"]) == 1


def test_perception_dom_only_flow_still_works():
    obs = Perception().observe(
        {
            "url": "https://example.com",
            "title": "T",
            "visibleText": "Click Submit",
            "elements": [
                {"tag": "button", "text": "Submit", "selector": "#submit", "sensitive": False}
            ],
        }
    )
    assert obs["visual_context"] is None
    assert obs["visual_ui_map"] is None
    assert obs["ui_elements"][0]["selector"] == "#submit"


def test_perception_drops_unsafe_visual_ui_map():
    obs = Perception().observe(
        {"url": "u", "title": "t", "visibleText": "", "elements": []},
        visual_ui_map={
            "privacy_safe": True,
            "elements": [],
            "raw_image": "data:image/png;base64,AAA",
        },
    )
    assert obs["visual_ui_map"] is None


# ---------------------------------------------------------------------------
# Pipeline + DOM geometry
# ---------------------------------------------------------------------------


def test_dom_geometry_detector():
    els = DOMGeometryDetector().from_dom_elements(
        [
            {
                "tag": "button",
                "text": "Submit",
                "role": "button",
                "selector": "#submit",
                "box": {"x": 820, "y": 600, "width": 180, "height": 50},
                "interactive": True,
            }
        ]
    )
    assert len(els) == 1
    assert els[0].type == "button"
    assert els[0].confidence == 1.0
    assert els[0].source == "dom"


def test_pipeline_produces_map_with_performance():
    ui_map = build_visual_ui_map_from_inputs(
        image=_rect_image(),
        dom_elements=[
            {
                "tag": "button",
                "text": "Submit",
                "box": {"x": 250, "y": 200, "width": 130, "height": 50},
                "selector": "#submit",
            },
            {
                "tag": "input",
                "type": "search",
                "placeholder": "Search",
                "box": {"x": 40, "y": 80, "width": 260, "height": 30},
            },
        ],
    )
    assert "elements" in ui_map
    assert "summary" in ui_map
    assert "performance" in ui_map
    perf = ui_map["performance"]
    for key in (
        "dom_geometry_ms",
        "vision_detection_ms",
        "layout_analysis_ms",
        "fusion_ms",
        "privacy_validation_ms",
        "total_visual_perception_ms",
    ):
        assert key in perf
        assert isinstance(perf[key], (int, float))
        assert perf[key] >= 0
    assert ui_map["privacy_safe"] is True


def test_pipeline_marks_sensitive_overlap():
    ui_map = build_visual_ui_map_from_inputs(
        image=_rect_image(),
        dom_elements=[
            {
                "tag": "input",
                "type": "text",
                "text": "ok",
                "box": {"x": 40, "y": 80, "width": 260, "height": 30},
            }
        ],
        sensitive_regions=[
            {"category": "email", "x": 40, "y": 80, "width": 260, "height": 30}
        ],
    )
    matched = [e for e in ui_map["elements"] if e.get("sensitive")]
    assert len(matched) >= 1
    assert all(e.get("text") == "[REDACTED]" for e in matched)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_api_safe_visual_ui_map_accepted(client):
    ui_map = build_visual_ui_map(
        elements=[
            VisualElement(
                "ui_001", "button", Box(10, 10, 80, 30), True, "dom+vision", 0.9, text="Go"
            )
        ],
        width=100,
        height=100,
        privacy_safe=True,
    )
    res = client.post(
        "/api/agent/step",
        json={
            "task": "Click Go",
            "page": {
                "url": "https://example.com",
                "title": "Ex",
                "visibleText": "Go",
                "elements": [
                    {"tag": "button", "text": "Go", "selector": "#go", "sensitive": False}
                ],
            },
            "visual_ui_map": ui_map,
        },
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["visual_ui_map"] is not None
    assert data["visual_ui_map"]["privacy_safe"] is True


def test_api_unsafe_visual_map_ignored(client):
    res = client.post(
        "/api/agent/step",
        json={
            "task": "Click Go",
            "page": {
                "url": "https://example.com",
                "title": "Ex",
                "visibleText": "Go",
                "elements": [
                    {"tag": "button", "text": "Go", "selector": "#go", "sensitive": False}
                ],
            },
            "visual_ui_map": {
                "privacy_safe": True,
                "elements": [],
                "embedding": [1, 2, 3],
            },
        },
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data.get("visual_ui_map") is None


def test_api_raw_image_fields_rejected_on_step(client):
    res = client.post(
        "/api/agent/step",
        json={
            "task": "x",
            "page": {"url": "u", "title": "t", "visibleText": "", "elements": []},
            "visual_ui_map": {"privacy_safe": True, "elements": []},
            "raw_image": "data:image/png;base64,AAA",
        },
    )
    assert res.status_code == 400
    assert res.get_json()["error"] == "raw_screenshot_blocked"


def test_api_ui_map_endpoint(client):
    # Tiny PNG as data URL via numpy encode
    from vision.privacy_pipeline import encode_png_data_url

    img = encode_png_data_url(_rect_image())
    res = client.post(
        "/api/vision/ui-map",
        json={
            "image": img,
            "sanitized": True,
            "dom_elements": [
                {
                    "tag": "button",
                    "text": "Submit",
                    "box": {"x": 250, "y": 200, "width": 130, "height": 50},
                }
            ],
        },
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["privacy_safe"] is True
    assert data["visual_ui_map"] is not None
    assert "performance" in data


def test_api_ui_map_rejects_raw(client):
    res = client.post(
        "/api/vision/ui-map",
        json={"image": "x", "sanitized": False, "privacy_mode": True, "dom_elements": []},
    )
    assert res.status_code == 400
