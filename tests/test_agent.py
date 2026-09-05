"""Tests for the agent's action planner and decision flow."""

from agent.action_planner import ActionPlanner


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


def test_plan_unknown_action_returns_empty():
    planner = ActionPlanner()
    steps = planner.plan({"action": "unknown"})
    assert steps == []
