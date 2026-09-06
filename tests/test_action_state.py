"""Tests for action state machine (Milestone 4A / 4A.1)."""

import pytest

from agent.action_state import (
    ActionLifecycle,
    ActionStateMachine,
    InvalidTransitionError,
)


def test_valid_transitions():
    life = ActionStateMachine.create("life_1", task="Click x")
    assert life.state == "created"
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


def test_approved_to_waiting_for_extension():
    assert ActionStateMachine.validate_transition(
        "approved", "waiting_for_extension"
    )
    assert ActionStateMachine.validate_transition(
        "waiting_for_extension", "claimed"
    )
    assert ActionStateMachine.validate_transition("claimed", "executing")


def test_invalid_transitions_rejected():
    life = ActionStateMachine.create("life_2")
    life.transition("resolved")
    life.transition("requires_confirmation")
    life.transition("cancelled")
    with pytest.raises(InvalidTransitionError):
        life.transition("executing")


def test_cancelled_cannot_execute():
    assert not ActionStateMachine.validate_transition("cancelled", "executing")


def test_expired_cannot_claim():
    assert not ActionStateMachine.validate_transition("expired", "claimed")


def test_expired_cannot_approve():
    assert not ActionStateMachine.validate_transition("expired", "approved")


def test_success_cannot_execute_again():
    assert not ActionStateMachine.validate_transition("success", "executing")
    life = ActionStateMachine.create("life_3")
    for s in (
        "resolved",
        "approved",
        "executing",
        "executed",
        "verifying",
        "success",
    ):
        # resolved → approved is allowed
        if s == "approved" and life.state == "resolved":
            life.transition("approved")
            continue
        if life.can_transition(s):
            life.transition(s)
    assert life.state == "success"
    with pytest.raises(InvalidTransitionError):
        life.transition("executing")


def test_executed_cannot_go_back_to_approved():
    assert not ActionStateMachine.validate_transition("executed", "approved")
