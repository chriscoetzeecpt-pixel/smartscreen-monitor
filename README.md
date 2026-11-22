# smartscreen - local driver for the Turing Smart Screen 3.5" (Rev A)

A small, fully local replacement for the vendor Turmo / TURZX / UsbMonitor software.
No cloud, no telemetry, no admin rights, no installer. It is plain Python you can read end to end.

* Device: Turing Smart Screen 3.5" Rev A, USB serial number `USB35INCHIPSV2`, VID 1A86 PID 5722 (typically COM3 on Windows)
* Protocol: taken from the open-source project
  [mathoudebine/turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python)
  (Rev A path). Raw RGB565 pixels over USB serial. **The protocol has no firmware-write command**, so
  nothing this app sends can brick the screen. A garbled picture is fixed by replugging the USB cable.

## Pages

| Page | What it shows | Status |
|---|---|---|
| `metrics` (default) | CPU load + temp, GPU load + temp, VRAM, GPU power, RAM, read/write MB/s for both SSDs | Done |
| `weather` | Day, date, current temperature and conditions, tomorrow's high/low, conditions, rain chance | Done (Open-Meteo, no API key) |
| `posts` | Numbered "top X" list from a pluggable source | Layout done, real source TODO |

Previews (rendered by `--simulate`, RGB565 colour depth) are in `previews/`.

## 1. Vendor app

Already removed (scheduled task `TURMO_ScheduleTask` pointing to the deleted `TURMO_5_4_4` folder; if the
task still shows in Task Scheduler, delete it from an admin terminal with
`schtasks /Delete /TN "TURMO_ScheduleTask" /F`). The CH34x / USB CDC serial driver is a standard Windows
driver and is still needed, so leave it.

Only one program can hold the COM port. If this app logs "Access is denied" on COM3, something else
(vendor app, another copy of this app, a serial terminal) still has it open.

## 2. Install (Python 3.14)

Copy this folder somewhere permanent, for example `C:\Tools\smartscreen`, then in PowerShell:

```powershell
cd C:\Tools\smartscreen
C:\Python314\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

All dependencies have Python 3.14 wheels for Windows x64 (checked 19 Nov 2026):
`pillow` (cp314), `psutil` (abi3), `numpy` (cp314, optional speed-up), and `pyserial`, `nvidia-ml-py`,
`keyboard` (pure Python). Note: `keyboard` (hotkeys) has not had a release since 2020; it still works, but it is
optional. Delete it from `requirements.txt` and the app simply runs without hotkeys.

## 3. First run

```powershell
.\.venv\Scripts\python.exe main.py --list-ports      # should show COM3 ... <-- screen
.\.venv\Scripts\python.exe main.py --test-pattern    # colour bars + "smartscreen OK"
.\.venv\Scripts\python.exe main.py                   # run (Ctrl+C to stop)
.\.venv\Scripts\python.exe main.py --page weather    # start on another page
```

Logs go to the `logs` folder next to `main.py` (rotating, 1 MB per file, 5 old files kept):

* `logs\smartscreen.log` - the screen driver: serial errors, reconnects, page fetch failures,
  uncaught exceptions from every thread, and a status line every 30 minutes (frames sent, memory,
  thread count) so slow leaks show up.
* `logs\supervisor.log` - worker starts and restarts with their exit codes.
* `logs\faulthandler.log` - only written if the Python interpreter itself crashes.

`python main.py` runs a small supervisor that starts the screen driver as a child process and
restarts it if it exits with an error. Use `--no-supervisor` to run everything in one process, which
is handy when debugging in a console.

If the test pattern shows the right colours but the wrong way round, change `orientation` in
`config.json` (`portrait`, `reverse_portrait`, `landscape`, `reverse_landscape`).

## 4. Configuration (`config.json`)

| Key | Default | Meaning |
|---|---|---|
| `com_port` | `AUTO` | `AUTO` matches serial number `USB35INCHIPSV2`, then VID/PID, else `com_port_fallback` (`COM3`) |
| `orientation` | `portrait` | Layouts adapt to portrait (320x480) and landscape (480x320) |
| `brightness` | `30` | Percent. Keep 20-40: the Rev A board runs hot when bright |
| `update_interval` | `1.0` | Seconds between frames |
| `active_page` | `metrics` | Page shown at start |
| `pages` | all three | Order used by hotkeys and rotation; remove a page to hide it |
| `page_cycle_seconds` | `0` | Auto-rotate pages every N seconds, 0 = off |
| `hotkeys` | Ctrl+Alt+Shift + Right / Left / Down | Next page, previous page, screen on/off |
| `screen_off_on_exit` | `true` | Blank the screen on Ctrl+C so it doesn't show frozen numbers |
| `reset_on_startup` | `false` | Leave off. RESET re-enumerates USB and can change the COM number |
| `lhm_url` | `http://localhost:8085/data.json` | LibreHardwareMonitor web server, set `""` to disable |
| `show_amd_igpu` | `false` | Also show the Radeon iGPU temperature (from LHM) on the GPU card |
| `disk_labels` | `{}` | Override names, e.g. `{"PhysicalDrive1": "Games"}` |
| `disks_include` | `null` | Limit to e.g. `["PhysicalDrive0", "PhysicalDrive1"]` |
| `weather.city / latitude / longitude` | Your City, 0.0, 0.0 | Set latitude/longitude to `null` to look the city up by name |
| `posts.*` | static placeholder | See "Posts page" below |
| `log_level` | `INFO` | `DEBUG`, `INFO` or `WARNING`. `-v` forces `DEBUG` |
| `status_log_minutes` | `30` | Status line in the log every N minutes, 0 = off |
| `serial_write_timeout` | `5.0` | Seconds before a stalled serial write counts as an error |
| `reset_on_desync` | `true` | RESET the screen after a write failed part-way, then redraw. This is what clears a garbled screen |
| `reconnect_backoff_max_seconds` | `60` | Reconnect waits 2, 4, 8 ... seconds, up to this |
| `max_consecutive_errors` | `0` | Exit (so the supervisor starts a fresh process) after this many failed reconnects in a row, 0 = keep trying |
| `full_redraw_seconds` | `300` | Resend the whole screen this often so any artifacts heal by themselves, 0 = off |
| `watchdog_seconds` | `60` | Exit with code 3 if no frame completes for this long (a hang). The supervisor restarts it. 0 = off |
| `network_timeout_seconds` | `10` | HTTP timeout for weather and RSS |
| `supervisor` | `true` | `python main.py` restarts the screen driver after a crash or hang |

