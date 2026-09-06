"""Bootstrap durable action lifecycle stores (Milestone 5B)."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from agent.approved_action_delivery import configure_approved_action_delivery
from agent.confirmation_manager import configure_confirmation_manager
from agent.durable_stores import SqliteConfirmationStore
from agent.lifecycle_registry import configure_lifecycle_registry
from storage.database import Database, get_database


def configure_durable_lifecycle(db: Optional[Database] = None) -> Dict[str, Any]:
    """Wire confirmation / delivery / lifecycle to SQLite-backed stores."""
    database = db if db is not None else get_database()
    store = SqliteConfirmationStore(database)
    configure_confirmation_manager(store)
    delivery = configure_approved_action_delivery(database)
    registry = configure_lifecycle_registry(database)
    return {
        "confirmation_store": store,
        "delivery": delivery,
        "registry": registry,
        "db": database,
    }


def hydrate_durable_lifecycle(db: Optional[Database] = None) -> Dict[str, Any]:
    """Restore unfinished confirmations / deliveries / lifecycles after restart.

    Uncertain mid-flight states become recovery_required — never auto-executed.
    """
    t0 = time.perf_counter()
    configured = configure_durable_lifecycle(db)
    store: SqliteConfirmationStore = configured["confirmation_store"]
    delivery = configured["delivery"]
    registry = configured["registry"]

    conf_count = store.hydrate()
    delivery_stats = delivery.hydrate()
    life_stats = registry.hydrate()

    total_ms = round((time.perf_counter() - t0) * 1000, 3)
    return {
        "confirmations_restored": conf_count,
        "deliveries": delivery_stats,
        "lifecycles": life_stats,
        "crash_recovery_ms": total_ms,
        "total_action_recovery_ms": total_ms,
    }
