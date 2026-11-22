"""Top-X posts page. Data source is pluggable, see smartscreen/posts_providers.py.

TODO: decide on the real source (X list, Reddit, RSS, news). Layout is complete; only the
provider needs choosing in config.json.
"""
from __future__ import annotations

import logging
import time

from .base import Page
from .. import render as R
from .. import posts_providers as P

log = logging.getLogger(__name__)


class PostsPage(Page):
    name = "posts"

    def __init__(self, cfg, sensors=None):
        super().__init__(cfg, sensors)
        pc = cfg.get("posts", {})
        self.provider = pc.get("provider", "static")
        self.options = pc.get("options", {})
        self.limit = int(pc.get("count", 5))
        self.title = pc.get("title", "TOP POSTS")
        self.data_interval = float(pc.get("refresh_minutes", 10)) * 60
        P.TIMEOUT = float(cfg.get("network_timeout_seconds", 10))
        self.posts: list[P.Post] = []
        self.error = None

    def update(self):
        try:
            self.posts = P.get_posts(self.provider, self.limit, self.options)
            self.error = None
        except Exception as e:
            log.warning("Posts provider '%s' failed: %s", self.provider, e)
            self.error = str(e)[:60]
            self._last_update -= self.data_interval - 120

    def render(self, w, h):
        img, d = self.canvas(w, h)
        self.header(d, w, self.title, R.POSTS_ACCENT, right=time.strftime("%H:%M"))
        if not self.posts:
            R.text(d, (w // 2, h // 2), self.error or "No posts yet", R.font(18), R.MUTED, anchor="mm")
            return img
        pad, top = 6, 32
        n = len(self.posts)
        # Row height from the configured count, so a short list doesn't stretch into huge cards
        row_h = (h - top - pad) // max(n, min(self.limit, 6))
        body = R.font(16 if w > h else 15, "medium")
        meta_f = R.font(12, "medium")
        num_f = R.font(26, "bold")
        lines_per = max(1, min(3, (row_h - 22) // 19))
        for i, p in enumerate(self.posts):
            y0 = top + i * row_h
            rect = (pad, y0, w - pad, y0 + row_h - 4)
            R.card(d, rect, accent=R.POSTS_ACCENT, radius=8)
            R.text(d, (pad + 14, y0 + 6), str(i + 1), num_f, R.POSTS_ACCENT)
            tx = pad + 44
            for j, ln in enumerate(R.wrap(d, p.text, body, w - pad - 12 - tx, lines_per)):
                R.text(d, (tx, y0 + 6 + j * 19), ln, body, R.TEXT)
            if p.meta:
                R.text(d, (tx, y0 + row_h - 21), R.fit(d, p.meta, meta_f, w - pad - 12 - tx),
                       meta_f, R.DIM)
        return img
