"""
Sensor sources. Everything here runs WITHOUT admin rights.

  * psutil          - CPU load, RAM, per-disk read/write throughput.
  * nvidia-ml-py    - NVIDIA GPU temperature, load, VRAM, power (talks to the driver's NVML DLL).
  * LibreHardwareMonitor (optional) - CPU temperature and AMD iGPU readings, read from LHM's
                      local web server (http://localhost:8085/data.json). LHM itself needs admin
                      to read the hardware; this app only reads its JSON over localhost.

Any source that is missing or failing yields None, which the pages render as "n/a".
"""
from __future__ import annotations

import json
import logging
import random
import re
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class DiskRate:
    key: str            # psutil key, e.g. "PhysicalDrive0"
    label: str          # short display name, e.g. "SSD 1TB"
    read_mb_s: float | None = None
    write_mb_s: float | None = None


@dataclass
class Metrics:
    cpu_load: float | None = None        # %
    cpu_temp: float | None = None        # deg C (LHM)
    ram_used_gb: float | None = None
    ram_total_gb: float | None = None
    gpu_name: str | None = None
    gpu_load: float | None = None        # %
    gpu_temp: float | None = None        # deg C
    gpu_vram_used_gb: float | None = None
    gpu_vram_total_gb: float | None = None
    gpu_power_w: float | None = None
    igpu_temp: float | None = None       # AMD iGPU deg C (LHM, optional)
    igpu_load: float | None = None
    disks: list[DiskRate] = field(default_factory=list)
    lhm_ok: bool = False


# --------------------------------------------------------------------------- #
# LibreHardwareMonitor JSON
# --------------------------------------------------------------------------- #
_NUM = re.compile(r"-?\d+(?:[.,]\d+)?")


