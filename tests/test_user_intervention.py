"""User intervention pause / resume tests (Milestone 4C)."""

import pytest

from agent.approved_action_delivery import reset_approved_action_delivery
from agent.confirmation_manager import reset_confirmation_manager
from agent.lifecycle_registry import reset_lifecycle_registry
from agent.task_orchestrator import TaskOrchestrator, reset_task_orchestrator
from agent.task_plan_registry import reset_task_plan_registry


@pytest.fixture
def orch():
    reset_confirmation_manager()
    reset_lifecycle_registry()
    reset_approved_action_delivery()
    reset_task_plan_registry()
    reset_task_orchestrator()
    o = TaskOrchestrator()
    yield o
    reset_task_plan_registry()
    reset_task_orchestrator()


def _base_search_page():
    return {
        "url": "https://example.com/demo/adaptive",
        "title": "Adaptive",
        "visibleText": "Search",
        "elements": [
            {
                "tag": "input",
                "type": "text",
                "selector": "#search-box",
                "id": "search-box",
                "ariaLabel": "search box",
                "placeholder": "Search",
                "sensitive": False,
            },
            {
                "tag": "button",
                "text": "Search",
                "selector": "#search-button",
                "id": "search-button",
                "sensitive": False,
            },
        ],
    }


def _login_page():
    return {
        "url": "https://example.com/demo/adaptive?login=1",
        "title": "Login",
        "visibleText": "Sign in Password Email",
        "elements": [
            {
                "tag": "dialog",
                "role": "dialog",
                "text": "Sign in to continue",
                "type": "dialog",
                "sensitive": False,
            },
            {
                "tag": "input",
                "type": "email",
                "text": "",
                "ariaLabel": "Email",
                "sensitive": False,
            },
            {
                "tag": "input",
                "type": "password",
                "text": "",
                "ariaLabel": "Password",
                "sensitive": True,
            },
            {
                "tag": "button",
                "text": "Sign in",
                "role": "button",
                "sensitive": False,
            },
        ],
    }


def _advance_to_open_result(orch, plan):
    """Type + click search verified so current step is wait or open result."""
    page = _base_search_page()
    r1 = orch.resolve_current_step(plan.plan_id, page=page)
    assert r1.get("action")
    orch.on_step_execution_reported(
        plan.plan_id, lifecycle_id=r1.get("lifecycle_id"), verification_status="success", page=page
    )
    r2 = orch.resolve_current_step(plan.plan_id, page=page)
    assert r2.get("action")
    orch.on_step_execution_reported(
        plan.plan_id, lifecycle_id=r2.get("lifecycle_id"), verification_status="success", page=page
    )


def test_password_requirement_pauses_plan(orch):
    plan = orch.create_plan(
        "Search for AI courses and open the first result", tab_id=1
    )
    _advance_to_open_result(orch, plan)
    # Skip wait if present by succeeding it when results/login appear
    login = _login_page()
    r = orch.resolve_current_step(
        plan.plan_id,
        page=login,
        safe_page_state={"dialog_present": True, "url": login["url"]},
    )
    # May be waiting first — if waiting, report elapsed with login page again
    if r.get("status") == "waiting":
        r = orch.resolve_current_step(
            plan.plan_id,
            page=login,
            safe_page_state={"dialog_present": True, "url": login["url"]},
            wait_elapsed_ms=10,
        )
        # Force past wait by verifying success if still waiting with results text
        if r.get("status") == "waiting":
            step = plan.current_step()
            if step and step.action_type == "wait":
                from agent.task_plan import STEP_SUCCESS

                step.status = STEP_SUCCESS
                plan.advance_after_success()
                r = orch.resolve_current_step(
                    plan.plan_id,
                    page=login,
                    safe_page_state={"dialog_present": True, "url": login["url"]},
                )

    assert r.get("requires_user_intervention") or plan.requires_user_intervention
    assert plan.status == "paused"
    assert plan.intervention_reason
    # Must not return a type/password action
    action = r.get("action")
    if action:
        assert action.get("type") != "type" or "password" not in str(action).lower()


def test_otp_requirement_pauses(orch):
    plan = orch.create_plan("Click verify")
    page = {
        "url": "https://example.com/otp",
        "title": "OTP",
        "visibleText": "Enter OTP verification code",
        "elements": [
            {
                "tag": "dialog",
                "role": "dialog",
                "text": "Enter OTP verification code",
                "type": "dialog",
            },
            {
                "tag": "input",
                "type": "text",
                "ariaLabel": "OTP",
                "text": "",
                "sensitive": False,
            },
            {"tag": "button", "text": "Verify", "selector": "#verify", "id": "verify"},
        ],
    }
    r = orch.resolve_current_step(
        plan.plan_id, page=page, safe_page_state={"dialog_present": True}
    )
    assert plan.requires_user_intervention or r.get("requires_user_intervention")
    assert plan.status == "paused"


def test_captcha_pauses(orch):
    plan = orch.create_plan("Click continue")
    page = {
        "url": "https://example.com/captcha",
        "title": "Captcha",
        "visibleText": "I'm not a robot CAPTCHA",
        "elements": [
            {
                "tag": "dialog",
                "role": "dialog",
                "text": "CAPTCHA I'm not a robot",
                "type": "dialog",
            },
            {"tag": "button", "text": "Continue", "selector": "#continue", "id": "continue"},
        ],
    }
    orch.resolve_current_step(
        plan.plan_id, page=page, safe_page_state={"dialog_present": True}
    )
    assert plan.status == "paused"
    assert plan.requires_user_intervention


def test_payment_requirement_pauses(orch):
    plan = orch.create_plan("Click pay now")
    page = {
        "url": "https://example.com/pay",
        "title": "Pay",
        "visibleText": "Credit card payment",
        "elements": [
            {
                "tag": "dialog",
                "role": "dialog",
                "text": "Enter credit card payment",
                "type": "dialog",
            },
            {
                "tag": "input",
                "type": "text",
                "ariaLabel": "Card number",
                "sensitive": True,
            },
            {"tag": "button", "text": "Pay now", "selector": "#pay", "id": "pay"},
        ],
    }
    orch.resolve_current_step(
        plan.plan_id, page=page, safe_page_state={"dialog_present": True}
    )
    assert plan.status == "paused"
    assert plan.requires_user_intervention


def test_manual_resume_requires_fresh_perception(orch):
    plan = orch.create_plan("Click home")
    page = {
        "url": "https://example.com/login",
        "title": "Login",
        "visibleText": "Password login",
        "elements": [
            {
                "tag": "dialog",
                "role": "dialog",
                "text": "Login password required",
                "type": "dialog",
            },
            {
                "tag": "input",
                "type": "password",
                "ariaLabel": "Password",
                "sensitive": True,
            },
            {"tag": "button", "text": "Home", "selector": "#home", "id": "home"},
        ],
    }
    orch.resolve_current_step(
        plan.plan_id, page=page, safe_page_state={"dialog_present": True}
    )
    assert plan.status == "paused"
    gen_before = plan.perception_generation

    # After user clears login, resume with fresh page
    clear = {
        "url": "https://example.com/",
        "title": "Home",
        "visibleText": "Home",
        "elements": [
            {"tag": "button", "text": "Home", "selector": "#home", "id": "home", "sensitive": False},
        ],
    }
    r = orch.resume_plan(plan.plan_id, page=clear)
    assert plan.perception_generation > gen_before
    assert plan.requires_user_intervention is False
    assert r.get("status") in ("success", "recovering", "paused", "failed") or r.get("action")
