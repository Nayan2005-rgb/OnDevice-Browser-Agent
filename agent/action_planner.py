"""Converts a high-level decision from the decision engine into a
concrete, ordered sequence of browser actions the controller can execute."""

from typing import Dict, List


class ActionPlanner:
    def plan(self, decision: Dict) -> List[Dict]:
        """Expand a decision into concrete steps.

        Example input:  {"action": "login", "target": "#login-form"}
        Example output: [
            {"type": "click", "selector": "#username"},
            {"type": "type", "selector": "#username", "text": "..."},
            {"type": "click", "selector": "#submit"},
        ]
        """
        action = decision.get("action")
        target = decision.get("target")

        if action == "click" and target:
            return [{"type": "click", "selector": target}]
        if action == "type" and target:
            return [{"type": "type", "selector": target, "text": decision.get("text", "")}]

        return []
