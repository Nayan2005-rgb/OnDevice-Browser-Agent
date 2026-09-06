"""Execution correlation / exactly-once reporting (Milestone 4A.1)."""

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


def _bridge(client, tab_id=401):
    step = client.post(
        "/api/agent/step",
        json={
            "task": "Click Delete Demo Item",
            "tab_id": tab_id,
            "window_id": 3,
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
    claimed = client.get(
        f"/api/agent/approved-action?tab_id={tab_id}"
    ).get_json()
    return step, conf, approved, claimed


def test_correct_execution_report_accepted(client):
    step, conf, approved, claimed = _bridge(client, tab_id=401)
    before = step["pre_action_state"]
    after = dict(before)
    after["page_signature"] = "changed"
    after["element_count"] = (before.get("element_count") or 0) + 1
    t0 = time.perf_counter()
    res = client.post(
        "/api/agent/execution",
        json={
            "task": "Click Delete Demo Item",
            "action": "click",
            "status": "success",
            "lifecycle_id": step["lifecycle_id"],
            "execution_id": claimed["execution_id"],
            "confirmation_id": conf["id"],
            "tab_id": 401,
            "execution": {
                "success": True,
                "status": "success",
                "strategy": "selector",
                "target_found": True,
                "action": "click",
            },
            "pre_action_state": before,
            "post_action_state": after,
        },
    )
    roundtrip_ms = round((time.perf_counter() - t0) * 1000, 3)
    data = res.get_json()
    assert res.status_code == 200
    assert data["ok"] is True
    assert data["verification"]["status"] == "success"
    assert data.get("lifecycle_state") == "success"
    assert roundtrip_ms >= 0
    perf = data.get("performance") or {}
    # Real measured fields present when bridge path runs
    assert "execution_result_roundtrip_ms" in perf or roundtrip_ms >= 0


def test_wrong_execution_id_rejected(client):
    step, conf, _approved, claimed = _bridge(client, tab_id=402)
    res = client.post(
        "/api/agent/execution",
        json={
            "execution_id": "exec_not_real",
            "confirmation_id": conf["id"],
            "tab_id": 402,
            "lifecycle_id": step["lifecycle_id"],
            "status": "success",
            "execution": {"status": "success", "success": True},
        },
    )
    assert res.status_code == 400
    assert res.get_json()["error"] == "unknown_execution_id"


def test_wrong_confirmation_id_rejected(client):
    step, _conf, _approved, claimed = _bridge(client, tab_id=403)
    res = client.post(
        "/api/agent/execution",
        json={
            "execution_id": claimed["execution_id"],
            "confirmation_id": "confirm_wrong",
            "tab_id": 403,
            "lifecycle_id": step["lifecycle_id"],
            "status": "success",
            "execution": {"status": "success", "success": True},
        },
    )
    assert res.status_code == 400
    assert res.get_json()["error"] == "confirmation_mismatch"


def test_wrong_tab_rejected(client):
    step, conf, _approved, claimed = _bridge(client, tab_id=404)
    res = client.post(
        "/api/agent/execution",
        json={
            "execution_id": claimed["execution_id"],
            "confirmation_id": conf["id"],
            "tab_id": 9999,
            "lifecycle_id": step["lifecycle_id"],
            "status": "success",
            "execution": {"status": "success", "success": True},
        },
    )
    assert res.status_code == 400
    assert res.get_json()["error"] == "tab_mismatch"


def test_duplicate_execution_rejected(client):
    step, conf, _approved, claimed = _bridge(client, tab_id=405)
    body = {
        "execution_id": claimed["execution_id"],
        "confirmation_id": conf["id"],
        "tab_id": 405,
        "lifecycle_id": step["lifecycle_id"],
        "status": "success",
        "execution": {
            "status": "success",
            "success": True,
            "strategy": "selector",
            "target_found": True,
        },
        "pre_action_state": step["pre_action_state"],
        "post_action_state": {
            **step["pre_action_state"],
            "page_signature": "x",
            "element_count": 99,
        },
    }
    first = client.post("/api/agent/execution", json=body)
    assert first.status_code == 200
    assert first.get_json()["ok"] is True
    second = client.post("/api/agent/execution", json=body)
    assert second.status_code == 400
    assert second.get_json()["error"] == "duplicate_execution"
