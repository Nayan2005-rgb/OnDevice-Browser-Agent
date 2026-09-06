"""API tests for confirmation + action lifecycle (Milestone 4A)."""

import time

import pytest

from agent.confirmation_manager import reset_confirmation_manager
from agent.lifecycle_registry import reset_lifecycle_registry
from agent.approved_action_delivery import reset_approved_action_delivery
from server.app import create_app


@pytest.fixture
def client():
    reset_confirmation_manager()
    reset_lifecycle_registry()
    reset_approved_action_delivery()
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client
    reset_confirmation_manager()
    reset_lifecycle_registry()
    reset_approved_action_delivery()


def _payload(task, extra_elements=None):
    elements = [
        {
            "tag": "button",
            "text": "Submit",
            "selector": "#submit-button",
            "id": "submit-button",
            "sensitive": False,
        },
        {
            "tag": "button",
            "text": "Delete Demo Item",
            "selector": "#delete-demo-item",
            "id": "delete-demo-item",
            "sensitive": False,
        },
        {
            "tag": "button",
            "text": "Show Message",
            "selector": "#show-message",
            "id": "show-message",
            "sensitive": False,
        },
        {
            "tag": "button",
            "text": "Pay Now",
            "selector": "#pay-now",
            "id": "pay-now",
            "sensitive": False,
        },
    ]
    if extra_elements:
        elements.extend(extra_elements)
    return {
        "task": task,
        "page": {
            "url": "https://example.com/demo",
            "title": "Demo",
            "visibleText": "Submit Delete Demo Item Show Message",
            "elements": elements,
        },
        "privacy_report": {"total_redactions": 0},
    }


def test_confirmation_endpoint(client):
    step = client.post(
        "/api/agent/step",
        json={**_payload("Click Delete Demo Item"), "tab_id": 42, "window_id": 1},
    ).get_json()
    assert step["status"] == "requires_confirmation"
    assert step["action"] is None
    conf = step["confirmation"]
    assert conf and conf["id"]

    res = client.post(
        "/api/agent/confirm", json={"confirmation_id": conf["id"]}
    )
    data = res.get_json()
    assert data["status"] == "approved"
    assert data["execution_status"] == "waiting_for_extension"
    assert data.get("execution_id")
    # Frontend must not receive raw action details
    assert "action" not in data or data.get("action") is None
    assert "selector" not in data

    claimed = client.get("/api/agent/approved-action?tab_id=42").get_json()
    assert claimed["status"] == "approved"
    assert claimed["action"]["type"] == "click"
    assert claimed["action"]["selector"] == "#delete-demo-item"


def test_cancel_endpoint(client):
    step = client.post(
        "/api/agent/step", json=_payload("Delete Demo Item")
    ).get_json()
    conf = step["confirmation"]
    res = client.post(
        "/api/agent/cancel", json={"confirmation_id": conf["id"]}
    ).get_json()
    assert res["status"] == "cancelled"


def test_pending_confirmation(client):
    client.post("/api/agent/step", json=_payload("Click Delete Demo Item"))
    res = client.get("/api/agent/pending-confirmation").get_json()
    assert res["status"] == "requires_confirmation"
    assert res["confirmation"]["id"]


def test_invalid_id(client):
    res = client.post(
        "/api/agent/confirm", json={"confirmation_id": "confirm_missing"}
    ).get_json()
    assert res["status"] == "invalid_confirmation"


def test_expired_confirmation(client):
    from agent.confirmation_manager import get_confirmation_manager

    step = client.post(
        "/api/agent/step", json=_payload("Click Delete Demo Item")
    ).get_json()
    cid = step["confirmation"]["id"]
    mgr = get_confirmation_manager()
    record = mgr.store.get(cid)
    record["expires_at"] = time.time() - 5
    mgr.store.put(cid, record)
    res = client.post(
        "/api/agent/confirm", json={"confirmation_id": cid}
    ).get_json()
    assert res["status"] == "expired"


def test_replay_prevention(client):
    step = client.post(
        "/api/agent/step",
        json={**_payload("Click Delete Demo Item"), "tab_id": 7},
    ).get_json()
    cid = step["confirmation"]["id"]
    first = client.post(
        "/api/agent/confirm", json={"confirmation_id": cid}
    ).get_json()
    assert first["status"] == "approved"
    assert first["execution_status"] == "waiting_for_extension"
    second = client.post(
        "/api/agent/confirm", json={"confirmation_id": cid}
    ).get_json()
    assert second["status"] == "invalid_confirmation"


def test_frontend_cannot_modify_action_on_confirm(client):
    step = client.post(
        "/api/agent/step", json=_payload("Click Delete Demo Item")
    ).get_json()
    cid = step["confirmation"]["id"]
    res = client.post(
        "/api/agent/confirm",
        json={
            "confirmation_id": cid,
            "action": {"type": "click", "selector": "#hacked"},
            "selector": "#hacked",
        },
    )
    assert res.status_code == 400
    assert res.get_json()["error"] == "client_action_modification_forbidden"


def test_lifecycle_history(client):
    client.post("/api/agent/step", json=_payload("Click Show Message"))
    history = client.get("/api/agent/history").get_json()
    assert history["actions"]
    row = history["actions"][0]
    assert row.get("safety") is not None
    assert row.get("lifecycle_id")

    life = client.get("/api/agent/lifecycle").get_json()
    assert life["lifecycle"] is not None
    assert life["lifecycle"]["state"] in (
        "executing",
        "resolved",
        "approved",
        "success",
    )


def test_safe_action_still_returns_action(client):
    data = client.post(
        "/api/agent/step", json=_payload("Click Show Message")
    ).get_json()
    assert data["status"] == "success"
    assert data["action"]["type"] == "click"
    assert data["action"]["selector"] == "#show-message"


def test_verification_via_execution(client):
    step = client.post(
        "/api/agent/step", json=_payload("Click Show Message")
    ).get_json()
    before = step["pre_action_state"]
    after = dict(before)
    after["dialog_present"] = True
    after["page_signature"] = "changed-sig"
    after["element_count"] = (before.get("element_count") or 0) + 1
    res = client.post(
        "/api/agent/execution",
        json={
            "task": "Click Show Message",
            "action": "click",
            "status": "success",
            "lifecycle_id": step["lifecycle_id"],
            "execution": {"success": True, "status": "success", "action": "click"},
            "pre_action_state": before,
            "post_action_state": after,
        },
    ).get_json()
    assert res["ok"] is True
    assert res["verification"]["status"] == "success"
