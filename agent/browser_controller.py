"""Executes planned actions against the real browser via the extension.

In this milestone, the Chrome extension performs actual DOM actions
(browserActions.js). BrowserController packages planned steps so the
API can return them to the extension for execution.
"""

from typing import Dict, List


class BrowserController:
    def __init__(self, extension_bridge=None):
        """
        Args:
            extension_bridge: reserved for a future websocket/native
                              messaging client to the extension.
        """
        self.extension_bridge = extension_bridge

    def execute(self, steps: List[Dict]) -> List[Dict]:
        """Acknowledge planned steps for extension-side execution.

        Real clicks/types/scrolls happen in the content script after the
        API response is delivered by the background service worker.
        """
        results = []
        for step in steps:
            results.append(
                {
                    "step": step,
                    "status": "delegated_to_extension",
                }
            )
        return results
