"""Deterministic task planner for Milestone 4B.

Produces high-level semantic steps from a user goal. Does not invent
unsupported actions or resolve selectors / coordinates.
"""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from agent.task_plan import (
    PLAN_PLANNING,
    PLAN_RUNNING,
    PLAN_UNSUPPORTED,
    STEP_PENDING,
    TaskPlan,
    TaskStep,
    create_empty_plan,
    looks_sensitive_value,
    new_step_id,
)


# Safe local / demo URLs only for navigate intents
_SAFE_URL_SCHEMES = frozenset({"http", "https", "file"})
_SAFE_HOST_HINTS = (
    "localhost",
    "127.0.0.1",
    "example.com",
    "demo",
)


class TaskPlanner(ABC):
    """Interface for high-level goal → TaskPlan conversion."""

    @abstractmethod
    def create_plan(self, goal: str, context: Optional[Dict[str, Any]] = None) -> TaskPlan:
        ...


class LLMTaskPlanner(TaskPlanner):
    """Placeholder for a future LLM-backed planner.

    Not wired in this milestone — always returns unsupported.
    """

    def create_plan(self, goal: str, context: Optional[Dict[str, Any]] = None) -> TaskPlan:
        plan = create_empty_plan(
            goal or "",
            tab_id=(context or {}).get("tab_id"),
            window_id=(context or {}).get("window_id"),
        )
        plan.transition(PLAN_PLANNING, "LLM planner placeholder")
        plan.unsupported_reason = "LLMTaskPlanner is not implemented in Milestone 4B"
        plan.transition(PLAN_UNSUPPORTED, plan.unsupported_reason)
        return plan


