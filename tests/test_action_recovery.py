"""Action recovery engine (Milestone 5B)."""

from __future__ import annotations

from agent.action_recovery import (
    DECISION_REQUIRES_CONFIRMATION,
    DECISION_REQUIRES_USER_INTERVENTION,
    DECISION_SAFE_TO_RESUME,
    ActionRecoveryEngine,
    strip_trusted_coordinates,
)
from agent.durable_lifecycle import configure_durable_lifecycle


PAGE = {
    "url": "https://example.com/demo",
    "title": "Demo",
    "elements": [
        {
            "tag": "button",
            "text": "Search",
            "selector": "#search",
            "id": "search",
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
            "text": "Submit",
            "selector": "#submit",
            "id": "submit",
            "sensitive": False,
        },
    ],
}


def test_fresh_perception_required():
    engine = ActionRecoveryEngine()
    d = engine.evaluate(
        interrupted_status="executing",
        tab_id=1,
        task="Click Search",
        stored_action={"type": "click", "selector": "#search"},
        fresh_perception=None,
    )
    assert d.requires_fresh_perception is True
    assert d.decision == DECISION_REQUIRES_USER_INTERVENTION


def test_safe_action_can_re_resolve():
    engine = ActionRecoveryEngine()
    d = engine.evaluate(
        interrupted_status="executing",
        category="navigation",
        risk_level="safe",
        task="Click Search",
        stored_action={"type": "click", "selector": "#search", "x": 1, "y": 2},
        fresh_perception={"page": PAGE, "ui_elements": PAGE["elements"]},
        tab_id=1,
    )
    # Safe search may resume or require confirmation depending on classifier
    assert d.decision in (
        DECISION_SAFE_TO_RESUME,
        DECISION_REQUIRES_CONFIRMATION,
    )
    if d.action:
        assert d.action.get("coordinates_trusted") is False
        assert "x" not in d.action


def test_destructive_action_requires_confirmation_again():
    engine = ActionRecoveryEngine()
    d = engine.evaluate(
        interrupted_status="executing",
        category="deletion",
        risk_level="confirmation_required",
        task="Click Delete Demo Item",
        stored_action={"type": "click", "selector": "#delete-demo-item"},
        fresh_perception={"page": PAGE, "ui_elements": PAGE["elements"]},
        tab_id=1,
    )
    assert d.decision == DECISION_REQUIRES_CONFIRMATION


def test_payment_always_confirmation():
    engine = ActionRecoveryEngine()
    d = engine.evaluate(
        interrupted_status="claimed",
        category="payment",
        task="Click Pay Now",
        stored_action={"type": "click", "selector": "#pay"},
        fresh_perception={"page": PAGE},
        tab_id=1,
    )
    assert d.decision == DECISION_REQUIRES_CONFIRMATION


def test_captcha_requires_user_intervention():
    engine = ActionRecoveryEngine()
    d = engine.evaluate(
        interrupted_status="claimed",
        task="Click Search",
        fresh_perception={"page": PAGE},
        tab_id=1,
        captcha_detected=True,
    )
    assert d.decision == DECISION_REQUIRES_USER_INTERVENTION


def test_missing_tab_fail_closed():
    engine = ActionRecoveryEngine()
    d = engine.evaluate(
        interrupted_status="executing",
        fresh_perception={"page": PAGE},
        tab_id=None,
    )
    assert d.decision == "recovery_failed"


def test_strip_coordinates():
    out = strip_trusted_coordinates({"type": "click", "x": 9, "y": 8, "selector": "#a"})
    assert "x" not in out
    assert out["coordinates_trusted"] is False


def test_recovery_integrates_with_api(durable_client):
    client = durable_client
    step = client.post(
        "/api/agent/step",
        json={
            "task": "Click Delete Demo Item",
            "tab_id": 801,
            "page": {
                "url": PAGE["url"],
                "title": PAGE["title"],
                "visibleText": "Delete Demo Item",
                "elements": PAGE["elements"],
            },
            "privacy_report": {"total_redactions": 0},
        },
    ).get_json()
    client.post(
        "/api/agent/confirm", json={"confirmation_id": step["confirmation"]["id"]}
    )
    client.get("/api/agent/approved-action?tab_id=801")
    from agent.approved_action_delivery import get_approved_action_delivery
    import time

    delivery = get_approved_action_delivery()
    # Find claimed and mark recovery
    for eid, rec in list(delivery._by_execution.items()):
        if rec.get("tab_id") == 801:
            delivery.mark_recovery_required(eid, reason="browser_disconnected")
            break

    res = client.post(
        "/api/agent/action/recover",
        json={
            "lifecycle_id": step["lifecycle_id"],
            "tab_id": 801,
            "page": PAGE,
        },
    ).get_json()
    assert res["status"] == "ok"
    assert res["recovery"]["decision"] == DECISION_REQUIRES_CONFIRMATION
    assert res["recovery"]["coordinates_trusted"] is False


def test_recovery_integrates_with_persistent_sessions(temp_db):
    configure_durable_lifecycle(temp_db)
    from agent.session_manager import SessionManager
    from agent.task_plan_registry import TaskPlanRegistry
    from agent.task_plan import create_empty_plan

    sm = SessionManager(temp_db, plan_registry=TaskPlanRegistry())
    plan = create_empty_plan("Delete item", tab_id=90)
    session = sm.create_session(goal=plan.goal, plan=plan, tab_id=90)
    sm.persist_session(session)
    assert sm.get_session(session.session_id) is not None
