"""Durable lifecycle state machine (Milestone 5B)."""

from __future__ import annotations

import pytest

from agent.action_state import (
    RECOVERY_REQUIRED,
    ActionStateMachine,
    InvalidTransitionError,
)
from agent.lifecycle_registry import LifecycleRegistry


def test_recovery_required_transition_from_executing():
    assert ActionStateMachine.validate_transition("executing", RECOVERY_REQUIRED)
    assert ActionStateMachine.validate_transition("claimed", RECOVERY_REQUIRED)


def test_invalid_transition_raises():
    life = ActionStateMachine.create("life_x")
    life.transition("resolved")
    life.transition("cancelled")
    with pytest.raises(InvalidTransitionError):
        life.transition(RECOVERY_REQUIRED)


def test_lifecycle_persists_and_hydrates(temp_db):
    reg = LifecycleRegistry(db=temp_db)
    life = reg.create(task="Click Search", session_id="sess_1")
    reg.transition(life.lifecycle_id, "resolved")
    reg.transition(life.lifecycle_id, "approved")
    reg.transition(life.lifecycle_id, "waiting_for_extension")
    reg.transition(life.lifecycle_id, "claimed")
    reg.mark_recovery_required(life.lifecycle_id, "lease_expired")

    reg2 = LifecycleRegistry(db=temp_db)
    stats = reg2.hydrate()
    assert stats["restored"] >= 1
    item = reg2.get(life.lifecycle_id)
    assert item is not None
    assert item["lifecycle"].state == RECOVERY_REQUIRED
    assert item["recovery_reason"] == "lease_expired"
    events = reg2.durable_timeline(life.lifecycle_id)
    assert any(e.get("to_state") == RECOVERY_REQUIRED for e in events)


def test_recovery_required_can_go_to_confirmation():
    assert ActionStateMachine.validate_transition(
        RECOVERY_REQUIRED, "requires_confirmation"
    )
