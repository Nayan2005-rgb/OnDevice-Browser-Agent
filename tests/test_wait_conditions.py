"""Wait condition tests (Milestone 4C)."""

from agent.page_state import PageState
from agent.wait_conditions import (
    DEFAULT_WAIT_TIMEOUT_MS,
    MAX_WAIT_TIMEOUT_MS,
    WaitCondition,
    WaitConditionEvaluator,
    clamp_timeout_ms,
)


def test_timeout_constants():
    assert DEFAULT_WAIT_TIMEOUT_MS == 5000
    assert MAX_WAIT_TIMEOUT_MS == 10000


def test_timeout_cannot_exceed_maximum():
    assert clamp_timeout_ms(999999) == MAX_WAIT_TIMEOUT_MS
    assert WaitCondition(kind="element_present", timeout_ms=50000).timeout_ms == MAX_WAIT_TIMEOUT_MS


def test_element_appears():
    ev = WaitConditionEvaluator()
    state = PageState(
        interactive_elements=[
            {"role": "link", "label": "First search result", "type": "a"},
        ],
        element_count=1,
    )
    r = ev.evaluate(
        WaitCondition(kind="element_present", target_hint="search result"),
        page_state=state,
        elapsed_ms=100,
    )
    assert r.satisfied is True


def test_element_disappears():
    ev = WaitConditionEvaluator()
    state = PageState(interactive_elements=[], element_count=0)
    r = ev.evaluate(
        WaitCondition(kind="element_disappeared", target_hint="cookie banner"),
        page_state=state,
        elapsed_ms=50,
    )
    assert r.satisfied is True


def test_url_changes():
    ev = WaitConditionEvaluator()
    prev = PageState(url_signature="aaa", page_signature="s1")
    curr = PageState(url_signature="bbb", page_signature="s2")
    r = ev.evaluate(
        WaitCondition(kind="url_changed", baseline_url_signature="aaa"),
        page_state=curr,
        previous_state=prev,
        elapsed_ms=10,
    )
    assert r.satisfied is True


def test_dialog_appears():
    ev = WaitConditionEvaluator()
    state = PageState(dialog_present=True, dialog_count=1)
    r = ev.evaluate(WaitCondition(kind="dialog_present"), page_state=state, elapsed_ms=0)
    assert r.satisfied is True


def test_signature_changes():
    ev = WaitConditionEvaluator()
    curr = PageState(page_signature="new_sig")
    r = ev.evaluate(
        WaitCondition(kind="page_signature_changed", baseline_page_signature="old_sig"),
        page_state=curr,
        elapsed_ms=0,
    )
    assert r.satisfied is True


def test_timeout_safely_fails():
    ev = WaitConditionEvaluator()
    state = PageState(interactive_elements=[], element_count=0, page_signature="same")
    r = ev.evaluate(
        WaitCondition(
            kind="element_present",
            target_hint="missing thing",
            timeout_ms=1000,
            baseline_page_signature="same",
        ),
        page_state=state,
        elapsed_ms=1000,
    )
    assert r.satisfied is False
    assert r.timed_out is True


def test_results_present():
    ev = WaitConditionEvaluator()
    state = PageState(
        interactive_elements=[{"role": "link", "label": "search results list", "type": "a"}],
        element_count=1,
    )
    r = ev.evaluate(WaitCondition(kind="results_present"), page_state=state, elapsed_ms=0)
    assert r.satisfied is True
