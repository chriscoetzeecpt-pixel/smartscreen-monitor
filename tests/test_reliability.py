"""Reliability checks with a mocked serial port and display. No hardware or network needed.
Run: python -m pytest tests   (or python tests/test_reliability.py)"""
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import serial  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from smartscreen import app as A  # noqa: E402
from smartscreen import config as C  # noqa: E402
from smartscreen import lcd_rev_a as L  # noqa: E402
from smartscreen.pages.base import Page  # noqa: E402


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeSerial:
    """Stands in for serial.Serial. `limit` = bytes accepted before writes start failing."""

    def __init__(self, limit=None, short=False):
        self.limit, self.short, self.data, self.closed = limit, short, bytearray(), False

    def write(self, b):
        if self.limit is not None and len(self.data) + len(b) > self.limit:
            take = max(0, self.limit - len(self.data))
            self.data += b[:take]
            if self.short:
                return take  # pyserial-style short write
            raise serial.SerialTimeoutException("Write timeout")
        self.data += b
        return len(b)

    def read(self, n):
        return b""

    def reset_input_buffer(self):
        pass

    def flush(self):
        pass

    def close(self):
        self.closed = True


class FakeDisplay:
    def __init__(self, fail_after=None):
        self.width, self.height = 320, 480
        self.draws, self.fail_after, self.in_sync = [], fail_after, True
        self.opened = self.closed = self.resets = 0
        self.bytes_sent = 0
        self.on_draw = None

    def open(self):
        self.opened += 1

    def close(self):
        self.closed += 1

    def set_brightness(self, p):
        pass

    def screen_on(self):
        pass

    def screen_off(self):
        pass

    def reset_device(self, wait=0):
        self.resets += 1

    def draw_image(self, img, x=0, y=0):
        if self.fail_after is not None and len(self.draws) >= self.fail_after:
            self.in_sync = False
            raise serial.SerialTimeoutException("Write timeout")
        self.draws.append((x, y, img.width, img.height))
        if self.on_draw:
            self.on_draw(self)


class CounterPage(Page):
    """Draws a changing number in one corner so each tick is a small partial update."""
    name = "fake"
    data_interval = 0.0

    def __init__(self, cfg, sensors=None):
        super().__init__(cfg, sensors)
        self.n = 0

    def update(self):
        self.n += 1

    def render(self, w, h):
        img = Image.new("RGB", (w, h), (0, 0, 0))
        ImageDraw.Draw(img).rectangle((0, 0, 20, 20), fill=(self.n * 40 % 256, 0, 0))
        return img


def base_cfg(**over):
    cfg = C.load(Path("/nonexistent/config.json"))
    cfg.update(pages=["fake"], active_page="fake", update_interval=0.25, watchdog_seconds=0,
               status_log_minutes=0, hotkeys={"enabled": False}, page_cycle_seconds=0)
    cfg.update(over)
    return cfg


def make_app(cfg=None, display=None):
    cfg = cfg or base_cfg()
    return A.App(cfg, display or FakeDisplay(), None, page_factory=lambda n: CounterPage(cfg))


FULL = (0, 0, 320, 480)


# --------------------------------------------------------------------------- #
# Serial layer
# --------------------------------------------------------------------------- #
def test_write_timeout_marks_out_of_sync():
    lcd = L.LcdRevA("COM_TEST", L.Orientation.PORTRAIT)
    lcd.ser = FakeSerial(limit=10_000)
    img = Image.new("RGB", (320, 480))
    try:
        lcd.draw_image(img)
        raise AssertionError("expected a timeout")
    except serial.SerialTimeoutException:
        pass
    assert lcd.in_sync is False


def test_short_write_is_an_error():
    lcd = L.LcdRevA("COM_TEST", L.Orientation.PORTRAIT)
    lcd.ser = FakeSerial(limit=100, short=True)
    try:
        lcd.draw_image(Image.new("RGB", (320, 480)))
        raise AssertionError("expected a partial write error")
    except serial.SerialTimeoutException as e:
        assert "Partial write" in str(e)
    assert lcd.in_sync is False


def test_write_without_port_raises_serial_exception():
    lcd = L.LcdRevA("COM_TEST", L.Orientation.PORTRAIT)
    try:
        lcd.screen_on()
        raise AssertionError("expected SerialException")
    except serial.SerialException:
        pass


def test_close_never_raises():
    class Bad(FakeSerial):
        def close(self):
            raise OSError("device gone")
    lcd = L.LcdRevA("COM_TEST", L.Orientation.PORTRAIT)
    lcd.ser = Bad()
    lcd.close()
    assert lcd.ser is None


def test_full_frame_bytes_on_the_wire():
    lcd = L.LcdRevA("COM_TEST", L.Orientation.PORTRAIT)
    lcd.ser = FakeSerial()
    lcd.draw_image(Image.new("RGB", (320, 480)))
    assert len(lcd.ser.data) == 6 + 320 * 480 * 2 and lcd.in_sync


# --------------------------------------------------------------------------- #
# Frame diffing and redraw
# --------------------------------------------------------------------------- #
def test_error_mid_frame_forces_full_redraw():
    app = make_app()
    disp = app.display
    app.tick()
    assert disp.draws[-1] == FULL
    app.tick()
    assert disp.draws[-1] != FULL          # partial update
    disp.fail_after = len(disp.draws)
    try:
        app.tick()
        raise AssertionError("expected serial error")
    except serial.SerialTimeoutException:
        pass
    assert app.last_frame is None
    disp.fail_after = None
    app.tick()
    assert disp.draws[-1] == FULL