class RuleBasedTaskPlanner(TaskPlanner):
    """Grammar-driven planner for common short browser workflows."""

    def create_plan(self, goal: str, context: Optional[Dict[str, Any]] = None) -> TaskPlan:
        t0 = time.perf_counter()
        context = context or {}
        plan = create_empty_plan(
            goal or "",
            tab_id=context.get("tab_id"),
            window_id=context.get("window_id"),
        )
        plan.transition(PLAN_PLANNING, "Rule-based planning")

        text = (goal or "").strip()
        if not text:
            plan.unsupported_reason = "Empty goal"
            plan.transition(PLAN_UNSUPPORTED, plan.unsupported_reason)
            plan.performance["plan_creation_ms"] = _ms(t0)
            return plan

        steps, reason = self._parse(text)
        plan.performance["plan_creation_ms"] = _ms(t0)
        plan.performance["step_planning_ms"] = plan.performance["plan_creation_ms"]

        if not steps:
            plan.unsupported_reason = reason or "Task cannot be safely converted into a plan"
            plan.transition(PLAN_UNSUPPORTED, plan.unsupported_reason)
            return plan

        # Privacy: block plans that would type sensitive material
        for step in steps:
            if step.action_type == "type" and (
                step.value_redacted
                or (step.value and looks_sensitive_value(step.value))
            ):
                step.value = None
                step.value_redacted = True
                plan.unsupported_reason = "Sensitive typed value blocked from task plan"
                plan.transition(PLAN_UNSUPPORTED, plan.unsupported_reason)
                return plan

        plan.steps = steps
        plan.transition(PLAN_RUNNING, f"Created {len(steps)} step(s)")
        return plan

    def _parse(self, text: str) -> Tuple[List[TaskStep], Optional[str]]:
        # Order matters: more specific compound patterns first
        parsers = (
            self._parse_search_and_delete,
            self._parse_search_and_open_first,
            self._parse_search,
            self._parse_find,
            self._parse_type_into,
            self._parse_scroll_and_click,
            self._parse_navigate,
            self._parse_click,
            self._parse_scroll,
            self._parse_wait,
        )
        for parser in parsers:
            steps = parser(text)
            if steps is not None:
                return steps, None
        return [], "Unsupported goal grammar for Milestone 4B"

    # ------------------------------------------------------------------
    # Parsers (return None if no match, [] should not be used for miss)
    # ------------------------------------------------------------------

    def _parse_search_and_delete(self, text: str) -> Optional[List[TaskStep]]:
        m = re.match(
            r"(?is)^\s*(?:search\s+for|find)\s+(.+?)\s+and\s+delete\s+(?:the\s+)?(?:first\s+)?(?:result|item)\s*$",
            text,
        )
        if not m:
            return None
        query = _clean_query(m.group(1))
        return [
            _type_step(0, "Enter the search query", "search box", query),
            _click_step(1, "Submit the search", "search button"),
            _wait_step(2, "Wait for search results", "page_changed_or_results_visible"),
            _click_step(3, "Open the first matching result", "first search result"),
            _click_step(4, "Delete the item", "delete"),
        ]

    def _parse_search_and_open_first(self, text: str) -> Optional[List[TaskStep]]:
        m = re.match(
            r"(?is)^\s*(?:search\s+for|find)\s+(.+?)\s+and\s+open\s+(?:the\s+)?first\s+(?:result|item|match)\s*$",
            text,
        )
        if not m:
            return None
        query = _clean_query(m.group(1))
        return [
            _type_step(0, "Enter the search query", "search box", query),
            _click_step(1, "Submit the search", "search button"),
            _wait_step(2, "Wait for search results", "page_changed_or_results_visible"),
            _click_step(3, "Open the first matching result", "first search result"),
        ]

    def _parse_search(self, text: str) -> Optional[List[TaskStep]]:
        m = re.match(r"(?is)^\s*search\s+for\s+(.+?)\s*$", text)
        if not m:
            return None
        query = _clean_query(m.group(1))
        return [
            _type_step(0, "Enter the search query", "search box", query),
            _click_step(1, "Submit the search", "search button"),
            _wait_step(2, "Wait for search results", "page_changed_or_results_visible"),
        ]

    def _parse_find(self, text: str) -> Optional[List[TaskStep]]:
        m = re.match(r"(?is)^\s*find\s+(.+?)\s*$", text)
        if not m:
            return None
        # Bare "find X" without click/open → treat as search-like
        item = _clean_query(m.group(1))
        if re.search(r"(?i)\b(click|open|delete|type|scroll|navigate)\b", item):
            return None
        return [
            _type_step(0, f"Enter query for {item}", "search box", item),
            _click_step(1, "Submit the search", "search button"),
            _wait_step(2, "Wait for results", "target_appears"),
        ]

    def _parse_type_into(self, text: str) -> Optional[List[TaskStep]]:
        m = re.match(
            r"(?is)^\s*type\s+[\"']?(.+?)[\"']?\s+into\s+(.+?)\s*$",
            text,
        )
        if not m:
            return None
        value = m.group(1).strip().strip("\"'")
        target = m.group(2).strip().strip("\"'")
        return [_type_step(0, f"Type into {target}", target, value)]

    def _parse_scroll_and_click(self, text: str) -> Optional[List[TaskStep]]:
        m = re.match(
            r"(?is)^\s*scroll\s+(down|up)?\s*(?:and\s+)?click\s+(.+?)\s*$",
            text,
        )
        if not m:
            return None
        direction = (m.group(1) or "down").lower()
        target = m.group(2).strip().strip("\"'")
        return [
            _scroll_step(0, f"Scroll {direction}", direction),
            _click_step(1, f"Click {target}", target),
        ]

    def _parse_navigate(self, text: str) -> Optional[List[TaskStep]]:
        m = re.match(
            r"(?is)^\s*(?:navigate\s+to|go\s+to|open\s+(?:url|page))\s+(.+?)\s*$",
            text,
        )
        if not m:
            return None
        raw = m.group(1).strip().strip("\"'")
        if not _is_safe_url_or_local(raw):
            # Matched navigate grammar but URL is not in the safe allowlist.
            return []
        return [
            TaskStep(
                step_id=new_step_id(0),
                description=f"Navigate to {raw}",
                action_type="navigate",
                url=raw,
                status=STEP_PENDING,
            )
        ]

    def _parse_click(self, text: str) -> Optional[List[TaskStep]]:
        m = re.match(
            r"(?is)^\s*(?:click|press|tap)\s+(?:on\s+)?(.+?)\s*$",
            text,
        )
        if not m:
            # Also allow bare destructive labels like "Delete Demo Item"
            if re.search(r"(?i)\b(delete|remove|pay|purchase|confirm\s+payment)\b", text):
                return [_click_step(0, text.strip(), text.strip())]
            return None
        target = m.group(1).strip().strip("\"'")
        return [_click_step(0, f"Click {target}", target)]

    def _parse_scroll(self, text: str) -> Optional[List[TaskStep]]:
        m = re.match(r"(?is)^\s*scroll\s+(down|up)\s*$", text)
        if not m:
            return None
        direction = m.group(1).lower()
        return [_scroll_step(0, f"Scroll {direction}", direction)]

    def _parse_wait(self, text: str) -> Optional[List[TaskStep]]:
        m = re.match(
            r"(?is)^\s*wait\s+(?:for\s+)?(.+?)\s*$",
            text,
        )
        if not m:
            return None
        cond_raw = m.group(1).strip().lower()
        condition = "page_changed"
        if "result" in cond_raw or "appear" in cond_raw or "target" in cond_raw:
            condition = "target_appears"
        elif "navigat" in cond_raw or "url" in cond_raw:
            condition = "navigation"
        elif "dialog" in cond_raw:
            condition = "dialog"
        return [_wait_step(0, f"Wait for {cond_raw}", condition)]


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 3)


