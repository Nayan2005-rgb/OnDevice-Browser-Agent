"""Recovery / durable persistence privacy (Milestone 5B)."""

from __future__ import annotations

import pytest

from privacy.persistence_validator import PersistencePrivacyError, validate_for_persistence
from storage.repositories.action_delivery_repository import ActionDeliveryRepository
from storage.repositories.action_lifecycle_repository import ActionLifecycleRepository
from storage.repositories.confirmation_repository import ConfirmationRepository


def test_raw_pii_blocked_from_confirmation_persistence(temp_db):
    repo = ConfirmationRepository(temp_db)
    with pytest.raises(PersistencePrivacyError):
        validate_for_persistence({"password": "secret123"}, context="test")
    # Action with password key is stripped / fail-closed to safe fallback
    out = repo.upsert(
        {
            "confirmation_id": "c_pii",
            "status": "pending",
            "action": {"type": "type", "password": "hunter2", "selector": "#pwd"},
            "created_at": 1.0,
            "expires_at": 9999999999.0,
        }
    )
    assert "password" not in (out.get("action") or {})


def test_screenshots_blocked_from_lifecycle_persistence(temp_db):
    repo = ActionLifecycleRepository(temp_db)
    with pytest.raises((PersistencePrivacyError, Exception)):
        repo.upsert(
            {
                "lifecycle_id": "life_shot",
                "state": "created",
                "payload": {"screenshot": "data:image/png;base64,AAAA"},
                "timeline": [],
            }
        )


def test_delivery_rejects_otp_key(temp_db):
    repo = ActionDeliveryRepository(temp_db)
    out = repo.upsert(
        {
            "execution_id": "exec_otp",
            "status": "approved",
            "action": {"type": "type", "otp": "123456", "selector": "#otp"},
            "created_at": 1.0,
            "expires_at": 9999999999.0,
            "updated_at": 1.0,
        }
    )
    assert "otp" not in (out.get("action") or {})
