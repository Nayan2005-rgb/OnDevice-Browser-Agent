"""Milestone 3B — visual action targeting: decision, planner, safety, API."""

import pytest

from agent.action_planner import ActionPlanner
from agent.decision_engine import DecisionEngine
from agent.target_resolver import TargetResolver
from server.app import create_app


SAMPLE_DOM = {
    "ui_elements": [
        {
            "tag": "button",
            "text": "Submit",
            "selector": "#submit-button",
            "type": None,
            "name": None,
            "id": "submit-button",
            "placeholder": None,
            "ariaLabel": None,
            "sensitive": False,
            "box": {"x": 20, "y": 20, "width": 100, "height": 40},
        },
        {
            "tag": "input",
            "text": "",
            "selector": "#search",
            "type": "text",
            "name": "q",
            "id": "search",
            "placeholder": "Search",
            "ariaLabel": "Search box",
            "sensitive": False,
        },
        {
            "tag": "input",
            "text": "",
            "selector": "#password",
            "type": "password",
            "name": "password",
            "id": "password",
            "placeholder": "Password",
            "ariaLabel": None,
            "sensitive": True,
            "value": "[PASSWORD_REDACTED]",
        },
        {
            "tag": "button",
            "text": "Delete account",
            "selector": "#delete-account",
            "id": "delete-account",
            "sensitive": False,
        },
    ],
    "sanitized_text": "Submit",
    "page": {"url": "https://example.com", "title": "Example", "elements": []},
    "page_context": {"viewport_width": 800, "viewport_height": 600},
}


def test_decision_dom_strategy_selected():
    engine = DecisionEngine()
    decision = engine.decide_next_action(SAMPLE_DOM, "Click the Submit button", [])
    assert decision["status"] == "success"
    assert decision["action"] == "click"
    assert decision["target"] == "#submit-button"
    assert decision["resolved_target"]["strategy"] == "selector"
    assert decision["resolved_target"]["source"] == "dom"


def test_decision_coordinate_fallback_selected():
    engine = DecisionEngine()
    obs = {
        "ui_elements": [],
        "page": {"elements": []},
        "page_context": {"viewport_width": 400, "viewport_height": 300},
        "visual_ui_map": {
            "dimensions": {"width": 800, "height": 600},
            "elements": [
                {
                    "id": "ui_001",
                    "type": "button",
                    "box": {"x": 200, "y": 100, "width": 100, "height": 50},
                    "interactive": True,
                    "source": "dom+vision",
                    "confidence": 0.88,
                    "text": "Blue Action",
                    # no selector → coordinate path
                }
            ],
            "privacy_safe": True,
        },
    }
    decision = engine.decide_next_action(obs, "Click Blue Action", [])
    assert decision["status"] == "success"
    assert decision["action"] == "coordinate_click"
    assert decision["coordinates"] is not None
    assert decision["resolved_target"]["strategy"] == "coordinates"
    assert decision["resolved_target"]["source"] == "dom+vision"


def test_decision_no_action_when_unsafe():
    engine = DecisionEngine()
    decision = engine.decide_next_action(SAMPLE_DOM, "Click the password field", [])
    assert decision["status"] == "no_action"
    assert decision["action"] is None


def test_decision_requires_confirmation_destructive():
    engine = DecisionEngine()
    decision = engine.decide_next_action(SAMPLE_DOM, "Delete account", [])
    assert decision["status"] == "requires_confirmation"
    assert decision["action"] is None


def test_plan_coordinate_click():
    planner = ActionPlanner()
    decision = {
        "action": "coordinate_click",
        "status": "success",
        "coordinates": {"x": 500, "y": 300},
        "resolved_target": {
            "strategy": "coordinates",
            "source": "dom+vision",
            "confidence": 0.86,
            "id": "ui_002",
        },
    }
    steps = planner.plan(decision)
    assert len(steps) == 1
    assert steps[0]["type"] == "coordinate_click"
    assert steps[0]["x"] == 500
    assert steps[0]["y"] == 300
    assert steps[0]["source"] == "dom+vision"


def test_plan_existing_click_preserved():
    planner = ActionPlanner()
    steps = planner.plan({"action": "click", "target": "#submit", "status": "success"})
    assert steps == [{"type": "click", "selector": "#submit"}]


def test_plan_type_preserved():
    planner = ActionPlanner()
    steps = planner.plan(
        {"action": "type", "target": "#search", "text": "hi", "status": "success"}
    )
    assert steps == [{"type": "type", "selector": "#search", "text": "hi"}]


def test_plan_scroll_preserved():
    planner = ActionPlanner()
    steps = planner.plan(
        {"action": "scroll", "direction": "down", "amount": 500, "status": "success"}
    )
    assert steps == [{"type": "scroll", "direction": "down", "amount": 500}]


def test_plan_requires_confirmation_empty():
    planner = ActionPlanner()
    steps = planner.plan(
        {"action": "click", "target": "#x", "status": "requires_confirmation"}
    )
    assert steps == []