All keys are optional. An older `config.json` without the newer keys simply gets the defaults above,
so you never need to recreate your config after an update.

Disk names are read once at start-up from `Win32_DiskDrive` (no admin) and matched to psutil's
`PhysicalDriveN` counters, so model names like "Samsung 1TB" appear automatically. Throughput is in
decimal MB/s (1 MB = 1,000,000 bytes), switching to GB/s above 1000 MB/s.

## 5. CPU temperature: LibreHardwareMonitor (optional)

Windows does not expose AMD CPU temperature to normal programs. CPU load, NVIDIA temp/load/VRAM and disk
throughput all work without it; without LHM the CPU (and AMD iGPU) temp tiles show `n/a` and nothing crashes.
LibreHardwareMonitor must be running with admin rights, and its web server enabled, or temperatures stay `n/a`.

1. Download the **latest** release from
   <https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases> and unzip it, for example to
   `C:\Tools\LibreHardwareMonitor`.
   **Security note:** LHM versions before 0.9.5 load the WinRing0 kernel driver, which has a known
   privilege-escalation vulnerability (CVE-2020-14979, any local program can use it to read/write
   physical memory). 0.9.5 and later replaced WinRing0 with the signed PawnIO driver. Use 0.9.5 or newer,
   and if an old LHM, Open Hardware Monitor, or vendor tool left `WinRing0x64.sys` loaded, remove it.
2. Run it as administrator (it needs admin rights to read temperature sensors). Then:
   * `Options > Remote Web Server > Run` (port 8085 is the default; check `http://localhost:8085/data.json` loads in a browser)
   * `Options > Start Minimized` and `Minimize To Tray` if you like
3. To have it always available after a reboot, start it elevated at every logon. Three options:

   **Task Scheduler (manual):**
   * Open Task Scheduler, then Create Task (not Create Basic Task)
   * General: name it e.g. "LibreHardwareMonitor Startup", and tick "Run with highest privileges"
   * Triggers: New, "At log on"
   * Actions: New, Start a program, `C:\Tools\LibreHardwareMonitor\LibreHardwareMonitor.exe`
   * Save. Windows may ask for an administrator password.

   **Or** use LHM's own `Options > Run On Windows Startup`.

   **Or** run the included script from an admin PowerShell, which also blocks port 8085 from the LAN:
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\setup_lhm_task.ps1 -LhmExe "C:\Tools\LibreHardwareMonitor\LibreHardwareMonitor.exe"
   ```
   The LHM web server listens on all network interfaces, so if you don't use the script, add the firewall
   rule yourself: `New-NetFirewallRule -DisplayName "Block LHM web server (LAN)" -Direction Inbound -Protocol TCP -LocalPort 8085 -Action Block`.

This app never needs admin. It only reads LHM's JSON over localhost. For AMD CPUs it uses
`Core (Tctl/Tdie)`, falling back to `Package`, `CCD1 (Tdie)` and so on. Comma decimals
(for example `61,5 °C`) are handled.

## 6. Auto-start at logon (visible, not hidden)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1     # create
Start-ScheduledTask -TaskName "SmartScreen (local)"                        # start now
powershell -ExecutionPolicy Bypass -File scripts\uninstall_autostart.ps1   # remove
```