def parse_lhm_value(text) -> float | None:
    """'45.3 °C' -> 45.3. Handles comma decimals (for example '45,3 °C')."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    m = _NUM.search(str(text))
    return float(m.group(0).replace(",", ".")) if m else None


_HW_ICONS = {"cpu.png", "nvidia.png", "ati.png", "amd.png", "hdd.png", "ram.png",
             "mainboard.png", "chip.png", "nic.png", "intel.png"}


def flatten_lhm(node, path=(), hw_icon="", out=None):
    """Walk the LHM tree and return a list of leaf sensors as dicts:
    {path: (texts...), value: float, sensor_id: str, type: str, hw_icon: str}"""
    if out is None:
        out = []
    text = node.get("Text", "")
    icon = node.get("ImageURL", "") or ""
    # Hardware-level nodes carry the device icon (cpu.png, nvidia.png, ati.png/amd.png, hdd.png)
    base = icon.rsplit("/", 1)[-1].lower()
    if base in _HW_ICONS:
        hw_icon = base
    children = node.get("Children") or []
    new_path = path + (text,)
    if not children and node.get("Value") not in (None, ""):
        out.append({
            "path": new_path,
            "value": parse_lhm_value(node.get("Value")),
            "sensor_id": node.get("SensorId", "") or "",
            "type": node.get("Type", "") or "",
            "hw_icon": hw_icon,
        })
    for c in children:
        flatten_lhm(c, new_path, hw_icon, out)
    return out


def _is_cpu(s):
    sid = s["sensor_id"].lower()
    return sid.startswith(("/amdcpu", "/intelcpu")) or s["hw_icon"] == "cpu.png"


def _is_amd_gpu(s):
    sid = s["sensor_id"].lower()
    return sid.startswith("/gpu-amd") or s["hw_icon"] in ("ati.png", "amd.png")


def _is_nvidia_gpu(s):
    return s["sensor_id"].lower().startswith("/gpu-nvidia") or s["hw_icon"] == "nvidia.png"


def _kind(s, kind):
    """kind: 'temperature' or 'load'. Uses the Type field when present, else the group name."""
    if s["type"]:
        return s["type"].lower() == kind
    group = s["path"][-2].lower() if len(s["path"]) >= 2 else ""
    return group.startswith(kind[:4])  # "Temperatures", "Load"


def _pick(sensors, preferred_names):
    for name in preferred_names:
        for s in sensors:
            if s["path"][-1].lower() == name.lower() and s["value"] is not None:
                return s["value"]
    for s in sensors:
        if s["value"] is not None:
            return s["value"]
    return None


CPU_TEMP_NAMES = ["Core (Tctl/Tdie)", "CPU Package", "Package", "Tctl", "Tdie",
                  "CCD1 (Tdie)", "Core Max", "Core Average"]
GPU_TEMP_NAMES = ["GPU Core", "GPU Temperature", "GPU Hot Spot"]
GPU_LOAD_NAMES = ["GPU Core", "D3D 3D"]


def extract_lhm(tree) -> dict:
    sensors = flatten_lhm(tree)
    cpu_t = [s for s in sensors if _is_cpu(s) and _kind(s, "temperature")]
    amd_t = [s for s in sensors if _is_amd_gpu(s) and _kind(s, "temperature")]
    amd_l = [s for s in sensors if _is_amd_gpu(s) and _kind(s, "load")]
    nv_t = [s for s in sensors if _is_nvidia_gpu(s) and _kind(s, "temperature")]
    nv_l = [s for s in sensors if _is_nvidia_gpu(s) and _kind(s, "load")]
    return {
        "cpu_temp": _pick(cpu_t, CPU_TEMP_NAMES),
        "igpu_temp": _pick(amd_t, GPU_TEMP_NAMES),
        "igpu_load": _pick(amd_l, GPU_LOAD_NAMES),
        "nv_temp": _pick(nv_t, GPU_TEMP_NAMES),
        "nv_load": _pick(nv_l, GPU_LOAD_NAMES),
    }


class LhmSource:
    def __init__(self, url: str, timeout: float = 0.8):
        self.url = url
        self.timeout = timeout
        self._last_err_log = 0.0

    def read(self) -> dict | None:
        if not self.url:
            return None
        try:
            with urllib.request.urlopen(self.url, timeout=self.timeout) as r:
                tree = json.loads(r.read().decode("utf-8", errors="replace"))
            return extract_lhm(tree)
        except Exception as e:
            now = time.monotonic()
            if now - self._last_err_log > 300:  # don't spam the log
                log.warning("LibreHardwareMonitor not reachable at %s (%s). CPU temp shows n/a.",
                            self.url, e)
                self._last_err_log = now
            return None


# --------------------------------------------------------------------------- #
# NVIDIA via NVML
# --------------------------------------------------------------------------- #
class NvidiaSource:
    def __init__(self, index: int = 0):
        self.h = None
        self.name = None
        try:
            import pynvml  # provided by the nvidia-ml-py package
            pynvml.nvmlInit()
            self.nv = pynvml
            self.h = pynvml.nvmlDeviceGetHandleByIndex(index)
            name = pynvml.nvmlDeviceGetName(self.h)
            self.name = name.decode() if isinstance(name, bytes) else name
            log.info("NVML: using GPU %d, %s", index, self.name)
        except Exception as e:
            log.warning("NVIDIA NVML unavailable (%s). GPU values may come from LHM or show n/a.", e)

    def read(self) -> dict:
        if self.h is None:
            return {}
        nv, h, out = self.nv, self.h, {"name": self.name}
        for key, fn in (
            ("temp", lambda: nv.nvmlDeviceGetTemperature(h, nv.NVML_TEMPERATURE_GPU)),
            ("load", lambda: nv.nvmlDeviceGetUtilizationRates(h).gpu),
            ("mem", lambda: nv.nvmlDeviceGetMemoryInfo(h)),
            ("power", lambda: nv.nvmlDeviceGetPowerUsage(h) / 1000.0),  # mW -> W
        ):
            try:
                out[key] = fn()
            except Exception:
                out[key] = None
        return out


# --------------------------------------------------------------------------- #
# Disks
# --------------------------------------------------------------------------- #
def shorten_model(model: str) -> str:
    """Trim common vendor prefixes so the label fits the disk card."""
    m = model.strip()
    for prefix in ("Samsung SSD ", "Samsung ", "WD ", "Western Digital ", "Seagate ", "Crucial "):
        if m.startswith(prefix):
            m = m[len(prefix):].strip()
            break
    m = m.replace("SSD", "").strip()
    return m[:14]


def windows_disk_models() -> dict[str, str]:
    """{'PhysicalDrive0': 'Samsung SSD 1TB', ...} via one PowerShell call.
    Only runs on Windows; never fatal."""
    if sys.platform != "win32":
        return {}
    try:
        cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
               "Get-CimInstance Win32_DiskDrive | Select-Object Index,Model | ConvertTo-Json -Compress"]
        flags = 0x08000000  # CREATE_NO_WINDOW, so no console flashes when run with pythonw
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15, creationflags=flags)
        data = json.loads(res.stdout or "[]")
        if isinstance(data, dict):
            data = [data]
        return {f"PhysicalDrive{d['Index']}": d["Model"] for d in data}
    except Exception as e:
        log.warning("Could not read disk models (%s); using PhysicalDriveN names", e)
        return {}


class DiskSource:
    def __init__(self, labels: dict[str, str] | None = None, include: list[str] | None = None):
        import psutil
        self.psutil = psutil
        models = windows_disk_models()
        self.labels = {k: shorten_model(v) for k, v in models.items()}
        self.labels.update(labels or {})       # config overrides win
        self.include = include                 # None = all physical drives
        self.prev = None
        self.prev_t = None

    def read(self) -> list[DiskRate]:
        try:
            now = time.monotonic()
            cur = self.psutil.disk_io_counters(perdisk=True) or {}
        except Exception as e:
            log.warning("disk_io_counters failed: %s", e)
            return []
        keys = sorted(k for k in cur if (self.include is None or k in self.include))
        rates = []
        for k in keys:
            label = self.labels.get(k, k.replace("PhysicalDrive", "Disk "))
            d = DiskRate(k, label)
            if self.prev and k in self.prev:
                dt = max(now - self.prev_t, 1e-3)
                d.read_mb_s = max(0.0, (cur[k].read_bytes - self.prev[k].read_bytes) / dt / 1e6)
                d.write_mb_s = max(0.0, (cur[k].write_bytes - self.prev[k].write_bytes) / dt / 1e6)
            rates.append(d)
        self.prev, self.prev_t = cur, now
        return rates


# --------------------------------------------------------------------------- #
# Aggregate
# --------------------------------------------------------------------------- #
class SensorHub:
    def __init__(self, cfg: dict):
        import psutil
        self.psutil = psutil
        psutil.cpu_percent(interval=None)  # prime; first call always returns 0.0
        self.lhm = LhmSource(cfg.get("lhm_url", "http://localhost:8085/data.json"))
        self.nv = NvidiaSource(int(cfg.get("nvidia_gpu_index", 0)))
        self.disks = DiskSource(cfg.get("disk_labels") or {}, cfg.get("disks_include"))
        self.show_igpu = bool(cfg.get("show_amd_igpu", False))

    def read(self) -> Metrics:
        m = Metrics()
        p = self.psutil
        m.cpu_load = p.cpu_percent(interval=None)
        vm = p.virtual_memory()
        m.ram_used_gb = (vm.total - vm.available) / 1024 ** 3
        m.ram_total_gb = vm.total / 1024 ** 3

        nv = self.nv.read()
        m.gpu_name = nv.get("name")
        m.gpu_temp = nv.get("temp")
        m.gpu_load = nv.get("load")
        m.gpu_power_w = nv.get("power")
        if nv.get("mem") is not None:
            m.gpu_vram_used_gb = nv["mem"].used / 1024 ** 3
            m.gpu_vram_total_gb = nv["mem"].total / 1024 ** 3

        lhm = self.lhm.read()
        if lhm:
            m.lhm_ok = True
            m.cpu_temp = lhm["cpu_temp"]
            if self.show_igpu:
                m.igpu_temp, m.igpu_load = lhm["igpu_temp"], lhm["igpu_load"]
            if m.gpu_temp is None:
                m.gpu_temp = lhm["nv_temp"]
            if m.gpu_load is None:
                m.gpu_load = lhm["nv_load"]

        m.disks = self.disks.read()
        return m


class DummySensors:
    """Plausible fake values for --simulate / --dummy. Drifts a little each read."""

    def __init__(self, seed: int = 7):
        self.rng = random.Random(seed)
        self.t = 0

    def read(self) -> Metrics:
        r = self.rng
        self.t += 1
        return Metrics(
            cpu_load=min(100, max(0, 23 + r.uniform(-6, 6))),
            cpu_temp=58.4 + r.uniform(-2, 2),
            ram_used_gb=14.2, ram_total_gb=31.1,
            gpu_name="NVIDIA GeForce RTX",
            gpu_load=min(100, max(0, 61 + r.uniform(-8, 8))),
            gpu_temp=64 + r.uniform(-1, 1),
            gpu_vram_used_gb=4.7, gpu_vram_total_gb=10.0, gpu_power_w=212.0,
            disks=[
                DiskRate("PhysicalDrive0", "SSD 1", 142.6 + r.uniform(-20, 20), 18.3),
                DiskRate("PhysicalDrive1", "SSD 2", 2.1, 0.4 + r.uniform(0, 1)),
            ],
            lhm_ok=True,
        )
