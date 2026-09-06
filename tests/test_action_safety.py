"""Tests for ActionRiskClassifier (Milestone 4A)."""

from agent.action_safety import ActionRiskClassifier


def test_safe_action_learn_more():
    c = ActionRiskClassifier()
    result = c.classify(
        task="Click Learn More",
        action="click",
        target_text="Learn More",
        element_type="button",
    )
    assert result.level == "safe"


def test_deletion_requires_confirmation():
    c = ActionRiskClassifier()
    result = c.classify(
        task="Click Delete Account",
        action="click",
        target_text="Delete Account",
        element_type="button",
    )
    assert result.level == "confirmation_required"
    # "Delete Account" is account_change; generic delete → deletion
    assert result.category in ("deletion", "account_change")


def test_generic_delete_category():
    c = ActionRiskClassifier()
    result = c.classify(
        task="Click Delete Item",
        action="click",
        target_text="Delete Item",
        element_type="button",
    )
    assert result.level == "confirmation_required"
    assert result.category == "deletion"


def test_remove_project_requires_confirmation():
    c = ActionRiskClassifier()
    result = c.classify(
        task="Click Remove Project",
        action="click",
        target={"text": "Remove Project", "type": "button"},
    )
    assert result.level == "confirmation_required"
    assert result.category == "deletion"


def test_checkout_requires_confirmation():
    c = ActionRiskClassifier()
    result = c.classify(task="Click Checkout", action="click", target_text="Checkout")
    assert result.level == "confirmation_required"
    assert result.category in ("payment",)


def test_pay_now_requires_confirmation():
    c = ActionRiskClassifier()
    result = c.classify(task="Click Pay Now", action="click", target_text="Pay Now")
    assert result.level == "confirmation_required"
    assert result.category == "payment"


def test_password_blocked():
    c = ActionRiskClassifier()
    result = c.classify(
        task="Type secret in password",
        action="type",
        element_type="password",
        sensitive=True,
    )
    assert result.level == "blocked"
    assert result.category == "sensitive_input"


def test_sensitive_target_blocked():
    c = ActionRiskClassifier()
    result = c.classify(
        task="Click the password field",
        action="click",
        target={"type": "password", "sensitive": True, "text": ""},
    )
    assert result.level == "blocked"
