"""Main loop: render the active page, diff against the last frame, send changed regions.

Robustness rules (added after a screen froze with artifacts after about a day of running):
  * Page data (sensors, weather, RSS) is fetched on background worker threads, one persistent
    thread per page, so a slow or hung network call can never stall rendering.
  * Any error while sending a frame forgets the last frame, so the next send is a full redraw.
  * A full redraw is also forced every `full_redraw_seconds`, so any desync heals by itself.
  * A watchdog exits the process (non-zero) if no frame completes for `watchdog_seconds`; the
    supervisor in main.py (or Task Scheduler) then starts a fresh process.
  * Serial errors close the port, RESET the device if a write failed part-way (the only reliable
    way to get the firmware back in step), reopen with exponential backoff and redraw fully.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
import traceback

from PIL import Image, ImageChops

from . import lcd_rev_a as L
from .pages import PAGES

log = logging.getLogger(__name__)

BAND = 40                 # rows per diff band
FULL_FRAME_RATIO = 0.6    # if more than this fraction changed, just send the whole frame
WATCHDOG_EXIT_CODE = 3


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


class PageWorker:
    """One long-lived daemon thread per page that calls page.maybe_update().

    Background-only pages (data_interval >= 60 s, such as weather and posts) refresh even while
    hidden, so their data is ready when shown. Fast pages (metrics) only poll while active.
    """

    STUCK_WARN_SECONDS = 120

    def __init__(self, app: "App", page):
        self.app = app
        self.page = page
        self.busy_since: float | None = None
        self._warned_stuck = False
        self.wake = threading.Event()
        self.thread = threading.Thread(target=self._loop, name=f"page-{page.name}", daemon=True)

    def start(self):
        self.thread.start()

    def _loop(self):
        while not self.app._stop.is_set():
            page = self.page
            if self.app.page is page or page.data_interval >= 60:
                self.busy_since = time.monotonic()
                try:
                    page.maybe_update()
                except Exception:
                    log.exception("Page %s update failed", page.name)
                finally:
                    self.busy_since = None
                    self._warned_stuck = False
            self.wake.wait(min(1.0, max(0.1, page.data_interval / 4)))
            self.wake.clear()

    def check_stuck(self):
        b = self.busy_since
        if b is not None and not self._warned_stuck and time.monotonic() - b > self.STUCK_WARN_SECONDS:
            self._warned_stuck = True
            log.warning("Page %s update has been running for %.0f s (network or driver hang?). "
                        "Rendering continues with old data.", self.page.name, time.monotonic() - b)


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
        self._pending_screen: bool | None = None  # set by hotkey thread, applied by render loop
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._last_switch = time.monotonic()
        self.full_redraw_seconds = float(cfg.get("full_redraw_seconds") or 0)
        self._last_full = 0.0
        self.last_frame_ok = time.monotonic()   # watchdog heartbeat
        self.frames = 0
        self.full_frames = 0
        self.workers: list[PageWorker] = []

    # ---- background data -------------------------------------------------- #
    def start_workers(self):
        if self.workers:
            return
        self.workers = [PageWorker(self, p) for p in self.pages]
        for w in self.workers:
            w.start()

    # ---- page control (called from hotkey threads too) ------------------- #
    def switch(self, delta: int):
        with self._lock:
            self.index = (self.index + delta) % len(self.pages)
            self._last_switch = time.monotonic()
            log.info("Page -> %s", self.page.name)
            for w in self.workers:
                if w.page is self.page:
                    w.wake.set()

    def toggle_screen(self):
        """Hotkey thread: only records the request. The render loop owns the serial port."""
        with self._lock:
            self.screen_on = not self.screen_on
            self._pending_screen = self.screen_on
            log.info("Screen %s requested", "on" if self.screen_on else "off")

    @property
    def page(self):
        return self.pages[self.index]

    def stop(self):
        self._stop.set()

    def heartbeat(self):
        self.last_frame_ok = time.monotonic()

    def invalidate(self):
        """Forget what the screen shows, so the next frame is sent in full."""
        self.last_frame = None

    # ---- rendering ---------------------------------------------------------- #
    def push_frame(self, frame: Image.Image) -> int:
        """Send only changed rectangles. Returns bytes of pixel data sent.

        If anything goes wrong part-way, last_frame is cleared so the next call redraws the
        whole screen instead of diffing against a frame the device never fully received."""
        now = time.monotonic()
        if self.full_redraw_seconds > 0 and now - self._last_full >= self.full_redraw_seconds:
            self.last_frame = None
        rects = changed_rects(self.last_frame, frame)
        full = self.last_frame is None
        sent = 0
        try:
            for x0, y0, x1, y1 in rects:
                self.display.draw_image(frame.crop((x0, y0, x1, y1)), x0, y0)
                sent += (x1 - x0) * (y1 - y0) * 2
        except BaseException:
            self.last_frame = None
            raise
        self.last_frame = frame
        if full:
            self._last_full = now
            self.full_frames += 1
        return sent

    def _apply_pending_screen(self):
        want = self._pending_screen
        if want is None:
            return
        self._pending_screen = None
        try:
            (self.display.screen_on if want else self.display.screen_off)()
        except BaseException:
            self._pending_screen = want  # retry after reconnect
            raise
        if want:
            self.invalidate()  # redraw fully when the screen comes back
        log.info("Screen %s", "on" if want else "off")

    def tick(self) -> int:
        """Render and send one frame. Serial errors propagate to the caller (reconnect)."""
        with self._lock:
            page = self.page
            screen_on = self.screen_on
        self._apply_pending_screen()
        if not self.workers:
            # no background workers (simulate / tests): update inline
            try:
                page.maybe_update()
            except Exception:
                log.exception("Page %s update failed", page.name)
        try:
            frame = page.render(self.display.width, self.display.height)
        except Exception:
            log.exception("Page %s render failed", page.name)
            self.heartbeat()   # the loop is alive; a broken page is not a hang
            return 0
        if not screen_on:
            self.heartbeat()
            return 0
        sent = self.push_frame(frame)
        self.frames += 1
        self.heartbeat()
        return sent

    def run(self):
        interval = self.cfg["update_interval"]
        cycle = float(self.cfg.get("page_cycle_seconds") or 0)
        while not self._stop.is_set():
            t0 = time.monotonic()
            if cycle > 0 and t0 - self._last_switch >= cycle:
                self.switch(+1)
            self.tick()
            for w in self.workers:
                w.check_stuck()
            self._stop.wait(max(0.05, interval - (time.monotonic() - t0)))


# --------------------------------------------------------------------------- #
# Watchdog and status logging
# --------------------------------------------------------------------------- #
def dump_threads() -> str:
    frames = sys._current_frames()
    out = []
    for t in threading.enumerate():
        out.append(f"--- thread {t.name} (daemon={t.daemon})")
        f = frames.get(t.ident)
        if f is not None:
            out.append("".join(traceback.format_stack(f)))
    return "\n".join(out)


def _rss_mb() -> float | None:
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1e6
    except Exception:
        return None


class Watchdog:
    """Daemon thread. Exits the process with WATCHDOG_EXIT_CODE if the heartbeat stops.

    Also logs a status line every `status_minutes` (frames, memory, threads) so slow leaks are
    visible in the log, and exits if the supervising parent process has gone away."""

    def __init__(self, app_ref, timeout: float, status_minutes: float = 30,
                 parent_pid: int | None = None, exit_fn=None):
        self.app_ref = app_ref          # callable returning the current heartbeat source
        self.timeout = float(timeout)
        self.status_seconds = float(status_minutes or 0) * 60
        self.parent_pid = parent_pid
        self.exit_fn = exit_fn or _hard_exit
        self.thread = threading.Thread(target=self._loop, name="watchdog", daemon=True)
        self._stop = threading.Event()

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self._stop.set()

    def check(self, now: float | None = None) -> bool:
        """Return False (and trigger exit) if the heartbeat is stale."""
        now = time.monotonic() if now is None else now
        src = self.app_ref()
        if src is None or self.timeout <= 0:
            return True
        age = now - src.last_frame_ok
        if age > self.timeout:
            log.critical("Watchdog: no frame completed for %.0f s (limit %.0f s). "
                         "Exiting with code %d so it gets restarted.\n%s",
                         age, self.timeout, WATCHDOG_EXIT_CODE, dump_threads())
            self.exit_fn(WATCHDOG_EXIT_CODE)
            return False
        return True

    def _parent_alive(self) -> bool:
        if not self.parent_pid:
            return True
        try:
            import psutil
            return psutil.pid_exists(self.parent_pid)
        except Exception:
            return True

    def _loop(self):
        last_status = time.monotonic()
        while not self._stop.wait(5):
            if not self.check():
                return
            if not self._parent_alive():
                log.warning("Supervisor process %s is gone, exiting", self.parent_pid)
                self.exit_fn(0)
                return
            now = time.monotonic()
            if self.status_seconds > 0 and now - last_status >= self.status_seconds:
                last_status = now
                src = self.app_ref()
                rss = _rss_mb()
                log.info("Status: frames=%s full=%s bytes_sent=%s threads=%d rss=%s MB",
                         getattr(src, "frames", "?"), getattr(src, "full_frames", "?"),
                         getattr(getattr(src, "display", None), "bytes_sent", "?"),
                         threading.active_count(), f"{rss:.1f}" if rss else "?")


def _hard_exit(code: int):
    logging.shutdown()
    os._exit(code)


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


# --------------------------------------------------------------------------- #
# Hardware loop with reconnect
# --------------------------------------------------------------------------- #
class _Beat:
    """Heartbeat used by the watchdog while no App exists yet (first connect)."""
    def __init__(self):
        self.last_frame_ok = time.monotonic()


def backoff_delay(attempt: int, base: float = 2.0, cap: float = 60.0) -> float:
    """2, 4, 8, ... seconds, capped."""
    return min(cap, base * (2 ** max(0, attempt)))


def run_hardware(cfg: dict, sensors, lcd_factory=None, find_port=None, sleep=time.sleep,
                 parent_pid: int | None = None, max_consecutive_errors: int | None = None):
    """Open the screen, run forever, reconnect if the USB link drops or a write fails.

    lcd_factory/find_port/sleep are injectable for tests."""
    import serial
    orientation = L.Orientation.parse(cfg["orientation"])
    find_port = find_port or L.find_port
    lcd_factory = lcd_factory or (lambda port: L.LcdRevA(
        port, orientation, cfg["reset_on_startup"], cfg.get("serial_write_timeout", 5.0)))
    cap = float(cfg.get("reconnect_backoff_max_seconds", 60))
    reset_on_error = bool(cfg.get("reset_on_desync", True))
    max_errors = max_consecutive_errors if max_consecutive_errors is not None else int(
        cfg.get("max_consecutive_errors", 0) or 0)

    app: App | None = None
    beat = _Beat()
    wd = Watchdog(lambda: app or beat, cfg.get("watchdog_seconds", 60),
                  cfg.get("status_log_minutes", 30), parent_pid)
    if float(cfg.get("watchdog_seconds", 60) or 0) > 0:
        wd.start()

    attempt = 0
    while True:
        port = find_port(cfg["com_port"], cfg["com_port_fallback"])
        lcd = lcd_factory(port)
        needs_reset = False
        started = None
        try:
            lcd.open()
            lcd.set_brightness(cfg["brightness"])
            if app is None:
                app = App(cfg, lcd, sensors)
                app.start_workers()
                install_hotkeys(app, cfg.get("hotkeys", {}))
            else:
                app.display = lcd
                if not app.screen_on:
                    app._pending_screen = False
            app.invalidate()        # full redraw after (re)connect
            app.heartbeat()
            if attempt:
                log.info("Reconnected to %s after %d failed attempt(s)", port, attempt)
            started = time.monotonic()
            app.run()
            return 0
        except KeyboardInterrupt:
            log.info("Stopping")
            if cfg.get("screen_off_on_exit"):
                try:
                    lcd.screen_off()
                except Exception:
                    pass
            if app:
                app.stop()
            return 0
        except (serial.SerialException, OSError) as e:
            log.error("Serial error on %s: %s", port, e)
            if "PermissionError" in str(e) or "Access is denied" in str(e):
                log.error("Port is busy. Is another program (vendor app?) holding %s?", port)
            needs_reset = not getattr(lcd, "in_sync", True)
        except Exception:
            log.exception("Unexpected error in the render loop")
            needs_reset = not getattr(lcd, "in_sync", True)
        finally:
            if app is not None:
                app.invalidate()
            lcd.close()

        # A run that lasted a while counts as healthy: restart the backoff from the bottom.
        if started is not None and time.monotonic() - started > 300:
            attempt = 0
        if needs_reset and reset_on_error and hasattr(lcd, "reset_device"):
            log.warning("A write failed part-way; the screen is out of step. Resetting it.")
            lcd.reset_device()
        delay = backoff_delay(attempt, cap=cap)
        attempt += 1
        if max_errors and attempt >= max_errors:
            log.critical("%d consecutive connection failures, exiting so a fresh process starts",
                         attempt)
            wd.stop()
            return 2
        log.info("Reconnecting in %.0f s (attempt %d)", delay, attempt)
        # keep the watchdog quiet while we wait on purpose (it guards hangs, not unplugged USB)
        left = delay
        while left > 0:
            (app or beat).last_frame_ok = time.monotonic()
            step = min(5.0, left)
            sleep(step)
            left -= step
        (app or beat).last_frame_ok = time.monotonic()
