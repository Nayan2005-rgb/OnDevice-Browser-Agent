"""Decision engine: takes a perception observation and the current task
goal, and decides the next high-level action (via a local or remote LLM)."""

from typing import Dict, List


class DecisionEngine:
    def __init__(self, llm_client=None):
        self.llm_client = llm_client

    def decide_next_action(self, observation: Dict, goal: str, history: List[Dict]) -> Dict:
        """Return a structured action decision, e.g.:
            {"action": "click", "target": "#submit-button", "reasoning": "..."}

        TODO: build a prompt from observation + goal + history and call
        self.llm_client (see server/llm_service.py) to get the next step.
        """
        return {"action": "noop", "target": None, "reasoning": "Not yet implemented."}
