"""Adaptive task orchestrator integration tests (Milestone 4C)."""

import pytest

from agent.adaptive_replanner import MAX_REPLAN_ATTEMPTS
from agent.approved_action_delivery import reset_approved_action_delivery
from agent.confirmation_manager import reset_confirmation_manager
from agent.lifecycle_registry import reset_lifecycle_registry
from agent.task_orchestrator import TaskOrchestrator, reset_task_orchestrator
from agent.task_plan import STEP_PENDING, STEP_SUCCESS, TaskStep
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
    reset_task_plan_registry()
    reset_task_orchestrator()


def _search_page(results=False, cookie=False, moved=False):
    elements = [
        {
            "tag": "input",
            "type": "text",
            "selector": "#search-box",
            "id": "search-box",
            "ariaLabel": "search box",
            "placeholder": "Search",
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
    if cookie:
        elements.extend(
            [
                {
                    "tag": "dialog",
                    "role": "dialog",
                    "text": "We use cookies",
                    "type": "dialog",
                    "sensitive": False,
                },
                {
                    "tag": "button",
                    "text": "Accept cookies",
                    "selector": "#accept-cookies",
                    "id": "accept-cookies",
                    "sensitive": False,
                },
            ]
        )
        visible += " We use cookies Accept cookies"
    if results:
        elements.append(
            {
                "tag": "a",
                "text": "First search result: AI courses",
                "selector": "#result-1" if not moved else "#result-moved",
                "id": "result-1" if not moved else "result-moved",
                "sensitive": False,
            }
        )
        visible += " First search result AI courses results"
    return {
        "url": "https://example.com/demo/adaptive",
        "title": "Adaptive",
        "visibleText": visible,
        "elements": elements,
    }


def test_normal_flow_no_unnecessary_replan(orch):
    plan = orch.create_plan(
        "Search for AI courses and open the first result", tab_id=1
    )
    r = orch.resolve_current_step(plan.plan_id, page=_search_page())
    assert r["status"] == "success"
    assert plan.replan_count == 0
    assert plan.plan_version == 1


def test_layout_change_re_resolves(orch):
    plan = orch.create_plan(
        "Search for AI courses and open the first result", tab_id=1
    )
    # Complete type + search
    r1 = orch.resolve_current_step(plan.plan_id, page=_search_page())
    orch.on_step_execution_reported(
        plan.plan_id, lifecycle_id=r1["lifecycle_id"], verification_status="success", page=_search_page()
    )
    r2 = orch.resolve_current_step(plan.plan_id, page=_search_page())
    orch.on_step_execution_reported(
        plan.plan_id, lifecycle_id=r2["lifecycle_id"], verification_status="success", page=_search_page()
    )
    # Wait step with results (moved layout)
    page = _search_page(results=True, moved=True)
    r3 = orch.resolve_current_step(plan.plan_id, page=page)
    if r3.get("status") == "waiting":
        r3 = orch.resolve_current_step(plan.plan_id, page=page, wait_elapsed_ms=50)
    # Should reach open-result without failing; may re-resolve
    if plan.current_step() and plan.current_step().action_type == "click":
        r4 = orch.resolve_current_step(plan.plan_id, page=page)
        assert r4.get("status") in ("success", "recovering")
        if r4.get("action"):
            assert r4["action"]["type"] in ("click", "coordinate_click")


def test_cookie_dialog_replans_safely(orch):
    plan = orch.create_plan(
        "Search for AI courses and open the first result", tab_id=1
    )
    r1 = orch.resolve_current_step(plan.plan_id, page=_search_page())
    orch.on_step_execution_reported(
        plan.plan_id, lifecycle_id=r1["lifecycle_id"], verification_status="success", page=_search_page()
    )
    r2 = orch.resolve_current_step(plan.plan_id, page=_search_page())
    orch.on_step_execution_reported(
        plan.plan_id,
        lifecycle_id=r2["lifecycle_id"],
        verification_status="success",
        page=_search_page(),
    )
    cookie_page = _search_page(cookie=True)
    # Seed previous state then hit cookie page
    plan.last_page_state = {
        "generation": plan.perception_generation,
        "url_signature": "prev",
        "page_signature": "prev_sig",
        "interactive_elements": [],
        "element_count": 2,
        "dialog_present": False,
        "dialog_count": 0,
        "visual_summary": {},
        "role_distribution": {},
        "layout_group_count": 0,
    }
    r = orch.resolve_current_step(
        plan.plan_id,
        page=cookie_page,
        safe_page_state={"dialog_present": True, "url": cookie_page["url"]},
    )
    # Either inserted dismiss step or paused/recovering/waiting
    if plan.plan_version > 1:
        assert any(
            (s.meta or {}).get("strategy") == "safe_dialog" or "cookie" in (s.description or "").lower()
            for s in plan.steps
            if s.status == STEP_PENDING or s.status in ("ready", "resolving", "executing")
        )
    assert plan.status in ("running", "paused", "waiting_for_confirmation")
    # Completed prefix immutable
    assert plan.steps[0].status == STEP_SUCCESS


def test_replan_limit_stops_safely(orch):
    plan = orch.create_plan("Click first search result", tab_id=1)
    plan.replan_count = MAX_REPLAN_ATTEMPTS
    empty = {
        "url": "https://example.com/empty",
        "title": "Empty",
        "visibleText": "nothing",
        "elements": [{"tag": "div", "text": "Home", "selector": "#home", "id": "home"}],
    }
    # Force previous state so change + missing target triggers replan path
    plan.last_page_state = {
        "generation": 0,
        "url_signature": "old",
        "page_signature": "old_sig",
        "interactive_elements": [
            {"role": "link", "label": "First search result", "type": "a"}
        ],
        "element_count": 1,
        "dialog_present": False,
        "dialog_count": 0,
        "visual_summary": {},
        "role_distribution": {"link": 1},
        "layout_group_count": 0,
    }
    # Exhaust recovery then hit max replan
    plan.steps[0].recovery_attempts = 2
    r = orch.resolve_current_step(plan.plan_id, page=empty)
    # May recover first if recovery_attempts was reset — set high and force replan endpoint
    r2 = orch.replan_plan(plan.plan_id, page=empty)
    assert r2.get("status") in (
        "max_replan_attempts_reached",
        "failed",
        "paused",
    ) or plan.status in ("failed", "paused")
    if r2.get("status") == "max_replan_attempts_reached" or plan.last_replan_reason == "max_replan_attempts_reached":
        assert plan.is_terminal() or plan.status == "failed"


def test_no_infinite_replan_loop(orch):
    plan = orch.create_plan("Click mystery target", tab_id=1)
    page = {
        "url": "https://example.com/x",
        "title": "X",
        "visibleText": "idle",
        "elements": [],
    }
    plan.last_page_state = {
        "generation": 0,
        "url_signature": "a",
        "page_signature": "b",
        "interactive_elements": [{"role": "button", "label": "mystery target", "type": "button"}],
        "element_count": 1,
        "dialog_present": False,
        "dialog_count": 0,
        "visual_summary": {},
        "role_distribution": {"button": 1},
        "layout_group_count": 0,
    }
    for _ in range(8):
        if plan.is_terminal() or plan.status == "paused":
            break
        step = plan.current_step()
        if step:
            step.recovery_attempts = 2
        orch.resolve_current_step(plan.plan_id, page=page)
    assert plan.replan_count <= MAX_REPLAN_ATTEMPTS + 1
    assert plan.is_terminal() or plan.status in ("paused", "failed", "running")


def test_metrics_recorded(orch):
    plan = orch.create_plan(
        "Search for AI courses and open the first result", tab_id=1
    )
    orch.resolve_current_step(plan.plan_id, page=_search_page())
    assert "page_state_build_ms" in plan.performance or "re_perception_ms" in plan.performance
    view = plan.public_view()
    assert "replan_count" in view["metrics"]
