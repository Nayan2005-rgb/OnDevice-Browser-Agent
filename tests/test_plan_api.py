"""Plan API tests (Milestone 4B)."""

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
    "visibleText": "Submit",
    "elements": [
        {
            "tag": "button",
            "text": "Submit",
            "selector": "#submit-button",
            "id": "submit-button",
            "sensitive": False,
        }
    ],
}


def test_create_plan_api(client):
    res = client.post(
        "/api/agent/plan",
        json={"goal": "Click the Submit button", "tab_id": 1, "page": PAGE},
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["plan_id"]
    assert data["plan"]["goal"]
    assert data["plan"]["total_steps"] == 1
    assert data.get("action")


def test_get_plan_status(client):
    created = client.post(
        "/api/agent/plan",
        json={"goal": "Click Submit", "tab_id": 1, "page": PAGE},
    ).get_json()
    got = client.get(f"/api/agent/plan/{created['plan_id']}")
    assert got.status_code == 200
    body = got.get_json()
    assert body["plan"]["plan_id"] == created["plan_id"]
    assert "steps" in body["plan"]


def test_unsupported_plan_api(client):
    res = client.post(
        "/api/agent/plan",
        json={"goal": "Telepathically reorganize my calendar forever"},
    ).get_json()
    assert res["status"] == "unsupported"
    assert res["action"] is None


def test_status_includes_plan_id(client):
    created = client.post(
        "/api/agent/plan",
        json={"goal": "Click Submit", "tab_id": 1, "page": PAGE},
    ).get_json()
    status = client.get("/api/agent/status").get_json()
    assert status.get("plan_id") == created["plan_id"]


def test_plan_status_hides_pii(client):
    res = client.post(
        "/api/agent/plan",
        json={
            "goal": 'Type "user@example.com" into search box',
            "page": {
                "url": "https://example.com",
                "title": "T",
                "visibleText": "search box",
                "elements": [
                    {
                        "tag": "input",
                        "selector": "#search-box",
                        "id": "search-box",
                        "ariaLabel": "search box",
                        "type": "text",
                        "sensitive": False,
                        "text": "",
                    }
                ],
            },
        },
    ).get_json()
    blob = str(res)
    # Typed email may appear as step value in non-redacted form for non-sensitive
    # queries; ensure raw screenshot / forbidden keys absent
    assert "raw_screenshot" not in blob
    assert "face_crop" not in blob
    assert "original_image" not in blob


def test_raw_image_blocked_on_plan(client):
    res = client.post(
        "/api/agent/plan",
        json={
            "goal": "Click Submit",
            "page": PAGE,
            "visual_ui_map": {"privacy_safe": True, "elements": []},
            "raw_image": "data:image/png;base64,AAA",
        },
    )
    assert res.status_code == 400
    assert res.get_json()["error"] == "raw_screenshot_blocked"


def test_cancel_and_resume_endpoints(client):
    created = client.post(
        "/api/agent/plan",
        json={"goal": "Click Submit", "tab_id": 1, "page": PAGE},
    ).get_json()
    plan_id = created["plan_id"]
    cancelled = client.post(
        "/api/agent/plan/cancel", json={"plan_id": plan_id}
    ).get_json()
    assert cancelled["status"] == "cancelled"
    resume = client.post(
        "/api/agent/plan/resume", json={"plan_id": plan_id, "page": PAGE}
    ).get_json()
    assert resume["status"] == "cancelled"
