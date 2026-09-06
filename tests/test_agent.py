"""Tests for the agent's action planner and decision flow."""

from agent.action_planner import ActionPlanner
from agent.decision_engine import DecisionEngine
from agent.perception import Perception


SAMPLE_OBSERVATION = {
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
            "tag": "a",
            "text": "Learn more",
            "selector": "a.learn",
            "type": None,
            "name": None,
            "id": None,
            "placeholder": None,
            "ariaLabel": "Learn more",
            "sensitive": False,
        },
    ],
    "sanitized_text": "Email: [EMAIL_REDACTED] Submit your application",
    "page": {
        "url": "https://example.com",
        "title": "Example",
        "visibleText": "Email: [EMAIL_REDACTED] Submit your application",
        "elements": [],  # decision engine prefers ui_elements
    },
}


def test_plan_click_action():
    planner = ActionPlanner()
    decision = {"action": "click", "target": "#submit"}
    steps = planner.plan(decision)
    assert steps == [{"type": "click", "selector": "#submit"}]


def test_plan_type_action():
    planner = ActionPlanner()
    decision = {"action": "type", "target": "#username", "text": "alice"}
    steps = planner.plan(decision)
    assert steps == [{"type": "type", "selector": "#username", "text": "alice"}]


def test_plan_scroll_action():
    planner = ActionPlanner()
    decision = {"action": "scroll", "direction": "down", "amount": 500, "status": "success"}
    steps = planner.plan(decision)
    assert steps == [{"type": "scroll", "direction": "down", "amount": 500}]


def test_plan_unknown_action_returns_empty():
    planner = ActionPlanner()
    steps = planner.plan({"action": "unknown"})
    assert steps == []


def test_plan_no_action_status_returns_empty():
    planner = ActionPlanner()
    steps = planner.plan({"action": "click", "target": "#x", "status": "no_action"})
    assert steps == []


def test_click_decision_exact_button_text():
    engine = DecisionEngine()
    decision = engine.decide_next_action(SAMPLE_OBSERVATION, "Click the Submit button", [])
    assert decision["status"] == "success"
    assert decision["action"] == "click"
    assert decision["target"] == "#submit-button"
    assert "Submit" in decision["reasoning"]


def test_click_decision_case_insensitive():
    engine = DecisionEngine()
    decision = engine.decide_next_action(SAMPLE_OBSERVATION, "click the submit button", [])
    assert decision["status"] == "success"
    assert decision["action"] == "click"
    assert decision["target"] == "#submit-button"


def test_click_decision_aria_label():
    engine = DecisionEngine()
    decision = engine.decide_next_action(SAMPLE_OBSERVATION, "Click Learn more", [])
    assert decision["status"] == "success"
    assert decision["action"] == "click"
    assert decision["target"] == "a.learn"


def test_type_decision():
    engine = DecisionEngine()
    decision = engine.decide_next_action(
        SAMPLE_OBSERVATION, 'Type hello in the search box', []
    )
    assert decision["status"] == "success"
    assert decision["action"] == "type"
    assert decision["target"] == "#search"
    assert decision["text"] == "hello"


def test_scroll_decision():
    engine = DecisionEngine()
    decision = engine.decide_next_action(SAMPLE_OBSERVATION, "Scroll down", [])
    assert decision["status"] == "success"
    assert decision["action"] == "scroll"
    assert decision["direction"] == "down"
    assert decision["amount"] == 500


def test_no_matching_action():
    engine = DecisionEngine()
    decision = engine.decide_next_action(
        SAMPLE_OBSERVATION, "Click the Nonexistent Widget", []
    )
    assert decision["status"] == "no_action"
    assert decision["action"] is None
    assert "No matching element" in decision["reasoning"]


def test_action_response_schema_via_planner():
    engine = DecisionEngine()
    planner = ActionPlanner()
    decision = engine.decide_next_action(SAMPLE_OBSERVATION, "Click the Submit button", [])
    steps = planner.plan(decision)
    assert len(steps) == 1
    assert steps[0]["type"] == "click"
    assert "selector" in steps[0]


def test_perception_uses_structured_elements():
    perception = Perception()
    page = {
        "url": "https://example.com",
        "title": "Example",
        "visibleText": "Hello",
        "elements": [
            {
                "tag": "button",
                "text": "Go",
                "selector": "#go",
                "sensitive": False,
            }
        ],
    }
    obs = perception.observe(page, page["visibleText"])
    assert len(obs["ui_elements"]) == 1
    assert obs["ui_elements"][0]["selector"] == "#go"
    assert obs["sanitized_text"] == "Hello"
