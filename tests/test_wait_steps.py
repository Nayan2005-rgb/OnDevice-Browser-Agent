"""Wait step tests (Milestone 4B)."""

import pytest

from agent.task_orchestrator import TaskOrchestrator, reset_task_orchestrator
from agent.task_plan_registry import reset_task_plan_registry
from agent.task_planner import DEFAULT_WAIT_TIMEOUT_MS, MAX_WAIT_TIMEOUT_MS
from agent.confirmation_manager import reset_confirmation_manager
from agent.lifecycle_registry import reset_lifecycle_registry
from agent.approved_action_delivery import reset_approved_action_delivery


@pytest.fixture
def orch():
    reset_confirmation_manager()
    reset_lifecycle_registry()
    reset_approved_action_delivery()
    reset_task_plan_registry()
    reset_task_orchestrator()
    o = TaskOrchestrator()
    yield o
    reset_task_plan_registry()
    reset_task_orchestrator()


def test_wait_timeout_constants():
    assert DEFAULT_WAIT_TIMEOUT_MS == 5000
    assert MAX_WAIT_TIMEOUT_MS == 10000
    assert MAX_WAIT_TIMEOUT_MS >= DEFAULT_WAIT_TIMEOUT_MS


def test_target_appears_before_timeout(orch):
    plan = orch.create_plan("Wait for results")
    page = {
        "url": "https://example.com/r",
        "title": "R",
        "visibleText": "search results appear here",
        "elements": [
            {
                "tag": "div",
                "text": "First search result",
                "selector": "#result-1",
                "id": "result-1",
                "sensitive": False,
            }
        ],
    }
    r = orch.resolve_current_step(plan.plan_id, page=page, wait_elapsed_ms=100)
    assert plan.steps[0].status == "success" or r["status"] in (
        "step_success",
        "completed",
    )
    assert plan.status in ("completed", "running")


def test_page_change_detected(orch):
    plan = orch.create_plan("Wait for page change")
    page1 = {
        "url": "https://example.com/a",
        "title": "A",
        "visibleText": "one",
        "elements": [{"tag": "div", "text": "one", "selector": "#a", "id": "a"}],
    }
    page2 = {
        "url": "https://example.com/b",
        "title": "B",
        "visibleText": "two results",
        "elements": [
            {"tag": "div", "text": "two results", "selector": "#b", "id": "b"}
        ],
    }
    r1 = orch.resolve_current_step(plan.plan_id, page=page1, wait_elapsed_ms=0)
    assert r1["status"] == "waiting"
    r2 = orch.resolve_current_step(plan.plan_id, page=page2, wait_elapsed_ms=200)
    assert r2["status"] in ("step_success", "completed") or plan.steps[0].status == "success"


def test_timeout_safely_fails(orch):
    plan = orch.create_plan("Wait for dialog")
    empty = {
        "url": "https://example.com/x",
        "title": "X",
        "visibleText": "idle",
        "elements": [],
    }
    orch.resolve_current_step(plan.plan_id, page=empty, wait_elapsed_ms=0)
    r = orch.resolve_current_step(
        plan.plan_id, page=empty, wait_elapsed_ms=DEFAULT_WAIT_TIMEOUT_MS + 1
    )
    assert r["status"] == "failed"
    assert plan.status == "failed"


def test_no_infinite_wait(orch):
    plan = orch.create_plan("Wait for target to appear")
    step = plan.steps[0]
    assert (step.timeout_ms or DEFAULT_WAIT_TIMEOUT_MS) <= MAX_WAIT_TIMEOUT_MS
    empty = {
        "url": "https://example.com/x",
        "title": "X",
        "visibleText": "idle",
        "elements": [],
    }
    orch.resolve_current_step(plan.plan_id, page=empty, wait_elapsed_ms=0)
    orch.resolve_current_step(
        plan.plan_id, page=empty, wait_elapsed_ms=MAX_WAIT_TIMEOUT_MS + 50
    )
    assert plan.is_terminal()
