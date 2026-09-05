"""Executes planned actions against the real browser, via the extension's
message-passing bridge (browserActions.js)."""

from typing import Dict, List


class BrowserController:
    def __init__(self, extension_bridge=None):
        """
        Args:
            extension_bridge: an interface (e.g. websocket/native messaging
                               client) used to send commands to the extension.
        """
        self.extension_bridge = extension_bridge

    def execute(self, steps: List[Dict]) -> List[Dict]:
        """Execute each planned step in order, returning results per step.

        TODO: send each step to self.extension_bridge and await confirmation
        from browserActions.js in the extension.
        """
        results = []
        for step in steps:
            results.append({"step": step, "status": "not_implemented"})
        return results
