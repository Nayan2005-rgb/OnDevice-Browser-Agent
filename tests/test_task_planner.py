"""Tests for RuleBasedTaskPlanner (Milestone 4B)."""

from agent.task_planner import LLMTaskPlanner, RuleBasedTaskPlanner


def test_simple_click_plan():
    planner = RuleBasedTaskPlanner()
    plan = planner.create_plan("Click the Submit button")
    assert plan.status == "running"
    assert len(plan.steps) == 1
    assert plan.steps[0].action_type == "click"
    assert "Submit" in (plan.steps[0].target_hint or "")


def test_search_workflow_steps():
    planner = RuleBasedTaskPlanner()
    plan = planner.create_plan("Search for AI courses and open the first result")
    assert plan.status == "running"
    types = [s.action_type for s in plan.steps]
    assert types == ["type", "click", "wait", "click"]
    assert plan.steps[0].value == "AI courses"
    assert "first" in (plan.steps[3].target_hint or "").lower()


def test_search_only():
    planner = RuleBasedTaskPlanner()
    plan = planner.create_plan("Search for robotics")
    assert plan.status == "running"
    assert [s.action_type for s in plan.steps] == ["type", "click", "wait"]


def test_search_and_delete():
    planner = RuleBasedTaskPlanner()
    plan = planner.create_plan("Search for demo item and delete the first result")
    assert plan.status == "running"
    assert plan.steps[-1].action_type == "click"
    assert "delete" in (plan.steps[-1].target_hint or "").lower()


def test_type_into():
    planner = RuleBasedTaskPlanner()
    plan = planner.create_plan('Type "hello" into search box')
    assert plan.status == "running"
    assert plan.steps[0].action_type == "type"
    assert plan.steps[0].value == "hello"


def test_scroll_and_click():
    planner = RuleBasedTaskPlanner()
    plan = planner.create_plan("Scroll down and click Submit")
    assert [s.action_type for s in plan.steps] == ["scroll", "click"]


def test_unsupported_task():
    planner = RuleBasedTaskPlanner()
    plan = planner.create_plan("Invent a novel quantum browser strategy autonomously forever")
    assert plan.status == "unsupported"
    assert plan.unsupported_reason


def test_empty_goal_unsupported():
    planner = RuleBasedTaskPlanner()
    plan = planner.create_plan("   ")
    assert plan.status == "unsupported"


def test_sensitive_type_blocked():
    planner = RuleBasedTaskPlanner()
    plan = planner.create_plan('Type "my password is secret123" into search box')
    assert plan.status == "unsupported"
    assert "Sensitive" in (plan.unsupported_reason or "")


def test_plan_ids_unique():
    planner = RuleBasedTaskPlanner()
    a = planner.create_plan("Click A")
    b = planner.create_plan("Click B")
    assert a.plan_id != b.plan_id


def test_llm_placeholder_unsupported():
    plan = LLMTaskPlanner().create_plan("Click Submit")
    assert plan.status == "unsupported"


def test_navigate_safe_local():
    planner = RuleBasedTaskPlanner()
    plan = planner.create_plan("Navigate to multi_step_agent_demo.html")
    assert plan.status == "running"
    assert plan.steps[0].action_type == "navigate"


def test_navigate_unsafe_rejected():
    planner = RuleBasedTaskPlanner()
    plan = planner.create_plan("Navigate to https://evil.example.attacker/phish")
    # host must match allowlist; attacker host fails
    assert plan.status == "unsupported"
