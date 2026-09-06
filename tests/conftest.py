"""Shared fixtures for Milestone 5A/5B persistence tests.

Fixtures are opt-in by name — existing tests are unaffected unless they request them.
"""

from __future__ import annotations

import pytest

from agent.approved_action_delivery import reset_approved_action_delivery
from agent.confirmation_manager import reset_confirmation_manager
from agent.lifecycle_registry import reset_lifecycle_registry
from agent.operator_control import reset_operator_control
from agent.session_manager import reset_session_manager
from agent.task_orchestrator import reset_task_orchestrator
from agent.task_plan_registry import reset_task_plan_registry
from storage import database as database_mod
from storage.database import reset_database


@pytest.fixture
def temp_db(tmp_path):
    db_path = tmp_path / "test_agent.db"
    db = reset_database(db_path)
    reset_session_manager(db)
    reset_operator_control()
    yield db
    # Detach singletons so later tests do not retain deleted temp DB paths
    reset_confirmation_manager()
    reset_lifecycle_registry()
    reset_approved_action_delivery()
    database_mod._db = None
    database_mod._configured_path = None


@pytest.fixture
def session_client(temp_db):
    """Flask test client with isolated temp DB and durable lifecycle stores."""
    from agent.durable_lifecycle import configure_durable_lifecycle

    configure_durable_lifecycle(temp_db)
    reset_task_plan_registry()
    reset_task_orchestrator()
    reset_session_manager(temp_db)
    reset_operator_control()

    from server.app import create_app

    app = create_app(hydrate_sessions=False)
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@pytest.fixture
def durable_client(temp_db):
    """Flask test client with durable confirmation / delivery / lifecycle."""
    from agent.durable_lifecycle import configure_durable_lifecycle

    configure_durable_lifecycle(temp_db)
    reset_task_plan_registry()
    reset_task_orchestrator()
    reset_session_manager(temp_db)
    reset_operator_control()

    from server.app import create_app

    app = create_app(hydrate_sessions=False)
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client
