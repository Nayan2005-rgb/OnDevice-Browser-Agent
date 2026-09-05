"""Captures screenshots of the active browser tab / screen region for the
agent's perception pipeline.

Note: Browser-tab screenshots are captured by the extension itself via
chrome.tabs.captureVisibleTab() and POSTed to the backend — a Python
process cannot reach into a browser tab directly. This class is kept as
an optional OS-level fallback (e.g. for capturing the full desktop)
using the `mss` library, for headless/agent-driven scenarios outside
the extension flow."""

from typing import Optional
import numpy as np


class ScreenshotCapture:
    def __init__(self, region: Optional[tuple] = None):
        """
        Args:
            region: Optional (x, y, width, height) to restrict capture.
                    Defaults to full screen.
        """
        self.region = region

    def capture(self) -> np.ndarray:
        """Capture the full desktop (or self.region) using mss and return
        it as an RGB numpy array. Requires `pip install mss`."""
        import mss

        with mss.mss() as sct:
            monitor = sct.monitors[1] if self.region is None else {
                "left": self.region[0],
                "top": self.region[1],
                "width": self.region[2],
                "height": self.region[3],
            }
            shot = sct.grab(monitor)
            img = np.array(shot)  # BGRA
            return img[:, :, [2, 1, 0]]  # convert to RGB


if __name__ == "__main__":
    capturer = ScreenshotCapture()
    print("ScreenshotCapture initialized (desktop fallback via mss).")