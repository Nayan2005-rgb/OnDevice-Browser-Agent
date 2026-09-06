"""Decision engine: takes a perception observation and the current task
goal, and decides the next high-level action.

Milestone 3B: hybrid DOM + Visual UI Map targeting via TargetResolver.
This milestone uses a temporary rule-based engine (no external LLM).
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

from agent.action_safety import ActionRiskClassifier
from agent.target_resolver import TargetResolver


class DecisionEngine:
    def __init__(
        self,
        llm_client=None,
        target_resolver: TargetResolver | None = None,
        safety_classifier: ActionRiskClassifier | None = None,
    ):
        self.llm_client = llm_client
        self.target_resolver = target_resolver or TargetResolver()
        self.safety_classifier = safety_classifier or ActionRiskClassifier()

    def decide_next_action(self, observation: Dict, goal: str, history: List[Dict]) -> Dict:
        """Return a structured action decision.

        Rule-based for this milestone. When an LLM is wired later, it can
        replace the body of this method while keeping the same return shape.
        """
        t0 = time.perf_counter()
        task = (goal or "").strip()
        if not task:
            return {
                "action": None,
                "target": None,
                "reasoning": "Empty task",
                "status": "no_action",
            }

        # Destructive intents require confirmation (never auto-execute).
        # Still attempt target resolution so confirmation can store the action.
        if TargetResolver.is_destructive_task(task):
            return self._destructive_confirmation(observation, task, t0)

        # Prefer scroll — it needs no element match
        scroll = self._match_scroll(task)
        if scroll:
            return self._apply_safety(scroll, task, t0)

        type_decision = self._match_type(task, observation)
        if type_decision:
            return self._apply_safety(type_decision, task, t0)

        click_decision = self._match_click(task, observation)
        if click_decision:
            return self._apply_safety(click_decision, task, t0)

        return {
            "action": None,
            "target": None,
            "reasoning": "No matching element found",
            "status": "no_action",
            "performance": {"total_action_pipeline_ms": _ms(t0)},
        }

    def _destructive_confirmation(
        self, observation: Dict, task: str, t0: float
    ) -> Dict:
        """Build requires_confirmation with optional resolved target + proposed action."""
        result = self.target_resolver.resolve(
            observation,
            task if re.search(r"(?i)\b(click|press|tap)\b", task) else f"click {task}",
            intent="click",
            prefer_tags=("button", "a", "input"),
        )
        resolved = result.target.to_dict() if result.target else None
        safety = self.safety_classifier.classify(
            task=task,
            action="click",
            target=resolved or {},
            target_text=(resolved or {}).get("text"),
            sensitive=(resolved or {}).get("sensitive"),
        )
        proposed = None
        if result.target and result.status in ("ready", "requires_confirmation"):
            proposed = self._proposed_from_target(result.target)

        out = {
            "action": None,
            "target": None,
            "resolved_target": resolved,
            "proposed_action": proposed,
            "reasoning": "Destructive action requires confirmation",
            "status": "requires_confirmation",
            "safety": safety.to_dict(),
            "performance": self._merge_perf(
                result.performance,
                {
                    "total_action_pipeline_ms": _ms(t0),
                    "safety_classification_ms": safety.classification_ms,
                },
            ),
        }
        return out

    def _apply_safety(self, decision: Dict, task: str, t0: float) -> Dict:
        """Attach safety classification; gate confirmation / blocked without breaking schemas."""
        status = decision.get("status")
        if status in ("no_action", "requires_confirmation"):
            # Still attach safety metadata when useful
            if status == "requires_confirmation" and "safety" not in decision:
                safety = self.safety_classifier.classify_decision(decision, task)
                decision["safety"] = safety.to_dict()
                if decision.get("action") and not decision.get("proposed_action"):
                    decision["proposed_action"] = self._proposed_from_decision(decision)
                    decision["action"] = None
                    decision["target"] = None
            decision["performance"] = self._merge_perf(
                decision.get("performance"),
                {"total_action_pipeline_ms": _ms(t0)},
            )
            return decision

        safety = self.safety_classifier.classify_decision(decision, task)
        decision["safety"] = safety.to_dict()
        decision["performance"] = self._merge_perf(
            decision.get("performance"),
            {
                "total_action_pipeline_ms": _ms(t0),
                "safety_classification_ms": safety.classification_ms,
            },
        )

        if safety.level == "blocked":
            # Preserve legacy status "no_action" for password / sensitive (existing tests)
            decision["proposed_action"] = None
            decision["action"] = None
            decision["target"] = None
            decision["text"] = None
            decision["coordinates"] = None
            decision["status"] = "no_action"
            decision["reasoning"] = safety.reason or "Action blocked for safety"
            return decision

        if safety.level == "confirmation_required":
            decision["proposed_action"] = self._proposed_from_decision(decision)
            decision["action"] = None
            decision["target"] = None
            decision["text"] = None
            decision["coordinates"] = None
            decision["status"] = "requires_confirmation"
            decision["reasoning"] = safety.reason or "Action requires confirmation"
            return decision

        return decision

    @staticmethod
    def _proposed_from_target(target) -> Optional[Dict]:
        if target.strategy == "selector" and target.selector:
            return {
                "type": "click",
                "selector": target.selector,
                "target": {
                    "strategy": "selector",
                    "selector": target.selector,
                    "confidence": target.confidence,
                    "source": target.source,
                    "id": target.id,
                    "type": target.type,
                    "text": target.text if not target.sensitive else "[REDACTED]",
                },
            }
        if target.strategy == "coordinates" and target.coordinates:
            coords = dict(target.coordinates)
            return {
                "type": "coordinate_click",
                "x": coords.get("x"),
                "y": coords.get("y"),
                "coordinates": coords,
                "source": target.source,
                "target": {
                    "strategy": "coordinates",
                    "coordinates": coords,
                    "confidence": target.confidence,
                    "source": target.source,
                    "id": target.id,
                    "box": target.box,
                },
            }
        return None

    @staticmethod
    def _proposed_from_decision(decision: Dict) -> Optional[Dict]:
        action = decision.get("action")
        resolved = decision.get("resolved_target") or {}
        if action == "click" and decision.get("target"):
            return {
                "type": "click",
                "selector": decision.get("target"),
                "target": {
                    "strategy": resolved.get("strategy", "selector"),
                    "selector": decision.get("target"),
                    "confidence": resolved.get("confidence"),
                    "source": resolved.get("source", "dom"),
                    "id": resolved.get("id"),
                    "type": resolved.get("type"),
                    "text": resolved.get("text"),
                },
            }
        if action == "coordinate_click":
            coords = decision.get("coordinates") or resolved.get("coordinates") or {}
            return {
                "type": "coordinate_click",
                "x": coords.get("x"),
                "y": coords.get("y"),
                "coordinates": coords,
                "source": resolved.get("source") or "dom+vision",
                "target": {
                    "strategy": "coordinates",
                    "coordinates": coords,
                    "confidence": resolved.get("confidence"),
                    "source": resolved.get("source"),
                    "id": resolved.get("id"),
                    "box": resolved.get("box"),
                },
            }
        if action == "type" and decision.get("target"):
            return {
                "type": "type",
                "selector": decision.get("target"),
                "text": decision.get("text", ""),
            }
        if action == "scroll":
            return {
                "type": "scroll",
                "direction": decision.get("direction", "down"),
                "amount": decision.get("amount", 500),
            }
        return None

    # ------------------------------------------------------------------
    # Scroll
    # ------------------------------------------------------------------
    def _match_scroll(self, task: str) -> Optional[Dict]:
        lower = task.lower()
        if "scroll" not in lower:
            return None

        direction = "down"
        if re.search(r"\bup\b", lower):
            direction = "up"
        elif re.search(r"\bdown\b", lower):
            direction = "down"

        amount_match = re.search(r"(\d{2,4})", lower)
        amount = int(amount_match.group(1)) if amount_match else 500

        return {
            "action": "scroll",
            "target": None,
            "direction": direction,
            "amount": amount,
            "reasoning": f"Scroll {direction} by {amount}px",
            "status": "success",
        }

    # ------------------------------------------------------------------
    # Type (DOM-first; never password / destructive)
    # ------------------------------------------------------------------
    def _match_type(self, task: str, observation: Dict) -> Optional[Dict]:
        patterns = [
            r'(?i)^type\s+["\']?(.+?)["\']?\s+in(?:to)?\s+(?:the\s+)?(.+)$',
            r'(?i)^enter\s+["\']?(.+?)["\']?\s+in(?:to)?\s+(?:the\s+)?(.+)$',
            r'(?i)^fill\s+(?:the\s+)?(.+?)\s+with\s+["\']?(.+?)["\']?$',
        ]

        text_to_type = None
        target_hint = None

        for i, pattern in enumerate(patterns):
            m = re.match(pattern, task.strip())
            if not m:
                continue
            if i < 2:
                text_to_type = m.group(1).strip().strip("\"'")
                target_hint = m.group(2).strip().rstrip(".")
            else:
                target_hint = m.group(1).strip()
                text_to_type = m.group(2).strip().strip("\"'")
            break

        if text_to_type is None or target_hint is None:
            return None

        result = self.target_resolver.resolve(
            observation,
            f"click {target_hint}",
            intent="click",
            prefer_tags=("input", "textarea"),
        )

        # Prefer classic DOM lookup for type so existing tests stay stable
        element = self._find_element(
            observation, target_hint, prefer_tags=("input", "textarea")
        )
        if not element:
            # Fall back to resolver (may find fused input)
            if result.status == "requires_confirmation":
                return {
                    "action": None,
                    "target": None,
                    "resolved_target": result.target.to_dict() if result.target else None,
                    "reasoning": result.reason,
                    "status": "requires_confirmation",
                    "performance": result.performance,
                }
            return {
                "action": None,
                "target": None,
                "reasoning": f'No matching input found for "{target_hint}"',
                "status": "no_action",
                "performance": result.performance,
            }

        if (element.get("type") or "").lower() == "password" or (
            element.get("sensitive") and (element.get("type") or "").lower() == "password"
        ):
            return {
                "action": None,
                "target": None,
                "reasoning": "Refusing to type into a password field",
                "status": "no_action",
            }

        if (element.get("type") or "").lower() == "password" or element.get("sensitive"):
            if (element.get("type") or "").lower() == "password":
                return {
                    "action": None,
                    "target": None,
                    "reasoning": "Refusing to type into a password field",
                    "status": "no_action",
                }

        resolved = {
            "id": element.get("id") or "dom_input",
            "type": "input",
            "strategy": "selector",
            "selector": element.get("selector"),
            "coordinates": None,
            "confidence": 0.98,
            "source": "dom",
        }
        return {
            "action": "type",
            "target": element.get("selector"),
            "text": text_to_type,
            "resolved_target": resolved,
            "reasoning": f'Found input matching "{target_hint}"',
            "status": "success",
        }

    # ------------------------------------------------------------------
    # Click — hybrid TargetResolver
    # ------------------------------------------------------------------
    def _match_click(self, task: str, observation: Dict) -> Optional[Dict]:
        patterns = [
            r'(?i)^(?:click|press|tap)\s+(?:on\s+)?(?:the\s+)?(.+)$',
            r'(?i)^(?:click|press|tap)$',
        ]

        target_hint = None
        for pattern in patterns:
            m = re.match(pattern, task.strip())
            if m:
                if m.lastindex:
                    target_hint = m.group(1).strip().rstrip(".")
                    target_hint = re.sub(
                        r"(?i)\s+(button|link|input|field|box)$", "", target_hint
                    ).strip()
                break

        if target_hint is None:
            m = re.search(
                r'(?i)(?:click|press|tap)\s+(?:on\s+)?(?:the\s+)?(.+?)(?:\s+button|\s+link)?$',
                task.strip(),
            )
            if m:
                target_hint = m.group(1).strip().rstrip(".")
                target_hint = re.sub(
                    r"(?i)\s+(button|link|input|field|box)$", "", target_hint
                ).strip()

        if not target_hint:
            lower = task.lower()
            if "click" not in lower and "press" not in lower and "tap" not in lower:
                return None
            target_hint = re.sub(
                r"(?i)^(find and\s+)?(click|press|tap)\s+(the\s+)?", "", task
            )
            target_hint = re.sub(r"(?i)\s+button$", "", target_hint).strip()

        if not target_hint:
            return None

        result = self.target_resolver.resolve(
            observation,
            task,
            intent="click",
            prefer_tags=("button", "a", "input"),
        )

        if result.status == "requires_confirmation":
            proposed = (
                self._proposed_from_target(result.target) if result.target else None
            )
            return {
                "action": None,
                "target": None,
                "resolved_target": result.target.to_dict() if result.target else None,
                "proposed_action": proposed,
                "reasoning": result.reason,
                "status": "requires_confirmation",
                "performance": result.performance,
            }

        if result.status != "ready" or result.target is None:
            # Preserve legacy message when nothing matched in the DOM
            reason = result.reason or "No matching element found"
            if result.candidates_considered == 0 or not self._find_element(
                observation, target_hint, prefer_tags=("button", "a", "input")
            ):
                # Prefer classic wording for empty / unmatched DOM cases
                if "confidence" not in (result.reason or "").lower() or (
                    result.candidates_considered == 0
                ):
                    reason = "No matching element found"
            return {
                "action": None,
                "target": None,
                "reasoning": reason,
                "status": "no_action",
                "performance": result.performance,
            }

        target = result.target
        resolved = target.to_dict()

        if target.strategy == "selector" and target.selector:
            label = target.text or target.selector
            # Preserve legacy reasoning wording for DOM clicks (regression suite)
            if target.source == "dom":
                reasoning = f'Found a {target.type} with text {label}'
            else:
                reasoning = result.reason or f'Found a {target.type} with text {label}'
            return {
                "action": "click",
                "target": target.selector,
                "resolved_target": resolved,
                "reasoning": reasoning,
                "status": "success",
                "performance": result.performance,
            }

        if target.strategy == "coordinates" and target.coordinates:
            return {
                "action": "coordinate_click",
                "target": None,
                "resolved_target": resolved,
                "coordinates": dict(target.coordinates),
                "reasoning": result.reason
                or "DOM selector unavailable; high-confidence fused visual target found",
                "status": "success",
                "performance": result.performance,
            }

        return {
            "action": None,
            "target": None,
            "reasoning": "No matching element found",
            "status": "no_action",
            "performance": result.performance,
        }

    # ------------------------------------------------------------------
    # Element matching helpers (legacy DOM path — still used by type)
    # ------------------------------------------------------------------
    def _elements(self, observation: Dict) -> List[Dict[str, Any]]:
        elements = observation.get("ui_elements") or []
        if elements:
            return elements
        page = observation.get("page") or {}
        return page.get("elements") or []

    def _find_element(
        self,
        observation: Dict,
        hint: str,
        prefer_tags: tuple = (),
    ) -> Optional[Dict]:
        hint_norm = self._normalize(hint)
        if not hint_norm:
            return None

        elements = self._elements(observation)
        if not elements:
            return None

        scored: List[tuple] = []
        for el in elements:
            score = self._score_element(el, hint_norm)
            if score <= 0:
                continue
            tag = (el.get("tag") or "").lower()
            if prefer_tags and tag in prefer_tags:
                score += 5
            if tag == "input" and (el.get("type") or "").lower() == "submit":
                score += 3
            scored.append((score, el))

        if not scored:
            return None

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]

    def _score_element(self, el: Dict, hint_norm: str) -> int:
        candidates = [
            el.get("text"),
            el.get("ariaLabel"),
            el.get("aria-label"),
            el.get("id"),
            el.get("name"),
            el.get("placeholder"),
            el.get("selector"),
            el.get("value"),
        ]
        best = 0
        for raw in candidates:
            if not raw:
                continue
            val = self._normalize(str(raw))
            if not val:
                continue
            if val == hint_norm:
                best = max(best, 100)
            elif hint_norm in val:
                best = max(best, 80)
            elif val in hint_norm:
                best = max(best, 60)
            else:
                hint_tokens = set(hint_norm.split())
                val_tokens = set(val.split())
                overlap = hint_tokens & val_tokens
                if overlap:
                    best = max(best, 40 + 10 * len(overlap))
        return best

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip().lower())

    @staticmethod
    def _merge_perf(
        existing: Optional[Dict[str, Any]], extra: Dict[str, Any]
    ) -> Dict[str, Any]:
        out = dict(existing or {})
        out.update(extra)
        return out


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000.0, 3)
