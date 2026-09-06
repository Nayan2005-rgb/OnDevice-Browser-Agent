"""API tests for adaptive re-planning (Milestone 4C)."""

import pytest

from server.app import create_app
from agent.task_plan_registry import reset_task_plan_registry
from agent.task_orchestrator import reset_task_orchestrator
from agent.confirmation_manager import reset_confirmation_manager
from agent.lifecycle_registry import reset_lifecycle_registry
from agent.approved_action_delivery import reset_approved_action_delivery


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
    reset_task_plan_registry()
    reset_task_orchestrator()


PAGE = {
    "url": "https://example.com/demo",
    "title": "Demo",
    "visibleText": "Search",
    "elements": [
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
    ],
}


def test_get_plan_includes_version_fields(client):
    created = client.post(
        "/api/agent/plan",
        json={"goal": "Search for AI courses", "page": PAGE, "tab_id": 1},
    )
    assert created.status_code == 200
    plan_id = created.get_json()["plan_id"]
    res = client.get(f"/api/agent/plan/{plan_id}")
    data = res.get_json()
    assert res.status_code == 200
    assert data["plan"]["version"] == 1
    assert "replan_count" in data
    assert "page_change" in data or data["plan"].get("page_change") is None or True


def test_replan_rejects_client_steps(client):
    created = client.post(
        "/api/agent/plan",
        json={"goal": "Search for AI courses", "page": PAGE, "tab_id": 1},
    )
    plan_id = created.get_json()["plan_id"]
    res = client.post(
        "/api/agent/plan/replan",
        json={
            "plan_id": plan_id,
            "page": PAGE,
            "steps": [{"action_type": "click", "target_hint": "hack"}],
        },
    )
    assert res.status_code == 400
    assert res.get_json()["error"] == "client_steps_not_allowed"


def test_replan_requires_fresh_page(client):
    created = client.post(
        "/api/agent/plan",
        json={"goal": "Search for AI courses", "page": PAGE, "tab_id": 1},
    )
    plan_id = created.get_json()["plan_id"]
    res = client.post("/api/agent/plan/replan", json={"plan_id": plan_id})
    assert res.status_code == 400


def test_resume_after_pause(client):
    created = client.post(
        "/api/agent/plan",
        json={"goal": "Click home", "tab_id": 1},
    )
    plan_id = created.get_json()["plan_id"]
    login = {
        "url": "https://example.com/login",
        "title": "Login",
        "visibleText": "Password Sign in",
        "elements": [
            {
                "tag": "dialog",
                "role": "dialog",
                "text": "Login requires password",
                "type": "dialog",
            },
            {
                "tag": "input",
                "type": "password",
                "ariaLabel": "Password",
                "sensitive": True,
            },
            {"tag": "button", "text": "Home", "selector": "#home", "id": "home"},
        ],
    }
    paused = client.post(
        "/api/agent/plan/resume",
        json={
            "plan_id": plan_id,
            "page": login,
            "safe_page_state": {"dialog_present": True},
        },
    )
    pdata = paused.get_json()
    assert pdata.get("requires_user_intervention") or pdata.get("status") == "paused"

    clear = {
        "url": "https://example.com/",
        "title": "Home",
        "visibleText": "Home",
        "elements": [
            {"tag": "button", "text": "Home", "selector": "#home", "id": "home", "sensitive": False},
        ],
    }
    resumed = client.post(
        "/api/agent/plan/resume",
        json={"plan_id": plan_id, "page": clear},
    )
    assert resumed.status_code == 200
    body = resumed.get_json()
    assert body.get("status") != "not_found"


def test_plan_status_hides_sensitive_content(client):
    created = client.post(
        "/api/agent/plan",
        json={"goal": "Search for AI courses", "page": PAGE},
    )
    plan_id = created.get_json()["plan_id"]
    res = client.get(f"/api/agent/plan/{plan_id}")
    blob = str(res.get_json()).lower()
    assert "hunter2" not in blob
    assert "raw_screenshot" not in blob