def test_periodic_full_redraw():
    app = make_app(base_cfg(full_redraw_seconds=30))
    disp = app.display
    app.tick()
    app.tick()
    assert disp.draws[-1] != FULL
    app._last_full -= 31
    app.tick()
    assert disp.draws[-1] == FULL
    assert app.full_frames == 2


def test_render_error_does_not_kill_loop():
    app = make_app()

    def boom(w, h):
        raise ValueError("bad font")
    app.pages[0].render = boom
    before = app.last_frame_ok
    time.sleep(0.01)
    assert app.tick() == 0
    assert app.last_frame_ok > before


def test_slow_page_update_does_not_block_rendering():
    cfg = base_cfg()
    app = make_app(cfg)
    page = app.pages[0]
    release = threading.Event()

    def slow_update():
        release.wait(5)
    page.update = slow_update
    app.start_workers()
    time.sleep(0.2)
    t0 = time.monotonic()
    for _ in range(5):
        app.tick()
    assert time.monotonic() - t0 < 1.0
    release.set()
    app.stop()


def test_screen_toggle_is_applied_by_render_loop():
    app = make_app()
    calls = []
    app.display.screen_off = lambda: calls.append("off")
    app.display.screen_on = lambda: calls.append("on")
    app.toggle_screen()
    assert calls == []                      # hotkey thread does not touch the port
    app.tick()
    assert calls == ["off"]
    app.toggle_screen()
    app.tick()
    assert calls == ["off", "on"] and app.display.draws[-1] == FULL


# --------------------------------------------------------------------------- #
# Reconnect loop
# --------------------------------------------------------------------------- #
def test_reconnect_resets_device_and_redraws(monkeypatch):
    monkeypatch.setitem(A.PAGES, "fake", CounterPage)
    cfg = base_cfg(com_port="COM_TEST")
    made = []

    def factory(port):
        if not made:
            d = FakeDisplay(fail_after=3)       # first link dies on the 4th draw
        else:
            d = FakeDisplay()

            def stop_after(disp):
                if len(disp.draws) >= 3:
                    app_holder[0].stop()
            d.on_draw = stop_after
        made.append(d)
        return d

    app_holder = []
    orig_app = A.App

    class SpyApp(orig_app):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            app_holder.append(self)
    monkeypatch.setattr(A, "App", SpyApp)
    sleeps = []
    rc = A.run_hardware(cfg, None, lcd_factory=factory, find_port=lambda a, b: "COM_TEST",
                        sleep=sleeps.append)
    assert rc == 0
    assert len(made) == 2
    assert made[0].resets == 1 and made[0].closed >= 1     # out of step -> RESET
    assert made[1].draws[0] == FULL                        # full redraw after reconnect
    assert sum(sleeps) == 2                                # first backoff step


def test_reconnect_gives_up_after_max_errors():
    cfg = base_cfg(com_port="COM_TEST")

    class Dead(FakeDisplay):
        def open(self):
            raise serial.SerialException("could not open port")
    rc = A.run_hardware(cfg, None, lcd_factory=lambda p: Dead(),
                        find_port=lambda a, b: "COM_TEST", sleep=lambda s: None,
                        max_consecutive_errors=4)
    assert rc == 2


def test_backoff_delay():
    assert [A.backoff_delay(i, cap=60) for i in range(7)] == [2, 4, 8, 16, 32, 60, 60]


# --------------------------------------------------------------------------- #
# Watchdog
# --------------------------------------------------------------------------- #
def test_watchdog_exits_when_frames_stop():
    exits = []

    class Src:
        last_frame_ok = time.monotonic() - 120
    wd = A.Watchdog(lambda: Src, timeout=60, status_minutes=0, exit_fn=exits.append)
    assert wd.check() is False and exits == [A.WATCHDOG_EXIT_CODE]
    Src.last_frame_ok = time.monotonic()
    assert wd.check() is True and exits == [A.WATCHDOG_EXIT_CODE]


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def test_old_config_gets_new_defaults():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "config.json"
        p.write_text(json.dumps({"brightness": 25, "weather": {"city": "X"}}), encoding="utf-8")
        cfg = C.load(p)
    assert cfg["brightness"] == 25 and cfg["weather"]["city"] == "X"
    assert cfg["weather"]["refresh_minutes"] == 15
    for key in ("watchdog_seconds", "full_redraw_seconds", "serial_write_timeout",
                "reset_on_desync", "network_timeout_seconds", "supervisor"):
        assert key in cfg


def test_bad_values_fall_back_or_clamp():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "config.json"
        p.write_text(json.dumps({"watchdog_seconds": "soon", "serial_write_timeout": 0,
                                 "update_interval": 2}), encoding="utf-8")
        cfg = C.load(p)
    assert cfg["watchdog_seconds"] == 60
    assert cfg["serial_write_timeout"] == 0.5


def test_example_config_has_all_reliability_keys():
    ex = json.loads((Path(__file__).resolve().parent.parent / "config.example.json")
                    .read_text(encoding="utf-8"))
    for key in ("log_level", "status_log_minutes", "serial_write_timeout", "reset_on_desync",
                "reconnect_backoff_max_seconds", "max_consecutive_errors", "full_redraw_seconds",
                "watchdog_seconds", "network_timeout_seconds", "supervisor"):
        assert ex[key] == C.DEFAULTS[key], key


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
