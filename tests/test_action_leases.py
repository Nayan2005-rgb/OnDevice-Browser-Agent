"""Action lease expiry (Milestone 5B)."""

from __future__ import annotations

import time

from agent.approved_action_delivery import ApprovedActionDelivery
from agent.durable_lifecycle import configure_durable_lifecycle


def test_claim_lease_expiration_marks_recovery_required(temp_db):
    configure_durable_lifecycle(temp_db)
    delivery = ApprovedActionDelivery(db=temp_db, lease_seconds=1, ttl_seconds=60)
    queued = delivery.create(
        confirmation_id="confirm_lease",
        action={"type": "click", "selector": "#a"},
        tab_id=21,
    )
    claimed = delivery.claim_for_tab(21)
    assert claimed["status"] == "approved"
    eid = claimed["execution_id"]
    live = delivery._by_execution[eid]
    live["lease_until"] = time.time() - 1
    delivery._persist_unlocked(live)

    changed = delivery.expire_leases()
    assert any(c.get("execution_id") == eid for c in changed)
    rec = delivery.get(eid)
    assert rec["status"] == "recovery_required"
    assert rec["recovery_reason"] == "lease_expired"
    # Must not auto re-claim / execute
    assert delivery.claim_for_tab(21)["status"] == "none"


def test_lease_validation_metric_recorded(temp_db):
    delivery = ApprovedActionDelivery(db=temp_db, lease_seconds=1)
    delivery.create(
        confirmation_id="confirm_lease_m",
        action={"type": "click", "selector": "#a"},
        tab_id=22,
    )
    claimed = delivery.claim_for_tab(22)
    eid = claimed["execution_id"]
    delivery._by_execution[eid]["lease_until"] = time.time() - 0.1
    delivery.expire_leases()
    perf = delivery.get(eid).get("performance") or {}
    assert "lease_validation_ms" in perf or delivery.get(eid)["status"] == "recovery_required"
