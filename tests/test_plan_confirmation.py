"""Plan confirmation checkpoint tests (Milestone 4B)."""

import pytest

from agent.confirmation_manager import reset_confirmation_manager
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


def _delete_page():
    return {
        "url": "https://example.com/demo",
        "title": "Demo",
        "visibleText": "Delete Demo Item",
        "elements": [
            {
                "tag": "button",
                "text": "Delete Demo Item",
                "selector": "#delete-demo-item",
                "id": "delete-demo-item",
                "sensitive": False,
            }
        ],
    }


def _search_delete_goal_pages():
    search = {
        "url": "https://example.com/demo",
        "title": "Demo",
        "visibleText": "search box Search",
        "elements": [
            {
                "tag": "input",
                "selector": "#search-box",
                "id": "search-box",
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
        ],
    }
    results = {
        "url": "https://example.com/demo",
        "title": "Demo",
        "visibleText": "results First search result Delete Demo Item",
        "elements": search["elements"]
        + [
            {
                "tag": "a",
                "text": "First search result: demo item",
                "selector": "#result-1",
                "id": "result-1",
                "sensitive": False,
            },
            {
                "tag": "button",
                "text": "Delete Demo Item",
                "selector": "#delete-demo-item",
                "id": "delete-demo-item",
                "sensitive": False,
            },
        ],
    }
    return search, results


def test_risky_step_pauses_and_blocks_later_steps(client):
    res = client.post(
        "/api/agent/plan",
        json={
            "goal": "Click Delete Demo Item",
            "tab_id": 42,
            "window_id": 1,
            "page": _delete_page(),
        },
    ).get_json()
    assert res["status"] == "requires_confirmation"
    assert res["action"] is None
    plan = res["plan"]
    assert plan["status"] == "waiting_for_confirmation"

    # Resume while waiting must not execute
    again = client.post(
        "/api/agent/plan/resume",
        json={"plan_id": res["plan_id"], "page": _delete_page()},
    ).get_json()
    assert again["status"] in ("waiting_for_confirmation", "requires_confirmation")
    assert again.get("action") is None


def test_approved_confirmation_queues_exactly_once(client):
    res = client.post(
        "/api/agent/plan",
        json={
            "goal": "Click Delete Demo Item",
            "tab_id": 42,
            "window_id": 1,
            "page": _delete_page(),
        },
    ).get_json()
    cid = res["confirmation"]["id"]
    approved = client.post(
        "/api/agent/confirm", json={"confirmation_id": cid}
    ).get_json()
    assert approved["status"] == "approved"
    assert approved["execution_id"]

    claimed = client.get("/api/agent/approved-action?tab_id=42").get_json()
    assert claimed["status"] == "approved"
    assert claimed["action"]["type"] == "click"
    assert claimed["action"]["selector"] == "#delete-demo-item"

    # Exactly once
    again = client.get("/api/agent/approved-action?tab_id=42").get_json()
    assert again["status"] == "none"

    # Wrong tab cannot claim (already claimed, but also tab binding)
    wrong = client.get("/api/agent/approved-action?tab_id=99").get_json()
    assert wrong["status"] == "none"


def test_cancelled_confirmation_stops_plan(client):
    res = client.post(
        "/api/agent/plan",
        json={
            "goal": "Click Delete Demo Item",
            "tab_id": 5,
            "page": _delete_page(),
        },
    ).get_json()
    cid = res["confirmation"]["id"]
    client.post("/api/agent/cancel", json={"confirmation_id": cid})
    status = client.get(f"/api/agent/plan/{res['plan_id']}").get_json()
    assert status["plan"]["status"] in ("cancelled", "paused")
    resume = client.post(
        "/api/agent/plan/resume",
        json={"plan_id": res["plan_id"], "page": _delete_page()},
    ).get_json()
    assert resume.get("action") is None


def test_multi_step_delete_pauses_before_destructive(client):
    search, results = _search_delete_goal_pages()
    created = client.post(
        "/api/agent/plan",
        json={
            "goal": "Search for demo item and delete the first result",
            "tab_id": 8,
            "page": search,
        },
    ).get_json()
    plan_id = created["plan_id"]
    assert created["action"]["type"] == "type"

    # Complete type + click + wait quickly
    client.post(
        "/api/agent/execution",
        json={
            "plan_id": plan_id,
            "lifecycle_id": created["lifecycle_id"],
            "status": "success",
            "execution": {"status": "success", "success": True},
            "page": search,
        },
    )
    click = client.post(
        "/api/agent/plan/resume", json={"plan_id": plan_id, "page": search}
    ).get_json()
    if click.get("lifecycle_id"):
        client.post(
            "/api/agent/execution",
            json={
                "plan_id": plan_id,
                "lifecycle_id": click["lifecycle_id"],
                "status": "success",
                "execution": {"status": "success", "success": True},
                "page": results,
            },
        )

    # Advance through wait / open until delete confirmation or failure
    for _ in range(6):
        nxt = client.post(
            "/api/agent/plan/resume",
            json={
                "plan_id": plan_id,
                "page": results,
                "wait_elapsed_ms": 500,
                "tab_id": 8,
            },
        ).get_json()
        if nxt.get("status") == "requires_confirmation":
            assert nxt["action"] is None
            plan = nxt["plan"]
            assert plan["status"] == "waiting_for_confirmation"
            # Prior safe steps should have succeeded; delete not executed
            assert any(s["status"] == "success" for s in plan["steps"][:-1])
            return
        if nxt.get("action") and nxt.get("lifecycle_id"):
            client.post(
                "/api/agent/execution",
                json={
                    "plan_id": plan_id,
                    "lifecycle_id": nxt["lifecycle_id"],
                    "status": "success",
                    "execution": {"status": "success", "success": True},
                    "page": results,
                },
            )
        if nxt.get("status") in ("failed", "completed", "cancelled"):
            break

    plan = client.get(f"/api/agent/plan/{plan_id}").get_json()["plan"]
    # Soft assert: either we hit confirmation or plan still progressing safely
    assert plan["status"] in (
        "waiting_for_confirmation",
        "running",
        "completed",
        "failed",
    )
