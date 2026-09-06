"""Tests for ActionVerifier (Milestone 4A)."""

from agent.action_verifier import ActionVerifier


def _state(**kwargs):
    base = {
        "url": "https://example.com",
        "visible_elements": [{"tag": "button", "role": "button", "label": "A", "type": ""}],
        "element_count": 1,
        "dialog_present": False,
        "scroll_y": 0,
        "page_signature": "",
        "target_signature": "abc",
        "target_present": True,
        "target_enabled": True,
    }
    base.update(kwargs)
    v = ActionVerifier()
    if not base["page_signature"]:
        base["page_signature"] = v.compute_page_signature(
            url=base["url"],
            elements=base["visible_elements"],
            dialog_present=base["dialog_present"],
        )
    return base


def test_url_changed():
    v = ActionVerifier()
    before = _state()
    after = _state(url="https://example.com/next")
    after["page_signature"] = v.compute_page_signature(
        url=after["url"], elements=after["visible_elements"]
    )
    result = v.verify(before, after, action_type="click", execution_success=True)
    assert result.status == "success"
    assert result.signals["url_changed"] is True


def test_dom_changed():
    v = ActionVerifier()
    before = _state()
    after = _state(
        visible_elements=[
            {"tag": "button", "role": "button", "label": "A"},
            {"tag": "div", "role": "", "label": "Message"},
        ],
        element_count=2,
    )
    after["page_signature"] = v.compute_page_signature(
        url=after["url"], elements=after["visible_elements"]
    )
    result = v.verify(before, after, action_type="click")
    assert result.status == "success"
    assert result.signals["page_changed"] is True


def test_new_dialog_detected():
    v = ActionVerifier()
    before = _state(dialog_present=False)
    after = _state(dialog_present=True)
    after["page_signature"] = v.compute_page_signature(
        url=after["url"],
        elements=after["visible_elements"],
        dialog_present=True,
    )
    result = v.verify(before, after, action_type="click")
    assert result.status == "success"
    assert result.signals["dialog_appeared"] is True


def test_target_state_changed():
    v = ActionVerifier()
    before = _state(target_present=True, target_signature="a")
    after = _state(target_present=False, target_signature="b")
    result = v.verify(before, after, action_type="click")
    assert result.status == "success"
    assert result.signals["target_changed"] is True


def test_no_change_unclear_or_failed():
    v = ActionVerifier()
    before = _state()
    after = dict(before)
    result = v.verify(before, after, action_type="click", execution_success=True)
    assert result.status in ("unclear", "failed")


def test_sensitive_data_not_included():
    v = ActionVerifier()
    state = v.capture_pre_action_state(
        {
            "url": "https://example.com",
            "elements": [
                {
                    "tag": "input",
                    "type": "password",
                    "text": "secret",
                    "value": "hunter2",
                    "sensitive": True,
                    "selector": "#password",
                }
            ],
        }
    )
    blob = str(state.to_dict())
    assert "hunter2" not in blob
    assert "secret" not in blob or "[REDACTED]" in blob
    for el in state.visible_elements:
        assert el.get("label") == "[REDACTED]"
        assert "value" not in el
