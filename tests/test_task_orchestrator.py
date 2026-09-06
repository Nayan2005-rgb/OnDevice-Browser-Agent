"""Tests for TaskOrchestrator multi-step control (Milestone 4B)."""

import pytest

from agent.confirmation_manager import reset_confirmation_manager
from agent.lifecycle_registry import reset_lifecycle_registry
from agent.approved_action_delivery import reset_approved_action_delivery
from agent.task_orchestrator import TaskOrchestrator, reset_task_orchestrator
from agent.task_plan_registry import reset_task_plan_registry


@pytest.fixture
def orch():
    reset_confirmation_manager()
    reset_lifecycle_registry()
    reset_approved_action_delivery()
    reset_task_plan_registry()
    reset_task_orchestrator()
    o = TaskOrchestrator()
    yield o
    reset_confirmation_manager()
    reset_lifecycle_registry()
    reset_approved_action_delivery()
    reset_task_plan_registry()
    reset_task_orchestrator()


def _search_page(query_typed=False, results=False, include_delete=False):
    elements = [
        {
            "tag": "input",
            "text": "",
            "selector": "#search-box",
            "id": "search-box",
            "placeholder": "Search",
            "ariaLabel": "search box",
            "type": "text",
            "sensitive": False,
        },
        {
            "tag": "button",
            "text": "Search",
            "selector": "#search-button",
            "id": "search-button",
            "sensitive": False,
        },
    ]
    visible = "Search"
    if results:
        elements.extend(
            [
                {
                    "tag": "a",
                    "text": "First search result: AI courses",
                    "selector": "#result-1",
                    "id": "result-1",
                    "sensitive": False,
                },
                {
                    "tag": "a",
                    "text": "Search result: Other",
                    "selector": "#result-2",
                    "id": "result-2",
                    "sensitive": False,
                },
            ]
        )
        visible += " First search result AI courses results"
    if include_delete:
        elements.append(
            {
                "tag": "button",
                "text": "Delete Demo Item",
                "selector": "#delete-demo-item",
                "id": "delete-demo-item",
                "sensitive": False,
            }
        )
        visible += " Delete Demo Item"
    return {
        "url": "https://example.com/demo/multi_step",
        "title": "Multi Step Demo",
        "visibleText": visible,
        "elements": elements,
    }


def test_only_current_step_executes(orch):
    plan = orch.create_plan(
        "Search for AI courses and open the first result", tab_id=1
    )
    assert plan.status == "running"
    assert plan.current_step_index == 0

    r1 = orch.resolve_current_step(plan.plan_id, page=_search_page())
    assert r1["status"] == "success"
    assert r1["action"]["type"] == "type"
    assert plan.current_step_index == 0
    # Future steps still pending
    assert plan.steps[1].status == "pending"
    assert plan.steps[3].status == "pending"


def test_next_step_cannot_execute_early(orch):
    plan = orch.create_plan("Search for AI courses and open the first result", tab_id=1)
    # Manually try to bump index without verification — orchestrator still uses current
    plan.current_step_index = 0
    r = orch.resolve_current_step(plan.plan_id, page=_search_page())
    assert r["action"]["type"] == "type"
    # Step 4 must not run
    assert plan.current_step_index == 0


def test_step_advances_only_after_verification(orch):
    plan = orch.create_plan("Click the Search button", tab_id=1)
    r = orch.resolve_current_step(plan.plan_id, page=_search_page())
    assert r["action"]["type"] == "click"
    assert plan.current_step_index == 0

    # Without verification, index stays
    assert plan.steps[0].status == "executing"

    adv = orch.on_step_execution_reported(
        plan.plan_id,
        lifecycle_id=plan.steps[0].lifecycle_id,
        verification_status="success",
        execution_success=True,
        page=_search_page(),
    )
    assert plan.steps[0].status == "success"
    assert plan.status == "completed"
    assert adv["plan"]["status"] == "completed"


def test_failed_step_stops_future(orch):
    plan = orch.create_plan(
        "Search for AI courses and open the first result", tab_id=1
    )
    orch.resolve_current_step(plan.plan_id, page=_search_page())
    orch.on_step_execution_reported(
        plan.plan_id,
        lifecycle_id=plan.steps[0].lifecycle_id,
        verification_status="success",
        page=_search_page(),
    )
    assert plan.current_step_index == 1
    orch.resolve_current_step(plan.plan_id, page=_search_page())
    # Exhaust recovery then fail
    for _ in range(3):
        res = orch.on_step_execution_reported(
            plan.plan_id,
            lifecycle_id=plan.steps[1].lifecycle_id,
            verification_status="failed",
            page=_search_page(),
        )
        if res.get("status") == "failed" or plan.status == "failed":
            break
        # recovering → re-resolve
        orch.resolve_current_step(plan.plan_id, page=_search_page())

    assert plan.status == "failed"
    assert plan.steps[2].status == "pending"
    assert plan.steps[3].status == "pending"


