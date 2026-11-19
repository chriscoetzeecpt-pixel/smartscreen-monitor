"""Page interface.

A page is asked to:
  * update()  - refresh its data. Called on the main loop at most every `data_interval`
                seconds. May be slow (network); errors are caught and logged by the caller.
  * render(w, h) - return a full-screen PIL image. Must be fast and must not do I/O.
The app diffs each rendered frame against the last one sent and only transmits the
changed regions, so pages can simply redraw everything every tick.
"""
from __future__ import annotations

import time

from PIL import Image, ImageDraw

from .. import render as R


class Page:
    name = "page"
    data_interval = 1.0  # seconds between update() calls

    def __init__(self, cfg: dict, sensors=None):
        self.cfg = cfg
        self.sensors = sensors
        self._last_update = 0.0

    def maybe_update(self, force: bool = False):
        now = time.monotonic()
        if force or now - self._last_update >= self.data_interval:
            self._last_update = now
            self.update()

    def update(self):
        pass

    def render(self, w: int, h: int) -> Image.Image:
        raise NotImplementedError

    # Helpers
    @staticmethod
    def canvas(w, h):
        img = Image.new("RGB", (w, h), R.BG)
        return img, ImageDraw.Draw(img)

    def header(self, draw, w, title, accent, right=None):
        R.text(draw, (12, 6), title, R.font(18, "bold"), accent)
        if right:
            R.text(draw, (w - 12, 6), right, R.font(18, "semibold"), R.TEXT, anchor="ra")
