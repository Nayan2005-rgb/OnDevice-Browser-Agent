"""Server crash recovery for durable actions (Milestone 5B)."""

from __future__ import annotations

import time

from agent.action_state import RECOVERY_REQUIRED
from agent.durable_lifecycle import configure_durable_lifecycle, hydrate_durable_lifecycle
from agent.lifecycle_registry import LifecycleRegistry


def test_server_crash_during_claimed(temp_db):
    configure_durable_lifecycle(temp_db)
    from agent.approved_action_delivery import get_approved_action_delivery
    from agent.lifecycle_registry import get_lifecycle_registry

    delivery = get_approved_action_delivery()
    registry = get_lifecycle_registry()
    life = registry.create(task="Click Search")
    for s in ("resolved", "approved", "waiting_for_extension", "claimed"):
        registry.transition(life.lifecycle_id, s, "setup")
    queued = delivery.create(
        confirmation_id="c_claim",
        action={"type": "click", "selector": "#search"},
        tab_id=31,
        lifecycle_id=life.lifecycle_id,
        category="safe",
        task="Click Search",
    )
    delivery.claim_for_tab(31)
    # Simulate crash + restart
    stats = hydrate_durable_lifecycle(temp_db)
    assert stats["deliveries"]["recovery_marked"] >= 1
    rec = get_approved_action_delivery().get(queued["execution_id"])
    assert rec["status"] == "recovery_required"
    item = get_lifecycle_registry().get(life.lifecycle_id)
    assert item["lifecycle"].state == RECOVERY_REQUIRED


def test_server_crash_during_executing(temp_db):
    configure_durable_lifecycle(temp_db)
    from agent.approved_action_delivery import get_approved_action_delivery
    from agent.lifecycle_registry import get_lifecycle_registry

    delivery = get_approved_action_delivery()
    registry = get_lifecycle_registry()
    life = registry.create(task="Click Search")
    for s in ("resolved", "approved", "waiting_for_extension", "claimed", "executing"):
        registry.transition(life.lifecycle_id, s, "setup")
    queued = delivery.create(
        confirmation_id="c_exec",
        action={"type": "click", "selector": "#search"},
        tab_id=32,
        lifecycle_id=life.lifecycle_id,
    )
    delivery.claim_for_tab(32)
    delivery.mark_executing(queued["execution_id"])
    # Force executing in DB
    rec = delivery.get(queued["execution_id"])
    rec["status"] = "executing"
    delivery._persist_unlocked(rec)

    hydrate_durable_lifecycle(temp_db)
    restored = get_approved_action_delivery().get(queued["execution_id"])
    assert restored["status"] == "recovery_required"
    assert "execution" in (restored.get("recovery_reason") or "")
    # Never assume action was not executed — no auto replay
    assert get_approved_action_delivery().claim_for_tab(32)["status"] == "none"


def test_cancellation_remains_terminal(temp_db):
    registry = LifecycleRegistry(db=temp_db)
    life = registry.create(task="x")
    registry.transition(life.lifecycle_id, "resolved")
    registry.transition(life.lifecycle_id, "cancelled")
    from agent.action_state import InvalidTransitionError
    import pytest

    with pytest.raises(InvalidTransitionError):
        registry.transition(life.lifecycle_id, "executing")