def _clean_query(raw: str) -> str:
    return raw.strip().strip("\"'").rstrip(".")


def _is_safe_url_or_local(raw: str) -> bool:
    text = (raw or "").strip()
    if not text:
        return False
    # Allow known local demo page names without scheme
    if re.match(r"(?i)^[\w\-]+\.html$", text):
        return True
    if "://" not in text:
        # Relative / known demo paths
        return bool(re.match(r"(?i)^[\w./\-]+$", text)) and not text.startswith("//")
    try:
        parsed = urlparse(text)
    except Exception:
        return False
    if parsed.scheme.lower() not in _SAFE_URL_SCHEMES:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return parsed.scheme == "file"
    return any(h in host for h in _SAFE_HOST_HINTS)


def _type_step(index: int, description: str, hint: str, value: str) -> TaskStep:
    redacted = looks_sensitive_value(value)
    return TaskStep(
        step_id=new_step_id(index),
        description=description,
        action_type="type",
        target_hint=hint,
        value=None if redacted else value,
        value_redacted=redacted,
        status=STEP_PENDING,
    )


def _click_step(index: int, description: str, hint: str) -> TaskStep:
    return TaskStep(
        step_id=new_step_id(index),
        description=description,
        action_type="click",
        target_hint=hint,
        status=STEP_PENDING,
    )


def _scroll_step(index: int, description: str, direction: str) -> TaskStep:
    return TaskStep(
        step_id=new_step_id(index),
        description=description,
        action_type="scroll",
        direction=direction,
        amount=500,
        status=STEP_PENDING,
    )


def _wait_step(index: int, description: str, condition: str) -> TaskStep:
    return TaskStep(
        step_id=new_step_id(index),
        description=description,
        action_type="wait",
        condition=condition,
        timeout_ms=DEFAULT_WAIT_TIMEOUT_MS,
        status=STEP_PENDING,
    )


DEFAULT_WAIT_TIMEOUT_MS = 5000
MAX_WAIT_TIMEOUT_MS = 10000


def get_default_planner() -> TaskPlanner:
    return RuleBasedTaskPlanner()
