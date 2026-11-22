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
    # --- reliability (all optional; an older config.json without them gets these values) ---
    "log_level": "INFO",              # DEBUG, INFO, WARNING. -v on the command line forces DEBUG
    "status_log_minutes": 30,         # status line (frames, memory, threads) in the log; 0 = off
    "serial_write_timeout": 5.0,      # seconds before a stalled serial write is treated as an error
    "reset_on_desync": True,          # RESET the screen after a write failed part-way (fixes garbling)
    "reconnect_backoff_max_seconds": 60,  # reconnect waits 2, 4, 8 ... s up to this
    "max_consecutive_errors": 0,      # exit after this many failed reconnects in a row; 0 = keep trying
    "full_redraw_seconds": 300,       # resend the whole screen this often so artifacts self-heal; 0 = off
    "watchdog_seconds": 60,           # exit (code 3) if no frame completes for this long; 0 = off
    "network_timeout_seconds": 10,    # weather / RSS HTTP timeout
    "supervisor": True,               # main.py restarts the worker process if it exits with an error
}

_NUMERIC = {  # key: (minimum, maximum)
    "status_log_minutes": (0, 1440),
    "serial_write_timeout": (0.5, 60),
    "reconnect_backoff_max_seconds": (2, 3600),
    "max_consecutive_errors": (0, 100000),
    "full_redraw_seconds": (0, 86400),
    "watchdog_seconds": (0, 86400),
    "network_timeout_seconds": (1, 120),
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
    for key, (lo, hi) in _NUMERIC.items():
        try:
            cfg[key] = max(lo, min(hi, float(cfg[key])))
        except (TypeError, ValueError):
            cfg[key] = DEFAULTS[key]
    # a watchdog shorter than a few frames would kill a healthy app
    if cfg["watchdog_seconds"] and cfg["watchdog_seconds"] < 10 * cfg["update_interval"]:
        cfg["watchdog_seconds"] = max(15.0, 10 * cfg["update_interval"])
    cfg["max_consecutive_errors"] = int(cfg["max_consecutive_errors"])
    return cfg
