"""Milestone 4A.1 — confirm → approve → claim → execute bridge tests."""

from __future__ import annotations

import time

import pytest

from agent.approved_action_delivery import reset_approved_action_delivery
from agent.confirmation_manager import reset_confirmation_manager
from agent.lifecycle_registry import reset_lifecycle_registry
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


def _payload(task, tab_id=101, window_id=1):
    return {
        "task": task,
        "tab_id": tab_id,
        "window_id": window_id,
        "page": {
            "url": "https://example.com/demo",
            "title": "Demo",
            "visibleText": "Delete Demo Item Show Message",
            "elements": [
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
            ],
        },
        "privacy_report": {"total_redactions": 0},
    }


def _confirm_flow(client, tab_id=101):
    step = client.post(
        "/api/agent/step", json=_payload("Click Delete Demo Item", tab_id=tab_id)
    ).get_json()
    assert step["status"] == "requires_confirmation"
    conf = step["confirmation"]
    t0 = time.perf_counter()
    approved = client.post(
        "/api/agent/confirm", json={"confirmation_id": conf["id"]}
    ).get_json()
    approval_ms = round((time.perf_counter() - t0) * 1000, 3)
    return step, conf, approved, approval_ms


def test_approval_creates_waiting_action(client):
    _step, conf, approved, approval_ms = _confirm_flow(client)
    assert approved["status"] == "approved"
    assert approved["execution_status"] == "waiting_for_extension"
    assert approved["confirmation_id"] == conf["id"]
    assert approved.get("execution_id")
    assert "selector" not in approved
    assert approval_ms >= 0
    life = client.get("/api/agent/lifecycle").get_json()["lifecycle"]
    assert life["state"] == "waiting_for_extension"


def test_cancel_never_creates_executable_action(client):
    step = client.post(
        "/api/agent/step", json=_payload("Delete Demo Item", tab_id=55)
    ).get_json()
    cid = step["confirmation"]["id"]
    cancelled = client.post(
        "/api/agent/cancel", json={"confirmation_id": cid}
    ).get_json()
    assert cancelled["status"] == "cancelled"
    claimed = client.get("/api/agent/approved-action?tab_id=55").get_json()
    assert claimed["status"] == "none"


def test_expired_confirmation_never_creates_executable_action(client):
    from agent.confirmation_manager import get_confirmation_manager

    step = client.post(
        "/api/agent/step", json=_payload("Delete Demo Item", tab_id=56)
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
    claimed = client.get("/api/agent/approved-action?tab_id=56").get_json()
    assert claimed["status"] == "none"


def test_confirmation_cannot_be_reused(client):
    _step, conf, first, _ms = _confirm_flow(client, tab_id=57)
    assert first["status"] == "approved"
    second = client.post(
        "/api/agent/confirm", json={"confirmation_id": conf["id"]}
    ).get_json()
    assert second["status"] == "invalid_confirmation"


def test_approved_action_api_does_not_expose_pii_on_confirm(client):
    step = client.post(
        "/api/agent/step",
        json=_payload("Click Delete Demo Item", tab_id=88),
    ).get_json()
    # Inject a sensitive-looking label into confirmation target via store
    from agent.confirmation_manager import get_confirmation_manager

    cid = step["confirmation"]["id"]
    mgr = get_confirmation_manager()
    record = mgr.store.get(cid)
    record["target"]["label"] = "Account ending 4111"
    mgr.store.put(cid, record)

    approved = client.post(
        "/api/agent/confirm", json={"confirmation_id": cid}
    ).get_json()
    blob = str(approved)
    assert "4111" not in blob
    assert "selector" not in approved
    assert approved["execution_status"] == "waiting_for_extension"

    claimed = client.get("/api/agent/approved-action?tab_id=88").get_json()
    # Claimed payload includes action for extension only — no raw PII fields
    assert "password" not in str(claimed).lower()
    assert claimed["action"]["type"] == "click"
