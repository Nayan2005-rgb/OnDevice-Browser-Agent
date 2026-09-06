"""Session / plan repository tests."""

from __future__ import annotations

from agent.session import SESSION_RUNNING
from storage.repositories.plan_repository import PlanRepository
from storage.repositories.session_repository import SessionRepository


def test_session_repository_upsert_get(temp_db):
    repo = SessionRepository(temp_db)
    row = repo.upsert(
        {
            "session_id": "sess_1",
            "status": SESSION_RUNNING,
            "goal": "Search",
            "plan_version": 1,
            "current_step_index": 0,
            "metadata": {"safe": True},
        }
    )
    assert row["session_id"] == "sess_1"
    got = repo.get("sess_1")
    assert got["status"] == SESSION_RUNNING
    assert got["goal"] == "Search"


def test_plan_repository_roundtrip(temp_db):
    repo = PlanRepository(temp_db)
    payload = {
        "plan_id": "plan_1",
        "goal": "Search phones",
        "status": "running",
        "current_step_index": 0,
        "plan_version": 1,
        "steps": [
            {
                "step_id": "s1",
                "description": "Type",
                "action_type": "type",
                "status": "pending",
            }
        ],
        "revision_history": [],
    }
    repo.upsert(payload, session_id=None)
    got = repo.get("plan_1")
    assert got["goal"] == "Search phones"
    assert got["steps"][0]["description"] == "Type"


def test_list_active_sessions(temp_db):
    repo = SessionRepository(temp_db)
    repo.upsert({"session_id": "a", "status": "running", "goal": "a"})
    repo.upsert({"session_id": "b", "status": "completed", "goal": "b"})
    active = repo.list_sessions(active_only=True)
    ids = {r["session_id"] for r in active}
    assert "a" in ids
    assert "b" not in ids
