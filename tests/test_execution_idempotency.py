"""Persistent execution idempotency (Milestone 5B)."""

from __future__ import annotations

import threading

from agent.durable_lifecycle import configure_durable_lifecycle
from storage.repositories.execution_repository import ExecutionRepository


def test_duplicate_execution_rejected(temp_db):
    repo = ExecutionRepository(temp_db)
    first = repo.try_accept(
        execution_id="exec_dup_1",
        confirmation_id="c1",
        report_status="success",
    )
    assert first["status"] == "accepted"
    second = repo.try_accept(
        execution_id="exec_dup_1",
        confirmation_id="c1",
        report_status="success",
    )
    assert second["status"] == "duplicate_execution"
    assert "idempotency_check_ms" in (second["record"].get("performance") or {})


def test_concurrent_duplicate_execution_handled_safely(temp_db):
    repo = ExecutionRepository(temp_db)
    results = []

    def worker():
        results.append(
            repo.try_accept(
                execution_id="exec_race",
                confirmation_id="c_race",
                report_status="success",
            )["status"]
        )

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("accepted") == 1
    assert results.count("duplicate_execution") == 7


def test_api_duplicate_execution_with_durable_store(durable_client):
    client = durable_client
    step = client.post(
        "/api/agent/step",
        json={
            "task": "Click Delete Demo Item",
            "tab_id": 601,
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
    conf = step["confirmation"]
    approved = client.post(
        "/api/agent/confirm", json={"confirmation_id": conf["id"]}
    ).get_json()
    claimed = client.get("/api/agent/approved-action?tab_id=601").get_json()
    body = {
        "execution_id": claimed["execution_id"],
        "confirmation_id": conf["id"],
        "tab_id": 601,
        "lifecycle_id": step["lifecycle_id"],
        "status": "success",
        "execution": {"status": "success", "success": True, "strategy": "selector"},
    }
    first = client.post("/api/agent/execution", json=body)
    assert first.status_code == 200
    second = client.post("/api/agent/execution", json=body)
    assert second.status_code == 400
    assert second.get_json()["error"] == "duplicate_execution"