def test_password_target_rejected():
    resolver = TargetResolver()
    result = resolver.resolve(SAMPLE_DOM, "Click password")
    assert result.status == "no_action"


def test_sensitive_target_rejected():
    resolver = TargetResolver()
    obs = {
        "ui_elements": [
            {
                "tag": "input",
                "selector": "#card",
                "id": "card",
                "name": "card-number",
                "type": "text",
                "placeholder": "Card number",
                "sensitive": True,
            }
        ],
        "page": {"elements": []},
    }
    result = resolver.resolve(obs, "Click card")
    assert result.status == "no_action"


def test_out_of_viewport_rejected():
    resolver = TargetResolver()
    obs = {
        "ui_elements": [],
        "page": {"elements": []},
        "page_context": {"viewport_width": 200, "viewport_height": 200},
        "visual_ui_map": {
            "dimensions": {"width": 200, "height": 200},
            "elements": [
                {
                    "id": "ui_off",
                    "type": "button",
                    "box": {"x": -500, "y": -500, "width": 80, "height": 40},
                    "interactive": True,
                    "source": "vision",
                    "confidence": 0.9,
                    "text": "Offscreen",
                }
            ],
            "privacy_safe": True,
        },
    }
    result = resolver.resolve(obs, "Click Offscreen")
    assert result.status == "no_action"


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def _payload(task, extra_elements=None, visual_ui_map=None):
    elements = [
        {
            "tag": "button",
            "text": "Submit",
            "selector": "#submit-button",
            "id": "submit-button",
            "sensitive": False,
        }
    ]
    if extra_elements:
        elements.extend(extra_elements)
    body = {
        "task": task,
        "page": {
            "url": "https://example.com",
            "title": "Example",
            "visibleText": "Submit",
            "elements": elements,
            "viewportWidth": 800,
            "viewportHeight": 600,
        },
        "privacy_report": {"total_redactions": 0},
    }
    if visual_ui_map is not None:
        body["visual_ui_map"] = visual_ui_map
    return body


def test_api_coordinate_action_schema(client):
    ui_map = {
        "dimensions": {"width": 800, "height": 600},
        "elements": [
            {
                "id": "ui_vis",
                "type": "button",
                "box": {"x": 100, "y": 100, "width": 80, "height": 40},
                "interactive": True,
                "source": "vision",
                "confidence": 0.9,
                "text": "Vision Control",
                "sensitive": False,
            }
        ],
        "summary": {},
        "layout": {"groups": []},
        "privacy_safe": True,
    }
    res = client.post(
        "/api/agent/step",
        json=_payload("Click Vision Control", extra_elements=[], visual_ui_map=ui_map),
    )
    assert res.status_code == 200
    data = res.get_json()
    # May be coordinate_click (vision) — page also has Submit only, so Vision Control → coords
    assert data["status"] in ("success", "no_action")
    if data["status"] == "success" and data["action"]:
        assert data["action"]["type"] in ("coordinate_click", "click")
        if data["action"]["type"] == "coordinate_click":
            assert "x" in data["action"]
            assert "y" in data["action"]
            assert data["action"].get("target", {}).get("strategy") == "coordinates"


def test_api_execution_feedback_stored(client):
    client.post("/api/agent/step", json=_payload("Click the Submit button"))
    res = client.post(
        "/api/agent/execution",
        json={
            "task": "Click the Submit button",
            "action": "click",
            "status": "success",
            "strategy": "selector",
            "source": "dom",
            "confidence": 0.98,
            "execution": {
                "success": True,
                "status": "success",
                "strategy": "selector",
                "action": "click",
                "execution_time_ms": 8,
                "target": {"tag": "BUTTON", "role": "button"},
            },
        },
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert body["execution"]["status"] == "success"

    history = client.get("/api/agent/history").get_json()
    assert history["actions"]
    row = history["actions"][0]
    assert row["execution"]["status"] == "success"
    assert row.get("strategy") == "selector"


def test_api_unsafe_action_rejected(client):
    res = client.post(
        "/api/agent/step",
        json=_payload(
            "Click password",
            extra_elements=[
                {
                    "tag": "input",
                    "text": "",
                    "selector": "#password",
                    "type": "password",
                    "id": "password",
                    "name": "password",
                    "sensitive": True,
                }
            ],
        ),
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "no_action"
    assert data["action"] is None


def test_api_destructive_requires_confirmation(client):
    res = client.post("/api/agent/step", json=_payload("Delete account"))
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "requires_confirmation"
    assert data["action"] is None


def test_api_action_target_overlay(client):
    res = client.post("/api/agent/step", json=_payload("Click the Submit button"))
    assert res.status_code == 200
    data = res.get_json()
    assert data.get("action_target") is not None
    assert data["action_target"]["strategy"] == "selector"

    shot = client.get("/api/agent/screenshot").get_json()
    assert shot.get("action_target") is not None
