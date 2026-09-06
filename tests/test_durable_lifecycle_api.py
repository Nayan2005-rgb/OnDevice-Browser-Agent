"""Durable lifecycle HTTP API (Milestone 5B)."""

from __future__ import annotations


def test_pending_and_recovery_endpoints(durable_client):
    client = durable_client
    step = client.post(
        "/api/agent/step",
        json={
            "task": "Click Delete Demo Item",
            "tab_id": 701,
            "window_id": 1,
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
    pending = client.get("/api/agent/actions/pending").get_json()
    assert pending["status"] == "ok"
    assert any(c["id"] == step["confirmation"]["id"] for c in pending["confirmations"])

    # Approve then force recovery via lease path
    client.post(
        "/api/agent/confirm", json={"confirmation_id": step["confirmation"]["id"]}
    )
    claimed = client.get("/api/agent/approved-action?tab_id=701").get_json()
    assert claimed["status"] == "approved"

    from agent.approved_action_delivery import get_approved_action_delivery
    import time

    delivery = get_approved_action_delivery()
    eid = claimed["execution_id"]
    delivery._by_execution[eid]["lease_until"] = time.time() - 1
    delivery.expire_leases()

    recovery = client.get("/api/agent/actions/recovery").get_json()
    assert recovery["count"] >= 1

    detail = client.get(f"/api/agent/action/{step['lifecycle_id']}").get_json()
    assert detail["status"] == "ok"
    timeline = client.get(
        f"/api/agent/action/{step['lifecycle_id']}/timeline"
    ).get_json()
    assert timeline["status"] == "ok"


def test_action_cancel_api(durable_client):
    client = durable_client
    step = client.post(
        "/api/agent/step",
        json={
            "task": "Click Delete Demo Item",
            "tab_id": 702,
            "page": {
                "url": "https://example.com",
                "title": "t",
                "visibleText": "Delete Demo Item",
                "elements": [
                    {
                        "tag": "button",
                        "text": "Delete Demo Item",
                        "selector": "#delete-demo-item",
                        "sensitive": False,
                    }
                ],
            },
            "privacy_report": {"total_redactions": 0},
        },
    ).get_json()
    res = client.post(
        "/api/agent/action/cancel",
        json={
            "confirmation_id": step["confirmation"]["id"],
            "lifecycle_id": step["lifecycle_id"],
        },
    ).get_json()
    assert res["status"] == "cancelled"


def test_reject_client_action_mutation_on_recover(durable_client):
    client = durable_client
    res = client.post(
        "/api/agent/action/recover",
        json={"lifecycle_id": "x", "selector": "#evil", "tab_id": 1},
    )
    assert res.status_code == 400
    assert res.get_json()["error"] == "client_action_modification_forbidden"
