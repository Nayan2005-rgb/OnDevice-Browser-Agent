"""Page state and signature tests (Milestone 4C)."""

from agent.page_state import PageState, build_page_state, compute_page_signature


def _page(elements, url="https://example.com/demo"):
    return {
        "url": url,
        "title": "Demo",
        "visibleText": "demo",
        "elements": elements,
    }


def test_deterministic_page_signature():
    els = [
        {"tag": "button", "text": "Search", "role": "button", "type": "button", "sensitive": False},
        {"tag": "input", "text": "", "role": "textbox", "type": "text", "ariaLabel": "search box", "sensitive": False},
    ]
    a = build_page_state(page=_page(els), generation=1)
    b = build_page_state(page=_page(els), generation=1)
    assert a.page_signature == b.page_signature
    assert len(a.page_signature) == 32


def test_no_raw_screenshot_stored():
    state = build_page_state(
        page=_page([{"tag": "button", "text": "Go", "sensitive": False}]),
        observation={"screenshot": "SHOULD_NOT_APPEAR", "ui_elements": []},
    )
    data = state.to_dict()
    assert "screenshot" not in data
    assert "raw_screenshot" not in data
    blob = str(data)
    assert "SHOULD_NOT_APPEAR" not in blob


def test_no_raw_pii_stored():
    els = [
        {
            "tag": "input",
            "type": "password",
            "text": "hunter2",
            "label": "Password",
            "sensitive": True,
            "role": "textbox",
        },
        {
            "tag": "input",
            "type": "email",
            "text": "user@example.com",
            "label": "[EMAIL]",
            "sensitive": True,
        },
    ]
    state = build_page_state(page=_page(els))
    blob = str(state.to_dict()).lower()
    assert "hunter2" not in blob
    assert "user@example.com" not in blob
    for el in state.interactive_elements:
        if el.get("sensitive"):
            assert el.get("label") == "[REDACTED]"


def test_dialog_state_captured():
    els = [
        {"tag": "dialog", "role": "dialog", "text": "Cookie consent", "type": "dialog", "sensitive": False},
        {"tag": "button", "text": "Accept cookies", "role": "button", "sensitive": False},
    ]
    state = build_page_state(
        page=_page(els),
        safe_page_state={"dialog_present": True},
    )
    assert state.dialog_present is True
    assert state.dialog_count >= 1


def test_safe_visual_summary():
    vmap = {
        "summary": {
            "total_elements": 12,
            "buttons": 4,
            "inputs": 1,
            "links": 3,
            "dialogs": 0,
            "dom_only_elements": 5,
            "vision_only_elements": 2,
            "fused_elements": 5,
        },
        "layout": {"groups": [{"id": "g1"}, {"id": "g2"}]},
        "elements": [],
        "privacy_safe": True,
    }
    state = build_page_state(page=_page([]), visual_ui_map=vmap)
    assert state.visual_summary.get("total_elements") == 12
    assert state.layout_group_count == 2
    assert "screenshot" not in state.visual_summary


def test_signature_changes_with_structure():
    a = compute_page_signature(
        interactive_elements=[{"role": "button", "label": "Search", "sensitive": False}],
        dialog_present=False,
    )
    b = compute_page_signature(
        interactive_elements=[{"role": "button", "label": "Search", "sensitive": False}],
        dialog_present=True,
        dialog_count=1,
    )
    assert a != b


def test_page_state_public_view_omits_labels():
    state = PageState(
        generation=2,
        page_signature="abc",
        interactive_elements=[{"role": "button", "label": "Secretish"}],
        element_count=1,
    )
    pub = state.public_view()
    assert "interactive_elements" not in pub
    assert pub["element_count"] == 1
