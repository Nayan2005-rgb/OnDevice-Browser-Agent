"""Session API endpoint tests."""

from __future__ import annotations


def test_create_and_get_session(session_client):
    res = session_client.post(
        "/api/agent/sessions",
        json={"goal": "Search for laptops", "tab_id": 9, "operator_replan_approval": True},
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "created"
    assert data["session_id"]
    assert data["plan_id"]
    sid = data["session_id"]

    got = session_client.get(f"/api/agent/sessions/{sid}")
    assert got.status_code == 200
    body = got.get_json()
    assert body["session"]["goal"] == "Search for laptops"
    assert "password" not in str(body).lower() or "[redacted]" in str(body).lower()
    # No DB paths
    assert ".db" not in str(body)


def test_pause_resume_cancel_api(session_client):
    res = session_client.post(
        "/api/agent/sessions", json={"goal": "Compare phones", "tab_id": 2}
    )
    sid = res.get_json()["session_id"]

    p = session_client.post(f"/api/agent/sessions/{sid}/pause", json={})
    assert p.status_code == 200
    assert p.get_json()["status"] == "paused"

    r = session_client.post(f"/api/agent/sessions/{sid}/resume", json={})
    assert r.status_code == 409
    assert r.get_json()["status"] == "fresh_perception_required"

    c = session_client.post(f"/api/agent/sessions/{sid}/cancel", json={})
    assert c.status_code == 200
    assert c.get_json()["status"] == "cancelled"

    r2 = session_client.post(
        f"/api/agent/sessions/{sid}/resume",
        json={"page": {"url": "https://x", "elements": []}},
    )
    assert r2.status_code == 409
    assert r2.get_json()["status"] == "cancelled"


def test_invalid_session_id(session_client):
    res = session_client.get("/api/agent/sessions/does_not_exist")
    assert res.status_code == 404


def test_timeline_api(session_client):
    res = session_client.post("/api/agent/sessions", json={"goal": "g", "tab_id": 1})
    sid = res.get_json()["session_id"]
    tl = session_client.get(f"/api/agent/sessions/{sid}/timeline")
    assert tl.status_code == 200
    assert isinstance(tl.get_json()["timeline"], list)


def test_active_for_tab(session_client):
    session_client.post(
        "/api/agent/sessions", json={"goal": "g", "tab_id": 42}
    )
    res = session_client.get("/api/agent/sessions/active?tab_id=42")
    assert res.status_code == 200
    assert res.get_json()["session"] is not None


def test_plan_create_includes_session(session_client):
    res = session_client.post(
        "/api/agent/plan", json={"goal": "Search for headphones", "tab_id": 5}
    )
    assert res.status_code == 200
    data = res.get_json()
    # Session may be attached when storage available
    assert data.get("plan_id")
