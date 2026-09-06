"""Tests for ConfirmationManager (Milestone 4A)."""

import time

import pytest

from agent.confirmation_manager import ConfirmationManager, InMemoryConfirmationStore


@pytest.fixture
def mgr():
    return ConfirmationManager(store=InMemoryConfirmationStore(), default_ttl_seconds=60)


def test_create_confirmation(mgr):
    created = mgr.create(
        action={"type": "click", "selector": "#delete"},
        category="deletion",
        reason="May delete data",
        target={"label": "Delete Account", "type": "button", "source": "dom"},
        task="Delete account",
    )
    assert created["status"] == "requires_confirmation"
    conf = created["confirmation"]
    assert conf["id"].startswith("confirm_")
    assert conf["category"] == "deletion"
    assert conf["target"]["label"] == "Delete Account"
    assert "password" not in str(conf).lower() or "[REDACTED]" in str(conf)


def test_approve_confirmation(mgr):
    created = mgr.create(
        action={"type": "click", "selector": "#delete"},
        category="deletion",
        reason="x",
        target={"label": "Delete", "type": "button"},
    )
    cid = created["confirmation"]["id"]
    result = mgr.approve(cid)
    assert result["status"] == "approved"
    action = mgr.consume(cid)
    assert action["selector"] == "#delete"


def test_cancel_confirmation(mgr):
    created = mgr.create(
        action={"type": "click", "selector": "#x"},
        category="deletion",
        reason="x",
        target={"label": "Delete"},
    )
    cid = created["confirmation"]["id"]
    result = mgr.cancel(cid)
    assert result["status"] == "cancelled"
    assert mgr.consume(cid) is None


def test_expired_confirmation_rejected(mgr):
    created = mgr.create(
        action={"type": "click", "selector": "#x"},
        category="deletion",
        reason="x",
        target={"label": "Delete"},
        expires_in_seconds=1,
    )
    cid = created["confirmation"]["id"]
    # Force expiry
    record = mgr.store.get(cid)
    record["expires_at"] = time.time() - 1
    mgr.store.put(cid, record)
    result = mgr.approve(cid)
    assert result["status"] == "expired"


def test_confirmation_cannot_be_reused(mgr):
    created = mgr.create(
        action={"type": "click", "selector": "#x"},
        category="deletion",
        reason="x",
        target={"label": "Delete"},
    )
    cid = created["confirmation"]["id"]
    assert mgr.approve(cid)["status"] == "approved"
    assert mgr.consume(cid) is not None
    # Replay
    assert mgr.approve(cid)["status"] == "invalid_confirmation"
    assert mgr.consume(cid) is None


def test_unknown_confirmation_rejected(mgr):
    result = mgr.approve("confirm_does_not_exist")
    assert result["status"] == "invalid_confirmation"


def test_frontend_cannot_modify_stored_action(mgr):
    created = mgr.create(
        action={"type": "click", "selector": "#server-truth"},
        category="deletion",
        reason="x",
        target={"label": "Delete"},
    )
    cid = created["confirmation"]["id"]
    mgr.approve(cid)
    action = mgr.consume(cid)
    assert action["selector"] == "#server-truth"
    # Public view never exposes selector for client rewrite
    pending = created["confirmation"]
    assert "selector" not in pending.get("target", {})
