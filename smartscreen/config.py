"""Config loading. config.json next to main.py; missing keys fall back to DEFAULTS."""
from __future__ import annotations

import copy
import json
from pathlib import Path

DEFAULTS = {
    "com_port": "AUTO",
    "com_port_fallback": "COM3",
    "orientation": "portrait",
    "brightness": 30,           # percent, 0-100. Rev A runs hot when bright; 20-40 recommended
    "update_interval": 1.0,     # seconds
    "active_page": "metrics",
    "pages": ["metrics", "weather", "posts"],
    "page_cycle_seconds": 0,    # 0 = no automatic rotation
    "hotkeys": {"enabled": True, "next_page": "ctrl+alt+shift+right",
                "prev_page": "ctrl+alt+shift+left", "toggle_screen": "ctrl+alt+shift+down"},
    "screen_off_on_exit": True,
    "reset_on_startup": False,  # keep False: RESET re-enumerates USB and can change the COM port
    "title": None,
    "cpu_label": "CPU",
    "gpu_label": None,
    "nvidia_gpu_index": 0,
    "show_amd_igpu": False,
    "lhm_url": "http://localhost:8085/data.json",
    "max_disks": 2,
    "disks_include": None,
    "disk_labels": {},
    "weather": {"city": "Your City", "latitude": 0.0, "longitude": 0.0,
                "refresh_minutes": 15},
    "posts": {"title": "TOP POSTS", "count": 5, "refresh_minutes": 10, "provider": "static",
              "options": {"items": []}},
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path: Path) -> dict:
    path = Path(path)
    user = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
    cfg = _merge(DEFAULTS, user)
    cfg["brightness"] = max(0, min(100, float(cfg["brightness"])))
    cfg["update_interval"] = max(0.25, float(cfg["update_interval"]))
    return cfg
