"""
smartscreen - local driver for the Turing Smart Screen 3.5" (Rev A).

Examples (run from this folder):
  python main.py                      run on the real screen using config.json
  python main.py --list-ports         show serial ports and which one looks like the screen
  python main.py --test-pattern       draw colour bars + text once, to prove the link works
  python main.py --simulate           render every page to previews/*.png with dummy values
  python main.py --simulate --both-orientations --live-weather
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # so posts plugins (my_posts.py) next to main.py can be imported

from smartscreen import config as C           # noqa: E402
from smartscreen import lcd_rev_a as L        # noqa: E402


def setup_logging(verbose: bool):
    (HERE / "logs").mkdir(exist_ok=True)
    handlers = [logging.handlers.RotatingFileHandler(HERE / "logs" / "smartscreen.log",
                                                     maxBytes=1_000_000, backupCount=2,
                                                     encoding="utf-8")]
    if sys.stderr is not None:  # pythonw has no console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        handlers=handlers)


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
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    setup_logging(args.verbose)
    cfg = C.load(Path(args.config))
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
    run_hardware(cfg, SensorHub(cfg))


if __name__ == "__main__":
    main()
