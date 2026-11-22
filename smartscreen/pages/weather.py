"""Weather page: current temperature, today's date, tomorrow's forecast (Open-Meteo, no key).

TODO (nice to have): hourly strip for the rest of today, sunrise/sunset, load-shedding stage.
"""
from __future__ import annotations

import logging
import time

from .base import Page
from .. import render as R
from .. import weather_api as W

log = logging.getLogger(__name__)


def draw_icon(d, cx, cy, size, kind):
    """Simple vector weather icons, so no emoji/icon font is needed."""
    s = size / 2
    sun, cloud, rain, snow = (255, 200, 40), (200, 208, 220), (80, 160, 255), (230, 240, 255)

    def sun_at(x, y, r):
        for i in range(8):
            import math
            a = i * math.pi / 4
            d.line((x + math.cos(a) * r * 1.35, y + math.sin(a) * r * 1.35,
                    x + math.cos(a) * r * 1.75, y + math.sin(a) * r * 1.75), fill=sun,
                   width=max(2, int(r / 5)))
        d.ellipse((x - r, y - r, x + r, y + r), fill=sun)

    def cloud_at(x, y, w, col=cloud):
        h = w * 0.5
        d.ellipse((x - w * 0.5, y - h * 0.1, x - w * 0.05, y + h * 0.55), fill=col)
        d.ellipse((x - w * 0.25, y - h * 0.6, x + w * 0.25, y + h * 0.4), fill=col)
        d.ellipse((x + w * 0.02, y - h * 0.25, x + w * 0.5, y + h * 0.55), fill=col)
        d.rectangle((x - w * 0.3, y + h * 0.1, x + w * 0.3, y + h * 0.55), fill=col)

    if kind == "sun":
        sun_at(cx, cy, s * 0.5)
    elif kind == "partly":
        sun_at(cx + s * 0.25, cy - s * 0.25, s * 0.38)
        cloud_at(cx - s * 0.1, cy + s * 0.2, s * 1.3)
    elif kind in ("cloud", "fog"):
        cloud_at(cx, cy, s * 1.6)
        if kind == "fog":
            for i in range(3):
                y = cy + s * 0.55 + i * s * 0.17
                d.line((cx - s * 0.7, y, cx + s * 0.7, y), fill=R.MUTED, width=3)
    else:
        cloud_at(cx, cy - s * 0.2, s * 1.5)
        for i in range(3):
            x = cx - s * 0.45 + i * s * 0.45
            y = cy + s * 0.45
            if kind == "snow":
                d.ellipse((x - 4, y - 4, x + 4, y + 4), fill=snow)
            elif kind == "storm" and i == 1:
                d.polygon([(x, y - 6), (x - 8, y + 10), (x, y + 10), (x - 6, y + 24),
                           (x + 10, y + 4), (x + 2, y + 4), (x + 8, y - 6)], fill=sun)
            else:
                d.line((x + 4, y - 2, x - 4, y + 14), fill=rain, width=4)


