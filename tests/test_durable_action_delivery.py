"""Durable approved action delivery (Milestone 5B)."""

from __future__ import annotations

from agent.approved_action_delivery import ApprovedActionDelivery
from agent.durable_lifecycle import configure_durable_lifecycle, hydrate_durable_lifecycle


def test_approved_action_survives_restart(temp_db):
    configure_durable_lifecycle(temp_db)
    from agent.approved_action_delivery import get_approved_action_delivery

    delivery = get_approved_action_delivery()
    queued = delivery.create(
        confirmation_id="confirm_persist",
        action={"type": "click", "selector": "#ok"},
        tab_id=42,
        lifecycle_id="life_1",
        task="Click OK",
        category="safe",
    )
    eid = queued["execution_id"]

    stats = hydrate_durable_lifecycle(temp_db)
    assert stats["deliveries"]["restored"] >= 1
    delivery2 = get_approved_action_delivery()
    rec = delivery2.get(eid)
    assert rec is not None
    assert rec["status"] in ("approved", "waiting_for_browser")
    assert rec["tab_id"] == 42
    claimed = delivery2.claim_for_tab(42)
    assert claimed["status"] == "approved"
    assert claimed["action"]["selector"] == "#ok"


def test_wrong_tab_cannot_claim(temp_db):
    configure_durable_lifecycle(temp_db)
    from agent.approved_action_delivery import get_approved_action_delivery

    delivery = get_approved_action_delivery()
    delivery.create(
        confirmation_id="confirm_tab",
        action={"type": "click", "selector": "#a"},
        tab_id=7,
    )
    assert delivery.claim_for_tab(999)["status"] == "none"
    assert delivery.claim_for_tab(7)["status"] == "approved"


def test_two_extensions_cannot_claim_same_action(temp_db):
    configure_durable_lifecycle(temp_db)
    from agent.approved_action_delivery import get_approved_action_delivery

    delivery = get_approved_action_delivery()
    delivery.create(
        confirmation_id="confirm_once",
        action={"type": "click", "selector": "#a"},
        tab_id=8,
    )
    a = delivery.claim_for_tab(8)
    b = delivery.claim_for_tab(8)
    assert a["status"] == "approved"
    assert b["status"] == "none"


def test_cancelled_delivery_never_claims(temp_db):
    configure_durable_lifecycle(temp_db)
    from agent.approved_action_delivery import get_approved_action_delivery

    delivery = get_approved_action_delivery()
    delivery.create(
        confirmation_id="confirm_cancel",
        action={"type": "click", "selector": "#a"},
        tab_id=9,
    )
    delivery.cancel_for_confirmation("confirm_cancel")
    assert delivery.claim_for_tab(9)["status"] == "none"


def test_old_coordinates_not_trusted_after_restart(temp_db):
    configure_durable_lifecycle(temp_db)
    from agent.approved_action_delivery import get_approved_action_delivery
    from agent.action_recovery import strip_trusted_coordinates

    delivery = get_approved_action_delivery()
    delivery.create(
        confirmation_id="confirm_coords",
        action={"type": "click", "selector": "#a", "x": 10, "y": 20},
        tab_id=10,
    )
    hydrate_durable_lifecycle(temp_db)
    delivery2 = get_approved_action_delivery()
    claimed = delivery2.claim_for_tab(10)
    stripped = strip_trusted_coordinates(claimed.get("action"))
    assert stripped.get("coordinates_trusted") is False
    assert "x" not in stripped or stripped.get("coordinates_trusted") is False
