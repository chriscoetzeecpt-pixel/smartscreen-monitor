"""
smartscreen - local driver for the Turing Smart Screen 3.5" (Rev A).

Examples (run from this folder):
  python main.py                      run on the real screen using config.json
  python main.py --list-ports         show serial ports and which one looks like the screen
  python main.py --test-pattern       draw colour bars + text once, to prove the link works
  python main.py --simulate           render every page to previews/*.png with dummy values
  python main.py --simulate --both-orientations --live-weather
  python main.py --no-supervisor      run the screen loop in this process (no auto-restart)

By default the screen loop runs in a child process that is restarted automatically if it
crashes, hangs (watchdog) or keeps failing to reach the screen. Logs: logs/smartscreen.log
(screen driver) and logs/supervisor.log (restarts).
"""
from __future__ import annotations

import argparse
import faulthandler
import logging
import logging.handlers
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # so posts plugins (my_posts.py) next to main.py can be imported

from smartscreen import config as C           # noqa: E402
from smartscreen import lcd_rev_a as L        # noqa: E402


LOG_DIR = HERE / "logs"
LOG_FORMAT = "%(asctime)s %(levelname)-7s [%(process)d %(threadName)s] %(name)s: %(message)s"
_fault_file = None


def setup_logging(verbose: bool, filename: str = "smartscreen.log"):
    """Rotating file log in logs/ next to main.py, plus the console when there is one.

    Uncaught exceptions from the main thread and from every other thread are logged, and
    faulthandler writes a traceback to logs/faulthandler.log if the interpreter itself dies."""
    global _fault_file
    LOG_DIR.mkdir(exist_ok=True)
    handlers = [logging.handlers.RotatingFileHandler(LOG_DIR / filename, maxBytes=1_000_000,
                                                     backupCount=5, encoding="utf-8",
                                                     delay=False)]
    if sys.stderr is not None:  # pythonw has no console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format=LOG_FORMAT, handlers=handlers, force=True)
    logging.captureWarnings(True)
    log = logging.getLogger("smartscreen")

    def excepthook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            return
        log.critical("Uncaught exception", exc_info=(exc_type, exc, tb))

    def thread_excepthook(args):
        if args.exc_type is SystemExit:
            return
        log.critical("Uncaught exception in thread %s",
                     args.thread.name if args.thread else "?",
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = excepthook
    threading.excepthook = thread_excepthook
    try:
        _fault_file = open(LOG_DIR / "faulthandler.log", "a", encoding="utf-8")
        faulthandler.enable(file=_fault_file, all_threads=True)
    except Exception as e:  # never fatal
        log.debug("faulthandler not enabled: %s", e)


def apply_log_level(cfg: dict, verbose: bool):
    if verbose:
        return
    level = getattr(logging, str(cfg.get("log_level", "INFO")).upper(), logging.INFO)
    logging.getLogger().setLevel(level)


def list_ports():
    from serial.tools import list_ports as lp
    for p in lp.comports():
        tag = "  <-- screen" if (p.serial_number == L.SERIAL_NUMBER or
                                 (p.vid, p.pid) == (L.USB_VID, L.USB_PID)) else ""
        vidpid = f"{p.vid:04X}:{p.pid:04X}" if p.vid is not None else "----:----"
        print(f"{p.device:8} {vidpid}  sn={p.serial_number}  {p.description}{tag}")


def test_pattern(cfg):
    from PIL import Image, ImageDraw
    from smartscreen import render as R
    o = L.Orientation.parse(cfg["orientation"])
    lcd = L.LcdRevA(L.find_port(cfg["com_port"], cfg["com_port_fallback"]), o)
    lcd.open()
    lcd.set_brightness(cfg["brightness"])
    img = Image.new("RGB", (lcd.width, lcd.height))
    d = ImageDraw.Draw(img)
    cols = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255), (255, 255, 0), (0, 255, 255)]
    bw = lcd.width // len(cols)
    for i, c in enumerate(cols):
        d.rectangle((i * bw, 0, (i + 1) * bw, lcd.height // 2), fill=c)
    R.text(d, (lcd.width // 2, lcd.height * 3 // 4), "smartscreen OK", R.font(28, "bold"),
           anchor="mm")
    lcd.draw_image(img)
    lcd.close()
    print("Sent test pattern. Top half: red, green, blue, white, yellow, cyan bars.")


def simulate(cfg, args):
    from smartscreen.app import App
    from smartscreen.pages import PAGES
    from smartscreen.pages.weather import WeatherPage
    from smartscreen.sensors import DummySensors, SensorHub

    out = Path(args.out)
    sensors = SensorHub(cfg) if args.live_sensors else DummySensors()
    orients = ["portrait", "landscape"] if args.both_orientations else [cfg["orientation"]]

    def factory(name):
        if name == "weather":
            return WeatherPage(cfg, sensors, dummy=not args.live_weather)
        return PAGES[name](cfg, sensors)

    for oname in orients:
        o = L.Orientation.parse(oname)
        for name in cfg["pages"]:
            lcd = L.SimulatedLcd(o, out)
            pcfg = dict(cfg, pages=[name], active_page=name)
            app = App(pcfg, lcd, sensors, page_factory=factory)
            sent = []
            for _ in range(max(1, args.ticks)):
                app.page.maybe_update(force=True)
                sent.append(app.push_frame(app.page.render(lcd.width, lcd.height)))
            path = lcd.save(f"{name}_{oname}.png")
            extra = ""
            if len(sent) > 1:
                extra = f", later ticks sent {[s // 1024 for s in sent[1:]]} KiB (partial updates)"
            print(f"{path}  first frame {sent[0] // 1024} KiB{extra}")


def supervise(args) -> int:
    """Run the screen driver in a child process and restart it if it exits with an error
    (crash, watchdog, too many serial errors). Exit code 0 from the child means a normal stop.
    Logs go to logs/supervisor.log so two processes never rotate the same file."""
    log = logging.getLogger("smartscreen.supervisor")
    cmd = [sys.executable, str(HERE / "main.py"), "--worker", "--parent-pid", str(os.getpid()),
           "--config", args.config]
    if args.page:
        cmd += ["--page", args.page]
    if args.verbose:
        cmd.append("-v")
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    fails = 0
    while True:
        started = time.monotonic()
        log.info("Starting worker: %s", " ".join(cmd[1:]))
        child = subprocess.Popen(cmd, cwd=str(HERE), creationflags=flags)
        try:
            code = child.wait()
        except KeyboardInterrupt:
            log.info("Stopping (Ctrl+C)")
            child.terminate()
            try:
                child.wait(timeout=10)
            except Exception:
                child.kill()
            return 0
        if code == 0:
            log.info("Worker stopped normally")
            return 0
        fails = 0 if time.monotonic() - started > 600 else fails + 1
        delay = min(60, 5 * (2 ** min(fails, 4)) if fails else 5)
        log.error("Worker exited with code %s, restarting in %d s", code, delay)
        time.sleep(delay)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(HERE / "config.json"))
    ap.add_argument("--page", help="start on this page (overrides active_page)")
    ap.add_argument("--list-ports", action="store_true")
    ap.add_argument("--test-pattern", action="store_true")
    ap.add_argument("--simulate", action="store_true", help="render pages to PNG instead of serial")
    ap.add_argument("--out", default=str(HERE / "previews"), help="PNG output folder for --simulate")
    ap.add_argument("--both-orientations", action="store_true")
    ap.add_argument("--live-sensors", action="store_true", help="--simulate with real sensors")
    ap.add_argument("--live-weather", action="store_true", help="--simulate with real Open-Meteo data")
    ap.add_argument("--ticks", type=int, default=3, help="frames per page in --simulate")
    ap.add_argument("--no-supervisor", action="store_true",
                    help="run the screen loop in this process (no automatic restart)")
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--parent-pid", type=int, default=None, help=argparse.SUPPRESS)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    hardware_run = not (args.list_ports or args.test_pattern or args.simulate)
    try:
        cfg = C.load(Path(args.config))
    except Exception:
        setup_logging(args.verbose)
        logging.getLogger("smartscreen").critical("Could not read %s", args.config, exc_info=True)
        raise
    use_supervisor = (hardware_run and not args.worker and not args.no_supervisor
                      and cfg.get("supervisor", True))
    setup_logging(args.verbose, "supervisor.log" if use_supervisor else "smartscreen.log")
    apply_log_level(cfg, args.verbose)
    if use_supervisor:
        return supervise(args)
    if args.page:
        cfg["active_page"] = args.page

    if args.list_ports:
        return list_ports()
    if args.test_pattern:
        return test_pattern(cfg)
    if args.simulate:
        return simulate(cfg, args)

    from smartscreen.app import run_hardware
    from smartscreen.sensors import SensorHub
    log = logging.getLogger("smartscreen")
    log.info("Starting screen driver (pid %d, Python %s)", os.getpid(), sys.version.split()[0])
    return run_hardware(cfg, SensorHub(cfg), parent_pid=args.parent_pid)


if __name__ == "__main__":
    try:
        rc = main()
    except KeyboardInterrupt:
        rc = 0
    except SystemExit:
        raise
    except BaseException:
        logging.getLogger("smartscreen").critical("Fatal error", exc_info=True)
        rc = 1
    logging.shutdown()
    sys.exit(rc if isinstance(rc, int) else 0)