class WeatherPage(Page):
    name = "weather"

    def __init__(self, cfg, sensors=None, dummy=False):
        super().__init__(cfg, sensors)
        wc = cfg.get("weather", {})
        self.city = wc.get("city", "Your City")
        self.lat, self.lon = wc.get("latitude"), wc.get("longitude")
        self.data_interval = float(wc.get("refresh_minutes", 15)) * 60
        W.TIMEOUT = float(cfg.get("network_timeout_seconds", 10))
        self.dummy = dummy
        self.w: W.Weather | None = W.DUMMY if dummy else None
        self.error = None

    def update(self):
        if self.dummy:
            return
        try:
            if self.lat is None or self.lon is None:
                geo = W.geocode(self.city)
                if not geo:
                    raise RuntimeError(f"City '{self.city}' not found")
                self.lat, self.lon, self.city = geo
            self.w = W.fetch(self.lat, self.lon, self.city)
            self.error = None
        except Exception as e:
            log.warning("Weather fetch failed: %s", e)
            self.error = "weather offline"
            self._last_update -= self.data_interval - 120  # retry in about 2 minutes

    def render(self, w, h):
        img, d = self.canvas(w, h)
        self.header(d, w, "WEATHER", R.WEATHER_ACCENT, right=time.strftime("%H:%M"))
        wx = self.w
        day = time.strftime("%A")
        date = f"{time.localtime().tm_mday} {time.strftime('%B %Y')}"
        if wx is None:
            R.text(d, (w // 2, h // 2), self.error or "Fetching weather...", R.font(20), R.MUTED,
                   anchor="mm")
            return img
        now_txt, now_kind = W.describe(wx.now_code)
        tom_txt, tom_kind = W.describe(wx.tom_code)
        pad = 6
        if w > h:
            left = (pad, 32, w // 2 - pad // 2, h - pad)
            right = (w // 2 + pad // 2, 32, w - pad, h - pad)
        else:
            left = (pad, 32, w - pad, 32 + 250)
            right = (pad, 32 + 250 + pad, w - pad, h - pad)

        # --- now ------------------------------------------------------------------ #
        x0, y0, x1, y1 = left
        R.card(d, left, accent=R.WEATHER_ACCENT)
        R.text(d, (x0 + 14, y0 + 10), day, R.font(22, "bold"), R.TEXT)
        R.text(d, (x0 + 14, y0 + 36), date, R.font(16, "medium"), R.MUTED)
        big = R.font(64, "semibold")
        t = "n/a" if wx.now_temp is None else f"{wx.now_temp:.0f}°"
        R.text(d, (x0 + 14, y0 + 62), t, big, R.TEXT)
        draw_icon(d, x1 - 52, y0 + 104, 70, now_kind)
        R.text(d, (x0 + 14, y0 + 140), R.fit(d, now_txt, R.font(18, "semibold"), x1 - x0 - 28),
               R.font(18, "semibold"), R.WEATHER_ACCENT)
        f = R.font(15, "medium")
        lines = []
        if wx.today_min is not None:
            lines.append(f"Today {wx.today_min:.0f}° / {wx.today_max:.0f}°")
        extra = []
        if wx.now_humidity is not None:
            extra.append(f"Hum {wx.now_humidity:.0f}%")
        if wx.now_wind_kmh is not None:
            extra.append(f"Wind {wx.now_wind_kmh:.0f} km/h")
        if extra:
            lines.append("  ".join(extra))
        for i, ln in enumerate(lines):
            R.text(d, (x0 + 14, y0 + 168 + i * 20), R.fit(d, ln, f, x1 - x0 - 28), f, R.MUTED)

        # --- tomorrow ------------------------------------------------------------- #
        x0, y0, x1, y1 = right
        R.card(d, right, "TOMORROW", R.WEATHER_ACCENT,
               time.strftime("%a", time.localtime(time.time() + 86400)))
        draw_icon(d, x0 + 52, y0 + 78, 64, tom_kind)
        hi = "n/a" if wx.tom_max is None else f"{wx.tom_max:.0f}°"
        lo = "" if wx.tom_min is None else f" / {wx.tom_min:.0f}°"
        R.text(d, (x0 + 104, y0 + 46), hi, R.font(40, "semibold"), R.TEXT)
        R.text(d, (x0 + 104 + R.text_w(d, hi, R.font(40, "semibold")), y0 + 60), lo,
               R.font(22, "medium"), R.MUTED)
        R.text(d, (x0 + 104, y0 + 94), R.fit(d, tom_txt, R.font(17, "semibold"), x1 - x0 - 118),
               R.font(17, "semibold"), R.WEATHER_ACCENT)
        if wx.tom_rain_prob is not None:
            rain = f"Rain {wx.tom_rain_prob:.0f}%"
            if wx.tom_rain_mm:
                rain += f", {wx.tom_rain_mm:.1f} mm"
            ry = y0 + 128
            R.text(d, (x0 + 14, ry), rain, R.font(15, "medium"), R.TEXT)
            R.bar(d, (x0 + 14, ry + 22, x1 - 14, ry + 28), wx.tom_rain_prob / 100, (80, 160, 255))
        foot = f"{wx.location}  |  updated {wx.fetched_at}"
        if self.error:
            foot = f"{wx.location}  |  {self.error}"
        R.text(d, (x0 + 14, y1 - 22), R.fit(d, foot, R.font(13, "medium"), x1 - x0 - 28),
               R.font(13, "medium"), R.DIM)
        return img
