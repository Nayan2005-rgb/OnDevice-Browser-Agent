"""Converts a high-level decision from the decision engine into a
concrete, ordered sequence of browser actions the controller can execute.

Supports: click, coordinate_click, type, scroll, no_action, requires_confirmation.
"""

from typing import Dict, List


class ActionPlanner:
    def plan(self, decision: Dict) -> List[Dict]:
        """Expand a decision into concrete steps.

        Example input:  {"action": "click", "target": "#submit-button"}
        Example output: [{"type": "click", "selector": "#submit-button"}]

        Coordinate example:
            {"action": "coordinate_click", "coordinates": {"x": 500, "y": 300}}
            → [{"type": "coordinate_click", "x": 500, "y": 300, "source": "..."}]
        """
        action = decision.get("action")
        target = decision.get("target")
        status = decision.get("status")

        if status in ("no_action", "requires_confirmation") or not action:
            return []

        resolved = decision.get("resolved_target") or {}

        if action == "click" and target:
            step = {"type": "click", "selector": target}
            if resolved:
                step["strategy"] = resolved.get("strategy", "selector")
                step["source"] = resolved.get("source", "dom")
                step["confidence"] = resolved.get("confidence")
                step["target_id"] = resolved.get("id")
            return [step]

        if action == "coordinate_click":
            coords = decision.get("coordinates") or resolved.get("coordinates") or {}
            x = coords.get("x")
            y = coords.get("y")
            if x is None or y is None:
                return []
            step = {
                "type": "coordinate_click",
                "x": int(x),
                "y": int(y),
                "source": resolved.get("source")
                or decision.get("source")
                or "visual_ui_map",
            }
            if resolved:
                step["strategy"] = "coordinates"
                step["confidence"] = resolved.get("confidence")
                step["target_id"] = resolved.get("id")
                if resolved.get("box"):
                    step["box"] = resolved["box"]
            return [step]

        if action == "type" and target:
            step = {
                "type": "type",
                "selector": target,
                "text": decision.get("text", ""),
            }
            if resolved:
                step["strategy"] = resolved.get("strategy", "selector")
                step["source"] = resolved.get("source", "dom")
                step["confidence"] = resolved.get("confidence")
            return [step]

        if action == "scroll":
            return [
                {
                    "type": "scroll",
                    "direction": decision.get("direction", "down"),
                    "amount": decision.get("amount", 500),
                }
            ]

        return []
