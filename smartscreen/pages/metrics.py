"""Default page: CPU load + temp, GPU load + temp, SSD read/write throughput."""
from __future__ import annotations

import math
import platform
import time

from .base import Page
from .. import render as R


class MetricsPage(Page):
    name = "metrics"

    def __init__(self, cfg, sensors=None):
        super().__init__(cfg, sensors)
        self.data_interval = float(cfg.get("update_interval", 1.0))
        self.m = None
        self.title = cfg.get("title") or platform.node().upper() or "PC"
        self.cpu_name = cfg.get("cpu_label", "CPU")
        self.gpu_label = cfg.get("gpu_label")  # None = use NVML name

    def update(self):
        self.m = self.sensors.read()

    def render(self, w, h):
        img, d = self.canvas(w, h)
        m = self.m
        self.header(d, w, self.title, R.MUTED, right=time.strftime("%H:%M"))
        if m is None:
            R.text(d, (w // 2, h // 2), "Reading sensors...", R.font(20), R.MUTED, anchor="mm")
            return img
        pad, top = 6, 32
        if w > h:  # landscape 480x320: CPU | GPU on top, SSD (half) | RAM (half) below
            col = (w - 3 * pad) // 2
            self.cpu_card(d, (pad, top, pad + col, top + 150), m)
            self.gpu_card(d, (2 * pad + col, top, w - pad, top + 150), m)
            by0 = top + 150 + pad
            self.disk_card(d, (pad, by0, pad + col, h - pad), m)
            self.ram_card(d, (2 * pad + col, by0, w - pad, h - pad), m)
        else:      # portrait 320x480: stacked
            ch = 142
            self.cpu_card(d, (pad, top, w - pad, top + ch), m)
            self.gpu_card(d, (pad, top + ch + pad, w - pad, top + 2 * ch + pad), m)
            self.disk_card(d, (pad, top + 2 * (ch + pad), w - pad, h - pad), m)
        return img

    # ---- cards ---------------------------------------------------------- #
    def _two_values(self, d, rect, y, load, temp, accent, temp_warn, temp_hot, extra):
        x0, _, x1, y1 = rect
        inner_w = x1 - x0 - 28
        half = inner_w // 2
        lx, tx = x0 + 14, x0 + 14 + half + 8
        big = R.font(44, "semibold")
        small = R.font(13, "medium")
        R.text(d, (lx, y), "LOAD", small, R.MUTED)
        R.text(d, (tx, y), "TEMP", small, R.MUTED)
        R.text(d, (lx, y + 14), R.fmt_pct(load), big, R.TEXT if load is not None else R.DIM)
        R.text(d, (tx, y + 14), R.fmt_temp(temp), big, R.temp_color(temp, temp_warn, temp_hot))
        by = y + 68
        R.bar(d, (lx, by, lx + half - 12, by + 8), None if load is None else load / 100, accent)
        R.bar(d, (tx, by, x1 - 14, by + 8), None if temp is None else temp / 100,
              R.temp_color(temp, temp_warn, temp_hot))
        if extra:
            R.text(d, (lx, by + 16), R.fit(d, extra, R.font(14, "medium"), x1 - lx - 14),
                   R.font(14, "medium"), R.MUTED)

    def cpu_card(self, d, rect, m):
        y = R.card(d, rect, "CPU", R.CPU_ACCENT, self.cpu_name)
        extra = None
        if m.ram_used_gb is not None:
            extra = f"RAM {m.ram_used_gb:.1f} / {m.ram_total_gb:.0f} GB"
        if not m.lhm_ok:
            extra = (extra + "  |  " if extra else "") + "temp needs LHM"
        # Conservative warn/hot thresholds in degC (many CPUs are uncomfortable above these)
        self._two_values(d, rect, y, m.cpu_load, m.cpu_temp, R.CPU_ACCENT, 70, 82, extra)

    def gpu_card(self, d, rect, m):
        name = self.gpu_label or (m.gpu_name or "GPU").replace("NVIDIA GeForce ", "")
        y = R.card(d, rect, "GPU", R.GPU_ACCENT, name)
        parts = []
        if m.gpu_vram_used_gb is not None:
            parts.append(f"VRAM {m.gpu_vram_used_gb:.1f} / {m.gpu_vram_total_gb:.0f} GB")
        if m.gpu_power_w is not None:
            parts.append(f"{m.gpu_power_w:.0f} W")
        if m.igpu_temp is not None:
            parts.append(f"iGPU {m.igpu_temp:.0f}°C")
        self._two_values(d, rect, y, m.gpu_load, m.gpu_temp, R.GPU_ACCENT, 75, 83,
                         "  |  ".join(parts) or None)

    def ram_card(self, d, rect, m):
        x0, y0, x1, y1 = rect
        used, total = m.ram_used_gb, m.ram_total_gb
        pct = None if not total else max(0.0, min(100.0, used / total * 100.0))
        y = R.card(d, rect, "RAM", R.RAM_ACCENT, "usage")
        lx = x0 + 14
        big = R.font(40, "semibold")
        small = R.font(13, "medium")
        R.text(d, (lx, y), "USED", small, R.MUTED)
        R.text(d, (lx, y + 14), R.fmt_pct(pct), big, R.TEXT if pct is not None else R.DIM)
        by = y + 62
        R.bar(d, (lx, by, x1 - 14, by + 8), None if pct is None else pct / 100, R.RAM_ACCENT)
        if total:
            R.text(d, (lx, by + 16),
                   R.fit(d, f"{used:.1f} / {total:.0f} GB", R.font(15, "medium"), x1 - lx - 14),
                   R.font(15, "medium"), R.MUTED)

    def disk_card(self, d, rect, m):
        x0, y0, x1, y1 = rect
        y = R.card(d, rect, "SSD", R.DISK_ACCENT, "read / write")
        disks = m.disks[: int(self.cfg.get("max_disks", 2))]
        if not disks:
            R.text(d, (x0 + 14, y + 10), "No disk counters", R.font(16), R.DIM)
            return
        avail = (y1 - 6) - y
        row_h = avail // len(disks)
        name_f, val_f, tag_f = R.font(14, "semibold"), R.font(21, "semibold"), R.font(12, "bold")
        inner = x1 - x0 - 28
        half = inner // 2
        # Only draw per-row bars if the row is tall enough to fit them without overlap.
        draw_bars = row_h >= 56
        for i, dk in enumerate(disks):
            ry = y + i * row_h
            R.text(d, (x0 + 14, ry), R.fit(d, dk.label, name_f, inner), name_f, R.TEXT)
            vy = ry + 17
            cols = [(x0 + 14, "R", dk.read_mb_s, R.CPU_ACCENT),
                    (x0 + 14 + half + 6, "W", dk.write_mb_s, R.DISK_ACCENT)]
            for cx, tag, val, col in cols:
                R.text(d, (cx, vy + 6), tag, tag_f, col)
                R.text(d, (cx + 13, vy), R.fmt_rate(val), val_f,
                       R.TEXT if val is not None else R.DIM)
                if draw_bars:
                    frac = None
                    if val is not None:
                        frac = 0 if val < 0.1 else min(1.0, math.log10(val / 0.1) / math.log10(70000))
                    bw = half - 18
                    R.bar(d, (cx, vy + 27, cx + bw, vy + 32), frac, col)
