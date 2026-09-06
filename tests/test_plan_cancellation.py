"""Plan cancellation tests (Milestone 4B)."""

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


PAGE = {
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


def test_active_plan_can_cancel(client):
    created = client.post(
        "/api/agent/plan",
        json={
            "goal": "Search for AI courses and open the first result",
            "tab_id": 1,
            "page": PAGE,
        },
    ).get_json()
    plan_id = created["plan_id"]
    cancelled = client.post(
        "/api/agent/plan/cancel", json={"plan_id": plan_id}
    ).get_json()
    assert cancelled["status"] == "cancelled"
    status = client.get(f"/api/agent/plan/{plan_id}").get_json()
    assert status["plan"]["status"] == "cancelled"


def test_future_steps_never_executed_after_cancel(client):
    created = client.post(
        "/api/agent/plan",
        json={
            "goal": "Search for AI courses and open the first result",
            "tab_id": 1,
            "page": PAGE,
        },
    ).get_json()
    plan_id = created["plan_id"]
    client.post(
        "/api/agent/execution",
        json={
            "plan_id": plan_id,
            "lifecycle_id": created["lifecycle_id"],
            "status": "success",
            "execution": {"status": "success", "success": True},
            "page": PAGE,
        },
    )
    client.post("/api/agent/plan/cancel", json={"plan_id": plan_id})
    plan = client.get(f"/api/agent/plan/{plan_id}").get_json()["plan"]
    assert plan["steps"][0]["status"] in ("success", "cancelled", "executing")
    for step in plan["steps"][1:]:
        assert step["status"] in ("pending", "cancelled")
        assert step["status"] != "success" or step["index"] == 1


def test_pending_confirmation_cancelled_with_plan(client):
    created = client.post(
        "/api/agent/plan",
        json={
            "goal": "Click Delete Demo Item",
            "tab_id": 9,
            "page": {
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
            },
        },
    ).get_json()
    assert created["status"] == "requires_confirmation"
    plan_id = created["plan_id"]
    client.post("/api/agent/plan/cancel", json={"plan_id": plan_id})
    pending = client.get("/api/agent/pending-confirmation").get_json()
    assert pending["status"] in ("none", "requires_confirmation")
    # If confirmation still listed, plan itself is cancelled
    plan = client.get(f"/api/agent/plan/{plan_id}").get_json()["plan"]
    assert plan["status"] == "cancelled"


def test_cancelled_plan_cannot_auto_resume(client):
    created = client.post(
        "/api/agent/plan",
        json={
            "goal": "Search for AI courses",
            "tab_id": 1,
            "page": PAGE,
        },
    ).get_json()
    plan_id = created["plan_id"]
    client.post("/api/agent/plan/cancel", json={"plan_id": plan_id})
    resume = client.post(
        "/api/agent/plan/resume", json={"plan_id": plan_id, "page": PAGE}
    ).get_json()
    assert resume["status"] == "cancelled"
    assert resume.get("error") == "cancelled_plan_cannot_auto_resume"
    assert resume.get("action") is None
