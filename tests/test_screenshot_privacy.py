"""Tests for Milestone 2A vision / screenshot privacy pipeline."""

import numpy as np
import pytest

from privacy.sensitive_boxes import collect_sensitive_boxes, field_looks_sensitive
from server.app import create_app
from vision.coordinate_mapping import compute_scale, map_box_to_screenshot
from vision.redaction import (
    redact_by_categories,
    redact_regions,
    select_redaction_strategy,
)


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


# --- Bounding boxes ---


def test_sensitive_bounding_box_generation():
    elements = [
        {
            "type": "email",
            "name": "email",
            "id": "email",
            "x": 120,
            "y": 300,
            "width": 250,
            "height": 30,
            "value": "should-not-appear@example.com",
        },
        {
            "type": "password",
            "name": "password",
            "x": 120,
            "y": 340,
            "width": 250,
            "height": 30,
            "value": "secret123",
        },
        {
            "type": "text",
            "name": "credit_card",
            "autocomplete": "cc-number",
            "x": 10,
            "y": 10,
            "width": 200,
            "height": 24,
        },
        {
            "type": "text",
            "name": "q",
            "x": 10,
            "y": 50,
            "width": 200,
            "height": 24,
        },
        {
            "type": "tel",
            "name": "phone",
            "x": 0,
            "y": 0,
            "width": 0,
            "height": 0,  # invisible — skipped
        },
    ]
    boxes = collect_sensitive_boxes(elements)
    cats = {b["category"] for b in boxes}
    assert "email" in cats
    assert "password" in cats
    assert "credit_card" in cats
    assert len(boxes) == 3
    email_box = next(b for b in boxes if b["category"] == "email")
    assert email_box == {"category": "email", "x": 120, "y": 300, "width": 250, "height": 30}
    # Never leak raw values into box payloads
    blob = str(boxes)
    assert "secret123" not in blob
    assert "should-not-appear" not in blob


def test_field_classification_from_attrs():
    assert field_looks_sensitive({"type": "password"}) == "password"
    assert field_looks_sensitive({"type": "text", "name": "ssn"}) == "ssn"
    assert field_looks_sensitive({"type": "email"}) == "email"
    assert field_looks_sensitive({"type": "text", "placeholder": "phone"}) == "phone"
    assert field_looks_sensitive({"type": "text", "name": "q"}) is None


# --- Coordinate mapping ---


def test_coordinate_scale_dpr_2x():
    scale_x, scale_y = compute_scale(1200, 800, 2400, 1600)
    assert scale_x == 2.0
    assert scale_y == 2.0

    mapped = map_box_to_screenshot(
        {"x": 100, "y": 200, "width": 300, "height": 40},
        {"width": 1200, "height": 800},
        {"width": 2400, "height": 1600},
    )
    assert mapped == {"x": 200, "y": 400, "width": 600, "height": 80}


def test_coordinate_scale_non_uniform():
    mapped = map_box_to_screenshot(
        {"x": 10, "y": 20, "width": 30, "height": 40},
        {"width": 100, "height": 100},
        {"width": 200, "height": 300},
    )
    assert mapped["x"] == 20
    assert mapped["y"] == 60
    assert mapped["width"] == 60
    assert mapped["height"] == 120


# --- Redaction strategies ---


def test_redaction_strategy_selection():
    assert select_redaction_strategy("password") == "black_box"
    assert select_redaction_strategy("credit_card") == "black_box"
    assert select_redaction_strategy("ssn") == "black_box"
    assert select_redaction_strategy("email") == "blur"
    assert select_redaction_strategy("phone") == "blur"


def test_redact_regions_black_box():
    image = np.ones((10, 10, 3), dtype=np.uint8) * 255
    redacted = redact_regions(image, [(2, 2, 6, 6)], method="black_box")
    assert (redacted[2:6, 2:6] == 0).all()
    assert (redacted[0, 0] == 255).all()


def test_redact_regions_blur_softens_contrast():
    image = np.zeros((32, 32, 3), dtype=np.uint8)
    image[:, 16:] = 255
    redacted = redact_regions(image, [(0, 0, 32, 32)], method="blur")
    # Edge pixels should be mixed toward mid-gray after blur
    edge = int(redacted[16, 16, 0])
    assert 0 < edge < 255
    assert redacted.std() < image.std()


# --- Privacy report ---


def test_screenshot_privacy_report_generation():
    image = np.ones((1600, 2400, 3), dtype=np.uint8) * 180
    regions = [
        {"category": "email", "x": 200, "y": 400, "width": 600, "height": 80},
        {"category": "password", "x": 200, "y": 500, "width": 600, "height": 80},
        {"category": "phone", "x": 200, "y": 600, "width": 400, "height": 40},
    ]
    redacted, report = redact_by_categories(image, regions)

    assert report["screenshot_width"] == 2400
    assert report["screenshot_height"] == 1600
    assert report["total_redactions"] == 3
    assert "processing_time_ms" in report
    assert report["categories"]["email"] == 1
    assert report["categories"]["password"] == 1
    assert report["categories"]["phone"] == 1

    strategies = {r["strategy"] for r in report["redactions"]}
    assert "blur" in strategies
    assert "black_box" in strategies

    # No sensitive text in report
    assert "secret" not in str(report).lower()
    assert "@" not in str(report)

    # Password region blacked out
    assert (redacted[500:580, 200:800] == 0).all()


# --- Privacy boundary on server ---


def test_raw_screenshot_rejected_when_privacy_mode_on(client):
    res = client.post(
        "/api/agent/screenshot",
        json={
            "image": "data:image/png;base64,RAW",
            "privacy_mode": True,
            "sanitized": False,
        },
    )
    assert res.status_code == 400
    data = res.get_json()
    assert data["error"] == "raw_screenshot_blocked"


def test_raw_image_field_rejected(client):
    res = client.post(
        "/api/agent/screenshot",
        json={
            "image": "data:image/png;base64,SAFE",
            "privacy_mode": True,
            "sanitized": True,
            "raw_image": "data:image/png;base64,RAW",
        },
    )
    assert res.status_code == 400


def test_sanitized_screenshot_accepted(client):
    report = {
        "screenshot_width": 100,
        "screenshot_height": 80,
        "redactions": [
            {
                "category": "email",
                "strategy": "blur",
                "box": {"x": 1, "y": 2, "width": 3, "height": 4},
            }
        ],
        "total_redactions": 1,
        "processing_time_ms": 12,
        "categories": {"email": 1},
    }
    res = client.post(
        "/api/agent/screenshot",
        json={
            "image": "data:image/png;base64,SANITIZED",
            "privacy_mode": True,
            "sanitized": True,
            "screenshot_privacy_report": report,
        },
    )
    assert res.status_code == 200
    assert res.get_json()["sanitized"] is True

    got = client.get("/api/agent/screenshot").get_json()
    assert got["image"] == "data:image/png;base64,SANITIZED"
    assert got["sanitized"] is True
    assert got["screenshot_privacy_report"]["total_redactions"] == 1
    assert "secret" not in str(got)


def test_privacy_status_includes_screenshot_flag(client):
    client.post(
        "/api/agent/screenshot",
        json={
            "image": "data:image/png;base64,X",
            "sanitized": True,
            "privacy_mode": True,
            "screenshot_privacy_report": {"total_redactions": 2, "categories": {}},
        },
    )
    status = client.get("/api/privacy/status").get_json()
    assert status["screenshot_sanitized"] is True
    assert status["screenshot_privacy_report"]["total_redactions"] == 2
