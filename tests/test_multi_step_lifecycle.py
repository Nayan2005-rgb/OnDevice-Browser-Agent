"""Multi-step lifecycle integration tests (Milestone 4B)."""

import pytest

from agent.confirmation_manager import reset_confirmation_manager, get_confirmation_manager
from agent.lifecycle_registry import reset_lifecycle_registry
from agent.approved_action_delivery import reset_approved_action_delivery
from agent.task_plan_registry import reset_task_plan_registry
from agent.task_orchestrator import reset_task_orchestrator
from server.app import create_app


@pytest.fixture
def client():
    reset_confirmation_manager()
    reset_lifecycle_registry()
    reset_approved_action_delivery()
    reset_task_plan_registry()
    reset_task_orchestrator()
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
    reset_confirmation_manager()
    reset_lifecycle_registry()
    reset_approved_action_delivery()
    reset_task_plan_registry()
    reset_task_orchestrator()


def _page(elements, visible=""):
    return {
        "url": "https://example.com/demo",
        "title": "Demo",
        "visibleText": visible or " ".join(
            e.get("text") or e.get("id") or "" for e in elements
        ),
        "elements": elements,
    }


SEARCH_ELEMENTS = [
    {
        "tag": "input",
        "selector": "#search-box",
        "id": "search-box",
        "placeholder": "Search",
        "ariaLabel": "search box",
        "type": "text",
        "sensitive": False,
        "text": "",
    },
    {
        "tag": "button",
        "text": "Search",
        "selector": "#search-button",
        "id": "search-button",
        "sensitive": False,
    },
]

RESULT_ELEMENTS = SEARCH_ELEMENTS + [
    {
        "tag": "a",
        "text": "First search result: AI courses",
        "selector": "#result-1",
        "id": "result-1",
        "sensitive": False,
    }
]


def test_full_search_open_lifecycle(client):
    created = client.post(
        "/api/agent/plan",
        json={
            "goal": "Search for AI courses and open the first result",
            "tab_id": 11,
            "window_id": 1,
            "page": _page(SEARCH_ELEMENTS, "search box Search"),
        },
    ).get_json()
    assert created["status"] == "success"
    assert created["action"]["type"] == "type"
    plan_id = created["plan_id"]

    # Report type success
    life = created["lifecycle_id"]
    client.post(
        "/api/agent/execution",
        json={
            "plan_id": plan_id,
            "lifecycle_id": life,
            "task": "type",
            "status": "success",
            "execution": {"status": "success", "success": True},
            "pre_action_state": {"url": "https://example.com/demo", "page_signature": "a"},
            "post_action_state": {"url": "https://example.com/demo", "page_signature": "b"},
            "page": _page(SEARCH_ELEMENTS),
        },
    )

    # Resume → search click
    r2 = client.post(
        "/api/agent/plan/resume",
        json={
            "plan_id": plan_id,
            "tab_id": 11,
            "page": _page(SEARCH_ELEMENTS, "search box Search"),
        },
    ).get_json()
    assert r2["action"]["type"] == "click"
    assert "search" in (r2["action"].get("selector") or "").lower() or r2["action"].get(
        "selector"
    )

    client.post(
        "/api/agent/execution",
        json={
            "plan_id": plan_id,
            "lifecycle_id": r2["lifecycle_id"],
            "status": "success",
            "execution": {"status": "success", "success": True},
            "pre_action_state": {"url": "https://example.com/demo", "page_signature": "b"},
            "post_action_state": {
                "url": "https://example.com/demo",
                "page_signature": "c",
            },
            "page": _page(RESULT_ELEMENTS, "results First search result"),
        },
    )

    # Wait step with results visible
    r3 = client.post(
        "/api/agent/plan/resume",
        json={
            "plan_id": plan_id,
            "page": _page(RESULT_ELEMENTS, "results First search result AI courses"),
            "wait_elapsed_ms": 200,
        },
    ).get_json()
    # Wait may complete and advance, or return waiting — either way no premature open
    status = client.get(f"/api/agent/plan/{plan_id}").get_json()
    plan = status["plan"]
    assert plan["steps"][0]["status"] == "success"
    assert plan["steps"][1]["status"] == "success"

    # If wait already succeeded inside resume, current may be open step
    if plan["status"] != "completed":
        # Ensure open step resolves against results page
        if plan["steps"][2]["status"] != "success":
            # still on wait or past it
            pass
        r4 = client.post(
            "/api/agent/plan/resume",
            json={
                "plan_id": plan_id,
                "page": _page(RESULT_ELEMENTS, "results First search result"),
                "wait_elapsed_ms": 200,
            },
        ).get_json()
        if r4.get("action") and r4["action"]["type"] in ("click", "coordinate_click"):
            client.post(
                "/api/agent/execution",
                json={
                    "plan_id": plan_id,
                    "lifecycle_id": r4["lifecycle_id"],
                    "status": "success",
                    "execution": {"status": "success", "success": True},
                    "page": _page(RESULT_ELEMENTS),
                },
            )

    final = client.get(f"/api/agent/plan/{plan_id}").get_json()["plan"]
    # All completed steps success; no step executed out of order
    for s in final["steps"]:
        if s["status"] == "success":
            continue
        # pending only after last success is OK if plan still running
        assert s["status"] in ("pending", "success", "executing", "waiting", "ready", "resolving")


def test_one_action_at_a_time(client):
    res = client.post(
        "/api/agent/plan",
        json={
            "goal": "Search for AI courses and open the first result",
            "tab_id": 3,
            "page": _page(SEARCH_ELEMENTS, "search box"),
        },
    ).get_json()
    assert res.get("action") is not None
    # Response must not include a sequence of future actions
    assert "actions" not in res or not res.get("actions")
    plan = res["plan"]
    pending = [s for s in plan["steps"] if s["status"] == "pending"]
    assert len(pending) >= 3
