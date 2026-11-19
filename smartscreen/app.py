"""Main loop: render the active page, diff against the last frame, send changed regions."""
from __future__ import annotations

import logging
import threading
import time

from PIL import Image, ImageChops

from . import lcd_rev_a as L
from .pages import PAGES

log = logging.getLogger(__name__)

BAND = 40                 # rows per diff band
FULL_FRAME_RATIO = 0.6    # if more than this fraction changed, just send the whole frame


def changed_rects(old: Image.Image | None, new: Image.Image) -> list[tuple[int, int, int, int]]:
    """Return (x0, y0, x1, y1) boxes (x1/y1 exclusive) covering pixels that differ.

    The frame is cut into horizontal bands; each band contributes at most one bounding
    box. Cheap to compute and keeps transfers small for things like a ticking clock or a
    changing number.
    """
    w, h = new.size
    if old is None or old.size != new.size:
        return [(0, 0, w, h)]
    diff = ImageChops.difference(old, new)
    rects, area = [], 0
    for y in range(0, h, BAND):
        bb = diff.crop((0, y, w, min(h, y + BAND))).getbbox()
        if bb:
            r = (bb[0], y + bb[1], bb[2], y + bb[3])
            rects.append(r)
            area += (r[2] - r[0]) * (r[3] - r[1])
    if area > FULL_FRAME_RATIO * w * h:
        return [(0, 0, w, h)]
    return rects


class App:
    def __init__(self, cfg: dict, display, sensors, page_factory=None):
        self.cfg = cfg
        self.display = display
        self.sensors = sensors
        names = [p for p in cfg["pages"] if p in PAGES] or ["metrics"]
        factory = page_factory or (lambda name: PAGES[name](cfg, sensors))
        self.pages = [factory(n) for n in names]
        active = cfg.get("active_page", "metrics")
        self.index = names.index(active) if active in names else 0
        self.last_frame: Image.Image | None = None
        self.screen_on = True
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._last_switch = time.monotonic()

    # ---- page control (called from hotkey threads too) ------------------- #
    def switch(self, delta: int):
        with self._lock:
            self.index = (self.index + delta) % len(self.pages)
            self._last_switch = time.monotonic()
            log.info("Page -> %s", self.page.name)
            self.page.maybe_update(force=self.page._last_update == 0)

    def toggle_screen(self):
        with self._lock:
            self.screen_on = not self.screen_on
            (self.display.screen_on if self.screen_on else self.display.screen_off)()
            log.info("Screen %s", "on" if self.screen_on else "off")

    @property
    def page(self):
        return self.pages[self.index]

    def stop(self):
        self._stop.set()

    # ---- rendering ---------------------------------------------------------- #
    def push_frame(self, frame: Image.Image) -> int:
        """Send only changed rectangles. Returns bytes of pixel data sent."""
        sent = 0
        for x0, y0, x1, y1 in changed_rects(self.last_frame, frame):
            self.display.draw_image(frame.crop((x0, y0, x1, y1)), x0, y0)
            sent += (x1 - x0) * (y1 - y0) * 2
        self.last_frame = frame
        return sent

    def tick(self) -> int:
        with self._lock:
            page = self.page
            try:
                page.maybe_update()
            except Exception:
                log.exception("Page %s update failed", page.name)
            frame = page.render(self.display.width, self.display.height)
            if not self.screen_on:
                return 0
            return self.push_frame(frame)

    def run(self):
        interval = self.cfg["update_interval"]
        cycle = float(self.cfg.get("page_cycle_seconds") or 0)
        while not self._stop.is_set():
            t0 = time.monotonic()
            if cycle > 0 and t0 - self._last_switch >= cycle:
                self.switch(+1)
            self.tick()
            self._stop.wait(max(0.05, interval - (time.monotonic() - t0)))


def install_hotkeys(app: App, hk: dict):
    """Global hotkeys via the 'keyboard' package. Optional: skipped if not installed.
    Works without admin, but keys pressed while an elevated window has focus are not seen."""
    if not hk.get("enabled"):
        return
    try:
        import keyboard
    except ImportError:
        log.info("'keyboard' package not installed, hotkeys disabled")
        return
    try:
        if hk.get("next_page"):
            keyboard.add_hotkey(hk["next_page"], lambda: app.switch(+1))
        if hk.get("prev_page"):
            keyboard.add_hotkey(hk["prev_page"], lambda: app.switch(-1))
        if hk.get("toggle_screen"):
            keyboard.add_hotkey(hk["toggle_screen"], app.toggle_screen)
        log.info("Hotkeys: next=%s prev=%s toggle=%s", hk.get("next_page"), hk.get("prev_page"),
                 hk.get("toggle_screen"))
    except Exception as e:
        log.warning("Could not register hotkeys: %s", e)


def run_hardware(cfg: dict, sensors):
    """Open the screen, run forever, reconnect if the USB link drops."""
    import serial
    orientation = L.Orientation.parse(cfg["orientation"])
    app = None
    while True:
        port = L.find_port(cfg["com_port"], cfg["com_port_fallback"])
        lcd = L.LcdRevA(port, orientation, cfg["reset_on_startup"])
        try:
            lcd.open()
            lcd.set_brightness(cfg["brightness"])
            if app is None:
                app = App(cfg, lcd, sensors)
                install_hotkeys(app, cfg.get("hotkeys", {}))
            else:
                app.display = lcd
                app.last_frame = None   # force a full redraw after reconnect
            app.run()
            return
        except serial.SerialException as e:
            log.error("Serial error on %s: %s. Retrying in 5 s.", port, e)
            if "PermissionError" in str(e) or "Access is denied" in str(e):
                log.error("Port is busy. Is another program (vendor app?) holding %s?", port)
        except KeyboardInterrupt:
            log.info("Stopping")
            if cfg.get("screen_off_on_exit"):
                try:
                    lcd.screen_off()
                except Exception:
                    pass
            return
        finally:
            lcd.close()
        time.sleep(5)
