"""Approved action delivery / claim tests (Milestone 4A.1)."""

from __future__ import annotations

import time

import pytest

from agent.approved_action_delivery import (
    ApprovedActionDelivery,
    reset_approved_action_delivery,
)
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


def _step_and_confirm(client, tab_id=201):
    step = client.post(
        "/api/agent/step",
        json={
            "task": "Click Delete Demo Item",
            "tab_id": tab_id,
            "window_id": 2,
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
            "privacy_report": {"total_redactions": 0},
        },
    ).get_json()
    conf = step["confirmation"]
    approved = client.post(
        "/api/agent/confirm", json={"confirmation_id": conf["id"]}
    ).get_json()
    return step, conf, approved


def test_correct_tab_retrieves_action(client):
    _step, conf, approved = _step_and_confirm(client, tab_id=201)
    t0 = time.perf_counter()
    claimed = client.get("/api/agent/approved-action?tab_id=201").get_json()
    claim_ms = round((time.perf_counter() - t0) * 1000, 3)
    assert claimed["status"] == "approved"
    assert claimed["execution_id"] == approved["execution_id"]
    assert claimed["confirmation_id"] == conf["id"]
    assert claimed["action"]["selector"] == "#delete-demo-item"
    assert claim_ms >= 0


def test_wrong_tab_receives_none(client):
    _step_and_confirm(client, tab_id=201)
    wrong = client.get("/api/agent/approved-action?tab_id=999").get_json()
    assert wrong["status"] == "none"


def test_first_claim_succeeds_second_fails(client):
    _step_and_confirm(client, tab_id=202)
    first = client.get("/api/agent/approved-action?tab_id=202").get_json()
    assert first["status"] == "approved"
    second = client.get("/api/agent/approved-action?tab_id=202").get_json()
    assert second["status"] == "none"


def test_expired_approved_action_unavailable():
    delivery = ApprovedActionDelivery(ttl_seconds=1)
    queued = delivery.create(
        confirmation_id="confirm_x",
        action={"type": "click", "selector": "#delete"},
        tab_id=303,
    )
    eid = queued["execution_id"]
    # Mutate the live store record (get() returns a copy)
    live = delivery._by_execution[eid]
    live["expires_at"] = time.time() - 1
    claimed = delivery.claim_for_tab(303)
    assert claimed["status"] == "none"
    assert delivery.get(eid)["status"] == "expired"


def test_unit_claim_is_atomic():
    delivery = ApprovedActionDelivery()
    delivery.create(
        confirmation_id="confirm_y",
        action={"type": "click", "selector": "#a"},
        tab_id=10,
    )
    a = delivery.claim_for_tab(10)
    b = delivery.claim_for_tab(10)
    assert a["status"] == "approved"
    assert b["status"] == "none"
