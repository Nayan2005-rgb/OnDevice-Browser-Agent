"""Page change detector tests (Milestone 4C)."""

from agent.page_change_detector import PageChangeDetector, PageChangeLevel
from agent.page_state import PageState


def _state(**kwargs):
    base = dict(
        generation=1,
        url_signature="url_aaaa",
        page_signature="sig_aaaa",
        interactive_elements=[
            {"role": "button", "label": "Search", "type": "button", "sensitive": False},
            {"role": "textbox", "label": "search box", "type": "text", "sensitive": False},
        ],
        element_count=2,
        dialog_present=False,
        dialog_count=0,
        visual_summary={"total_elements": 10, "dialogs": 0, "buttons": 1},
        role_distribution={"button": 1, "textbox": 1},
        layout_group_count=2,
    )
    base.update(kwargs)
    return PageState(**base)


def test_identical_state_none():
    det = PageChangeDetector()
    a = _state()
    b = _state(generation=2)
    r = det.compare(a, b)
    assert r.level == PageChangeLevel.NONE.value
    assert r.changed is False
    assert r.score <= 0.10


def test_minor_count_change():
    det = PageChangeDetector()
    a = _state()
    b = _state(
        generation=2,
        page_signature="sig_bbbb",
        element_count=3,
        interactive_elements=a.interactive_elements
        + [{"role": "link", "label": "Home", "type": "a", "sensitive": False}],
        role_distribution={"button": 1, "textbox": 1, "link": 1},
        visual_summary={"total_elements": 11, "dialogs": 0, "buttons": 1},
    )
    r = det.compare(a, b)
    assert r.changed is True
    assert r.level in (
        PageChangeLevel.MINOR.value,
        PageChangeLevel.MODERATE.value,
    )


def test_major_element_change_moderate_or_structural():
    det = PageChangeDetector()
    a = _state()
    b = _state(
        generation=2,
        page_signature="sig_major",
        element_count=20,
        interactive_elements=[
            {"role": "link", "label": f"Item {i}", "type": "a", "sensitive": False}
            for i in range(20)
        ],
        role_distribution={"link": 20},
        visual_summary={
            "total_elements": 40,
            "dialogs": 0,
            "buttons": 0,
            "links": 20,
            "dom_only_elements": 30,
            "vision_only_elements": 5,
            "fused_elements": 5,
        },
        layout_group_count=8,
    )
    r = det.compare(a, b)
    assert r.level in (
        PageChangeLevel.MODERATE.value,
        PageChangeLevel.STRUCTURAL.value,
    )


def test_dialog_appears_structural():
    det = PageChangeDetector()
    a = _state()
    b = _state(
        generation=2,
        page_signature="sig_dialog",
        dialog_present=True,
        dialog_count=1,
        element_count=4,
        visual_summary={"total_elements": 14, "dialogs": 1, "buttons": 2},
    )
    r = det.compare(a, b)
    assert "dialog_appeared" in r.reasons
    assert r.level in (
        PageChangeLevel.STRUCTURAL.value,
        PageChangeLevel.MODERATE.value,
    )
    assert r.score >= 0.25


def test_url_changes_navigation():
    det = PageChangeDetector()
    a = _state()
    b = _state(generation=2, url_signature="url_bbbb", page_signature="sig_nav")
    r = det.compare(a, b)
    assert r.level == PageChangeLevel.NAVIGATION.value
    assert "url_changed" in r.reasons


def test_deterministic_results():
    det = PageChangeDetector()
    a = _state()
    b = _state(
        generation=2,
        dialog_present=True,
        dialog_count=1,
        page_signature="sig_x",
    )
    r1 = det.compare(a, b)
    r2 = det.compare(a, b)
    assert r1.to_dict()["level"] == r2.to_dict()["level"]
    assert r1.to_dict()["score"] == r2.to_dict()["score"]
    assert r1.to_dict()["reasons"] == r2.to_dict()["reasons"]
