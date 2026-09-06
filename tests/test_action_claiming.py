"""Action claiming unit/API tests (Milestone 4A.1)."""

from __future__ import annotations

import time

import pytest

from agent.action_state import ActionStateMachine, InvalidTransitionError
from agent.approved_action_delivery import ApprovedActionDelivery


def test_state_machine_bridge_path():
    life = ActionStateMachine.create("life_claim")
    life.transition("resolved")
    life.transition("requires_confirmation")
    life.transition("approved")
    life.transition("waiting_for_extension")
    life.transition("claimed")
    life.transition("executing")
    life.transition("executed")
    life.transition("verifying")
    life.transition("success")
    assert life.state == "success"


def test_invalid_bridge_transitions_rejected():
    with pytest.raises(InvalidTransitionError):
        ActionStateMachine.assert_transition("cancelled", "executing")
    with pytest.raises(InvalidTransitionError):
        ActionStateMachine.assert_transition("expired", "claimed")
    with pytest.raises(InvalidTransitionError):
        ActionStateMachine.assert_transition("success", "executing")
    with pytest.raises(InvalidTransitionError):
        ActionStateMachine.assert_transition("executed", "approved")


def test_claim_marks_performance():
    delivery = ApprovedActionDelivery()
    delivery.create(
        confirmation_id="confirm_p",
        action={"type": "click", "selector": "#x"},
        tab_id=1,
    )
    time.sleep(0.01)
    claimed = delivery.claim_for_tab(1)
    assert claimed["status"] == "approved"
    record = delivery.get(claimed["execution_id"])
    assert record["status"] == "claimed"
    assert record["performance"].get("action_claim_ms") is not None
    assert record["performance"].get("approved_action_wait_ms") is not None


def test_privacy_public_record_omits_action_details():
    delivery = ApprovedActionDelivery()
    queued = delivery.create(
        confirmation_id="confirm_priv",
        action={
            "type": "type",
            "selector": "#password",
            "text": "super-secret",
        },
        tab_id=2,
        category="deletion",
    )
    public = queued["record"]
    assert "action" not in public
    assert public.get("action_type") == "type"
    assert "super-secret" not in str(public)


def test_sanitize_result_strips_sensitive_values():
    delivery = ApprovedActionDelivery()
    delivery.create(
        confirmation_id="confirm_s",
        action={"type": "click", "selector": "#x"},
        tab_id=3,
    )
    claimed = delivery.claim_for_tab(3)
    report = delivery.report_execution(
        execution_id=claimed["execution_id"],
        confirmation_id="confirm_s",
        tab_id=3,
        status="success",
        result={
            "status": "success",
            "strategy": "selector",
            "target_found": True,
            "text": "should-not-store",
            "value": "secret",
        },
    )
    assert report["ok"] is True
    stored = delivery.get(claimed["execution_id"])["result"]
    assert "text" not in stored
    assert "value" not in stored
    assert stored.get("strategy") == "selector"