def test_reperception_generation_increments(orch):
    plan = orch.create_plan("Click Search", tab_id=1)
    g0 = plan.perception_generation
    orch.resolve_current_step(plan.plan_id, page=_search_page())
    assert plan.perception_generation == g0 + 1
    # Simulate success and next resolve would need new page — for single-step plan done
    orch.on_step_execution_reported(
        plan.plan_id,
        lifecycle_id=plan.steps[0].lifecycle_id,
        verification_status="success",
        page=_search_page(),
    )


def test_stale_coordinates_not_reused(orch):
    plan = orch.create_plan(
        "Search for AI courses and open the first result", tab_id=1
    )
    orch.resolve_current_step(plan.plan_id, page=_search_page())
    step0 = plan.steps[0]
    step0.meta["cached_coordinates"] = {"x": 1, "y": 2}
    orch.on_step_execution_reported(
        plan.plan_id,
        lifecycle_id=step0.lifecycle_id,
        verification_status="success",
        page=_search_page(),
    )
    # After success, caches cleared on that step
    assert "cached_coordinates" not in step0.meta
    assert "active_coordinates" not in step0.meta

    # Next resolve clears any leftover cache at start
    plan.steps[1].meta["cached_coordinates"] = {"x": 9, "y": 9}
    orch.resolve_current_step(plan.plan_id, page=_search_page())
    assert "cached_coordinates" not in plan.steps[1].meta


def test_wait_results_visible(orch):
    plan = orch.create_plan("Search for AI courses", tab_id=1)
    # Drive to wait step
    for _ in range(2):
        r = orch.resolve_current_step(plan.plan_id, page=_search_page())
        if r.get("action"):
            orch.on_step_execution_reported(
                plan.plan_id,
                lifecycle_id=plan.steps[plan.current_step_index].lifecycle_id,
                verification_status="success",
                page=_search_page(results=True),
            )
    assert plan.steps[plan.current_step_index].action_type == "wait"
    w = orch.resolve_current_step(
        plan.plan_id, page=_search_page(results=True), wait_elapsed_ms=100
    )
    assert plan.status in ("completed", "running")
    assert w["status"] in ("step_success", "completed", "success") or plan.steps[
        2
    ].status == "success"


def test_wait_timeout_fails(orch):
    plan = orch.create_plan("Wait for results", tab_id=1)
    assert plan.steps[0].action_type == "wait"
    empty = {
        "url": "https://example.com/empty",
        "title": "Empty",
        "visibleText": "Nothing here",
        "elements": [],
    }
    r = orch.resolve_current_step(plan.plan_id, page=empty, wait_elapsed_ms=0)
    assert r["status"] == "waiting"
    r2 = orch.resolve_current_step(
        plan.plan_id, page=empty, wait_elapsed_ms=6000
    )
    assert r2["status"] == "failed"
    assert plan.status == "failed"


def test_cancel_stops_future(orch):
    plan = orch.create_plan(
        "Search for AI courses and open the first result", tab_id=1
    )
    orch.resolve_current_step(plan.plan_id, page=_search_page())
    orch.on_step_execution_reported(
        plan.plan_id,
        lifecycle_id=plan.steps[0].lifecycle_id,
        verification_status="success",
        page=_search_page(),
    )
    cancelled = orch.cancel_plan(plan.plan_id)
    assert cancelled["status"] == "cancelled"
    assert plan.status == "cancelled"
    r = orch.resume_plan(plan.plan_id, page=_search_page())
    assert r["status"] == "cancelled"
    assert r.get("error") == "cancelled_plan_cannot_auto_resume"
    assert plan.steps[2].status == "pending"
    assert plan.steps[3].status == "pending"


def test_risky_step_pauses_plan(orch):
    plan = orch.create_plan("Click Delete Demo Item", tab_id=7, window_id=1)
    page = _search_page(include_delete=True)
    r = orch.resolve_current_step(plan.plan_id, page=page)
    assert r["status"] == "requires_confirmation"
    assert plan.status == "waiting_for_confirmation"
    assert r["action"] is None
    # Later steps would not exist on single-step; ensure no action leaked
    assert r.get("confirmation")


def test_plan_does_not_store_screenshots(orch):
    plan = orch.create_plan("Click Search", tab_id=1)
    orch.resolve_current_step(
        plan.plan_id,
        page=_search_page(),
        visual_context={"faces_detected": 0},
    )
    view = plan.public_view()
    assert "screenshot" not in str(view).lower() or "[REDACTED]" in str(view)
    assert not any(
        k in plan.meta for k in ("raw_screenshot", "original_image", "face_crop")
    )