This creates one normal-privilege task, **"SmartScreen (local)"**, in the Task Scheduler Library. It runs
`.venv\Scripts\pythonw.exe main.py` (no console window) 20 s after you log on, and is visible and
removable like any other task. Restarts are handled in two layers: `main.py` restarts its screen
driver after a crash, a hang caught by the watchdog, or repeated serial errors, and Task Scheduler
restarts the whole thing (every minute, up to 999 times) if the supervising process itself dies.
Re-run `install_autostart.ps1` after updating to pick up the higher restart count. Stop it from Task Scheduler (End) or Task
Manager (`pythonw.exe`). When stopped that way, the screen keeps showing the last frame until the app starts
again or the USB is replugged.

Simpler alternative: a shortcut in `shell:startup` with target
`C:\Tools\smartscreen\.venv\Scripts\pythonw.exe C:\Tools\smartscreen\main.py` and "Start in" set to the folder.

### Frozen or garbled screen

The screen has no way to report what it shows, so the app protects itself instead:

* Page data (sensors, weather, RSS) is fetched on background threads. A slow network or a stuck
  driver call can no longer stall drawing.
* If a serial write times out or only partly goes through, the device is left waiting for the rest
  of an image and would draw every later byte as pixels (the garbled look). The app then RESETs the
  screen, reopens the port (with backoff), runs the start-up handshake again and redraws the whole
  frame.
* The whole screen is also resent every `full_redraw_seconds`, so a missed partial update cannot
  stay on screen for long.
* If no frame completes for `watchdog_seconds`, the driver logs every thread's stack and exits with
  code 3. The supervisor starts a fresh one.

If it still freezes, check `logs\smartscreen.log` around the time it stopped changing. Lines like
`Serial error ... Write timeout` point at the USB link (try another port or cable, and turn off USB
selective suspend in the Windows power plan), and `Watchdog:` lines show where it was stuck.

## 7. Posts page (pluggable)

`smartscreen/posts_providers.py` defines one interface: `get_posts(limit, options) -> list[Post | str]`.

* `"provider": "static"` - items from `posts.options.items` (current placeholder)
* `"provider": "rss"` - any RSS/Atom feed, e.g. `"options": {"url": "https://feeds.bbci.co.uk/news/world/rss.xml"}` (tested working)
* `"provider": "plugin"` - your own code: create `my_posts.py` next to `main.py` with
  `def get_posts(limit, options): return ["first", "second"]` and set `"options": {"function": "my_posts:get_posts"}`

TODO: an X source. No credentials are stored in this project. If one is added, keep the token in an
environment variable or Windows Credential Manager, never in `config.json`.

## 8. Adding a page

Create `smartscreen/pages/mypage.py` with a `Page` subclass (`update()` fetches data, `render(w, h)`
returns a PIL image), register it in `smartscreen/pages/__init__.py`, add its name to `pages` in
`config.json`. The app diffs every frame and only sends changed rectangles, so pages can just redraw
everything each tick.

## 9. Development and checks

```powershell
.\.venv\Scripts\python.exe tests\test_protocol.py            # header/RGB565/orientation/LHM parsing checks
.\.venv\Scripts\python.exe -m pytest tests                    # all checks, incl. reconnect/watchdog with a mocked port (pip install pytest)
.\.venv\Scripts\python.exe main.py --simulate --both-orientations [--live-weather] [--live-sensors]
```

`--simulate` renders each page to `previews\<page>_<orientation>.png` and reports how many bytes the
partial-update logic would send. The header packing is checked against a literal copy of the upstream
`SendCommand` over 5,000 random inputs.

## Layout

```
main.py                     CLI entry point
config.json                 your settings
smartscreen/lcd_rev_a.py    serial protocol + simulated display
smartscreen/sensors.py      psutil, NVML, LibreHardwareMonitor
smartscreen/app.py          main loop, diffing, hotkeys, USB reconnect, watchdog
smartscreen/render.py       theme, fonts, drawing helpers
smartscreen/pages/          metrics, weather, posts
smartscreen/weather_api.py  Open-Meteo client
smartscreen/posts_providers.py
fonts/                      Barlow (SIL Open Font License, see OFL-Barlow.txt)
scripts/                    autostart and LHM task scripts
tests/test_protocol.py      protocol and parsing checks
tests/test_reliability.py   reconnect, redraw, watchdog and config checks (mocked serial)
logs/                       runtime logs (not in git)
previews/                   simulated renders
```
