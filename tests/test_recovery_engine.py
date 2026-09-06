"""Tests for RecoveryEngine (Milestone 4A)."""

from agent.recovery_engine import (
    MAX_RECOVERY_ATTEMPTS,
    RecoveryEngine,
    STRATEGY_COORDINATE,
    STRATEGY_RE_RESOLVE,
    STRATEGY_RETRY_SELECTOR,
    STRATEGY_STOP,
)


def test_retry_exact_selector():
    engine = RecoveryEngine()
    plan = engine.plan_recovery(
        original_action={"type": "click", "selector": "#btn"},
        verification_status="failed",
    )
    assert plan.allowed is True
    assert plan.strategy == STRATEGY_RETRY_SELECTOR
    assert plan.action["selector"] == "#btn"


def test_re_resolve_target():
    engine = RecoveryEngine()
    # First attempt uses retry
    engine.plan_recovery(
        original_action={"type": "click", "selector": "#btn"},
        verification_status="failed",
    )
    plan = engine.plan_recovery(
        original_action={"type": "click", "selector": "#btn"},
        re_resolved_action={"type": "click", "selector": "#btn2"},
        verification_status="failed",
    )
    assert plan.strategy == STRATEGY_RE_RESOLVE
    assert plan.action["selector"] == "#btn2"


def test_coordinate_fallback():
    engine = RecoveryEngine()
    engine.plan_recovery(
        original_action={"type": "click", "selector": "#a"},
        verification_status="failed",
    )
    engine.plan_recovery(
        original_action={"type": "click", "selector": "#a"},
        re_resolved_action={"type": "click", "selector": "#b"},
        verification_status="failed",
    )
    # Max attempts is 2 — third should stop. Use fresh engine with room:
    engine2 = RecoveryEngine(max_attempts=3)
    engine2.plan_recovery(
        original_action={"type": "click", "selector": "#a"},
        verification_status="failed",
    )
    engine2.plan_recovery(
        original_action={"type": "click", "selector": "#a"},
        re_resolved_action={"type": "click", "selector": "#b"},
        verification_status="failed",
    )
    plan = engine2.plan_recovery(
        original_action={"type": "click", "selector": "#a"},
        coordinate_action={"type": "coordinate_click", "x": 10, "y": 20},
        verification_status="failed",
    )
    assert plan.strategy == STRATEGY_COORDINATE


def test_maximum_two_attempts():
    engine = RecoveryEngine()
    assert engine.max_attempts == MAX_RECOVERY_ATTEMPTS
    engine.plan_recovery(
        original_action={"type": "click", "selector": "#a"},
        verification_status="failed",
    )
    engine.plan_recovery(
        original_action={"type": "click", "selector": "#a"},
        re_resolved_action={"type": "click", "selector": "#b"},
        verification_status="failed",
    )
    plan = engine.plan_recovery(
        original_action={"type": "click", "selector": "#a"},
        fused_action={"type": "click", "selector": "#c"},
        verification_status="failed",
    )
    assert plan.stop is True
    assert plan.strategy == STRATEGY_STOP
    assert len(engine.attempts) == 3  # includes the stop attempt record


def test_blocked_actions_not_retried():
    engine = RecoveryEngine()
    plan = engine.plan_recovery(
        original_action={"type": "type", "selector": "#password"},
        safety_level="blocked",
        verification_status="failed",
    )
    assert plan.allowed is False
    assert plan.strategy == STRATEGY_STOP


def test_destructive_actions_do_not_bypass_confirmation():
    engine = RecoveryEngine()
    plan = engine.plan_recovery(
        original_action={"type": "click", "selector": "#delete"},
        confirmation_required=True,
        confirmation_valid=False,
        verification_status="failed",
    )
    assert plan.allowed is False
    assert "confirmation" in plan.reason.lower()
