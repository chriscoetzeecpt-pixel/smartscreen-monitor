"""
Turing Smart Screen 3.5" (Rev A) serial protocol.

Protocol details follow the open-source project mathoudebine/turing-smart-screen-python
(library/lcd/lcd_comm_rev_a.py). Summary:

  * USB CDC serial, 115200 baud, RTS/CTS hardware flow control.
  * Every command starts with a 6-byte header that packs four 10-bit coordinates
    (x, y, ex, ey) followed by a one-byte command ID.
  * DISPLAY_BITMAP is followed by raw RGB565 little-endian pixels, row-major,
    for the inclusive rectangle (x, y) - (ex, ey).
  * There is NO firmware-write command in this protocol. Nothing in this file can
    reflash or brick the device. A garbled image means the device lost step with the byte
    stream; the app recovers by sending RESET (see LcdRevA.reset_device), or replug the USB cable.

Two display back-ends share the same interface:
  * LcdRevA        - the real screen over a serial port.
  * SimulatedLcd   - keeps an in-memory framebuffer and writes PNGs, for testing
                     layouts without hardware (used by --simulate).
"""
from __future__ import annotations

import logging
import struct
import time
from enum import IntEnum
from pathlib import Path

from PIL import Image

log = logging.getLogger(__name__)

NATIVE_WIDTH = 320   # portrait width
NATIVE_HEIGHT = 480  # portrait height

SERIAL_NUMBER = "USB35INCHIPSV2"
USB_VID = 0x1A86
USB_PID = 0x5722


class Command(IntEnum):
    HELLO = 69
    RESET = 101            # re-enumerates USB, COM number may change; we never send it by default
    CLEAR = 102
    TO_BLACK = 103
    SCREEN_OFF = 108
    SCREEN_ON = 109
    SET_BRIGHTNESS = 110   # value in the x field, 0..255, INVERTED (0 = brightest)
    SET_ORIENTATION = 121
    DISPLAY_BITMAP = 197


class Orientation(IntEnum):
    PORTRAIT = 0
    REVERSE_PORTRAIT = 1
    LANDSCAPE = 2
    REVERSE_LANDSCAPE = 3

    @classmethod
    def parse(cls, name: str) -> "Orientation":
        key = str(name).strip().upper().replace("-", "_").replace(" ", "_")
        try:
            return cls[key]
        except KeyError:
            raise ValueError(f"Unknown orientation '{name}'. Use one of: "
                             + ", ".join(o.name.lower() for o in cls)) from None

    @property
    def is_landscape(self) -> bool:
        return self in (Orientation.LANDSCAPE, Orientation.REVERSE_LANDSCAPE)


def size_for(orientation: Orientation) -> tuple[int, int]:
    """(width, height) of the drawing surface for an orientation."""
    if orientation.is_landscape:
        return NATIVE_HEIGHT, NATIVE_WIDTH
    return NATIVE_WIDTH, NATIVE_HEIGHT


# --------------------------------------------------------------------------- #
# Pure packing helpers (unit tested in tests/test_protocol.py)
# --------------------------------------------------------------------------- #
def pack_header(x: int, y: int, ex: int, ey: int, cmd: int) -> bytes:
    """Pack four 10-bit coordinates and a command byte into the 6-byte header."""
    for name, v in (("x", x), ("y", y), ("ex", ex), ("ey", ey)):
        if not 0 <= v <= 1023:
            raise ValueError(f"{name}={v} does not fit in 10 bits")
    if not 0 <= cmd <= 255:
        raise ValueError(f"cmd={cmd} does not fit in a byte")
    return bytes((
        (x >> 2) & 0xFF,
        (((x & 3) << 6) + (y >> 4)) & 0xFF,
        (((y & 15) << 4) + (ex >> 6)) & 0xFF,
        (((ex & 63) << 2) + (ey >> 8)) & 0xFF,
        ey & 0xFF,
        cmd & 0xFF,
    ))


def unpack_header(b: bytes) -> tuple[int, int, int, int, int]:
    """Inverse of pack_header, used only for tests and debugging."""
    x = (b[0] << 2) | (b[1] >> 6)
    y = ((b[1] & 0x3F) << 4) | (b[2] >> 4)
    ex = ((b[2] & 0x0F) << 6) | (b[3] >> 2)
    ey = ((b[3] & 0x03) << 8) | b[4]
    return x, y, ex, ey, b[5]


def pack_orientation(orientation: Orientation) -> bytes:
    """16-byte SET_ORIENTATION packet: header (all zero coords) + orientation + size."""
    width, height = size_for(orientation)
    buf = bytearray(16)
    buf[0:6] = pack_header(0, 0, 0, 0, Command.SET_ORIENTATION)
    buf[6] = orientation.value + 100
    buf[7:9] = struct.pack(">H", width)
    buf[9:11] = struct.pack(">H", height)
    return bytes(buf)


def brightness_to_raw(percent: float) -> int:
    """User brightness 0-100 % -> device value 0-255 where 0 is BRIGHTEST."""
    percent = max(0.0, min(100.0, float(percent)))
    return int(255 - (percent / 100.0) * 255)


def rgb565_le(image: Image.Image) -> bytes:
    """Convert a PIL image to RGB565 little-endian bytes, row-major.

    px = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3), stored as '<u2'.
    Uses numpy if available (fast), otherwise a pure-Python fallback.
    """
    img = image.convert("RGB")
    try:
        import numpy as np  # optional speed-up
        a = np.asarray(img, dtype=np.uint16)
        px = ((a[..., 0] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 2] >> 3)
        return px.astype("<u2").tobytes()
    except ImportError:
        raw = img.tobytes()
        out = bytearray(len(raw) // 3 * 2)
        j = 0
        for i in range(0, len(raw), 3):
            r, g, b = raw[i], raw[i + 1], raw[i + 2]
            v = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
            out[j] = v & 0xFF
            out[j + 1] = v >> 8
            j += 2
        return bytes(out)


# --------------------------------------------------------------------------- #
# Port discovery
# --------------------------------------------------------------------------- #
def find_port(preferred: str = "AUTO", fallback: str = "COM3") -> str:
    """Find the screen's COM port by its USB serial number, VID/PID, else fallback."""
    if preferred and preferred.upper() != "AUTO":
        return preferred
    from serial.tools import list_ports
    ports = list(list_ports.comports())
    for p in ports:
        if (p.serial_number or "") == SERIAL_NUMBER:
            log.info("Found screen on %s (serial number match)", p.device)
            return p.device
    for p in ports:
        if p.vid == USB_VID and p.pid == USB_PID:
            log.info("Found screen on %s (VID/PID match)", p.device)
            return p.device
    log.warning("Screen not auto-detected, falling back to %s", fallback)
    return fallback


# --------------------------------------------------------------------------- #
# Display back-ends
# --------------------------------------------------------------------------- #
class _DisplayBase:
    def __init__(self, orientation: Orientation):
        self.orientation = orientation
        self.width, self.height = size_for(orientation)

    # Interface
    def open(self): ...
    def close(self): ...
    def set_brightness(self, percent: float): ...
    def set_orientation(self, orientation: Orientation): ...
    def screen_on(self): ...
    def screen_off(self): ...
    def draw_image(self, image: Image.Image, x: int = 0, y: int = 0): ...


class LcdRevA(_DisplayBase):
    """Real hardware over serial."""

    def __init__(self, port: str, orientation: Orientation, reset_on_startup: bool = False,
                 write_timeout: float = 5.0):
        super().__init__(orientation)
        self.port = port
        self.reset_on_startup = reset_on_startup
        self.write_timeout = float(write_timeout)
        self.ser = None
        self.bytes_sent = 0
        # False once a write failed part-way. The device firmware then still expects the rest of
        # a bitmap and will treat the next commands as pixel data (garbled screen). Only a device
        # RESET reliably brings it back in step; closing and reopening the port does not.
        self.in_sync = True

    def open(self):
        import serial
        log.info("Opening %s at 115200 baud (rtscts)", self.port)
        # write_timeout stops us hanging forever if the device stalls flow control.
        self.ser = serial.Serial(self.port, 115200, timeout=1, write_timeout=self.write_timeout,
                                 rtscts=True)
        if self.reset_on_startup:
            # Not recommended: the device re-enumerates and may come back on another COM port.
            log.warning("Sending RESET, the COM port may disappear and change number")
            self._write(pack_header(0, 0, 0, 0, Command.RESET))
            time.sleep(5)
        self.in_sync = True
        self.hello()
        self.screen_on()
        self.set_orientation(self.orientation)

    def close(self):
        """Close the port. Never raises: a dead USB handle must not stop a reconnect."""
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception as e:
                log.debug("Error closing %s: %s", self.port, e)
            finally:
                self.ser = None

    def reset_device(self, wait: float = 5.0):
        """Send RESET to bring the firmware back to a known state, then close the port.
        The device re-enumerates on USB and may come back on a different COM port, so the
        caller must look the port up again before reopening. Never raises."""
        try:
            if self.ser is None:
                import serial
                self.ser = serial.Serial(self.port, 115200, timeout=1,
                                         write_timeout=self.write_timeout, rtscts=False)
            log.warning("Sending RESET to resync the screen (port may change number)")
            self.ser.write(pack_header(0, 0, 0, 0, Command.RESET))
            try:
                self.ser.flush()
            except Exception:
                pass
        except Exception as e:
            log.warning("RESET could not be sent on %s: %s", self.port, e)
        finally:
            self.close()
        time.sleep(wait)

    def _write(self, data: bytes):
        import serial
        if self.ser is None:
            raise serial.SerialException("Serial port is not open")
        try:
            n = self.ser.write(data)
        except Exception:
            self.in_sync = False
            raise
        if n is not None and n != len(data):
            self.in_sync = False
            raise serial.SerialTimeoutException(f"Partial write: {n} of {len(data)} bytes")
        self.bytes_sent += len(data)

    def hello(self):
        """Send HELLO and log any reply (used upstream only to detect sub-revision).
        An empty reply means the original Turing 3.5" variant; any reply is logged. Never fatal."""
        try:
            self.ser.reset_input_buffer()
            # Upstream sends six raw HELLO bytes (not a packed header) and reads 6 bytes back.
            self._write(bytes([Command.HELLO] * 6))
            reply = self.ser.read(6)
            self.ser.reset_input_buffer()
            log.info("HELLO reply: %s", reply.hex() if reply else "(none)")
        except Exception as e:  # never fatal
            log.debug("HELLO failed: %s", e)

    def set_brightness(self, percent: float):
        raw = brightness_to_raw(percent)
        log.info("Brightness %s%% (raw %d, 0 = brightest)", percent, raw)
        self._write(pack_header(raw, 0, 0, 0, Command.SET_BRIGHTNESS))

    def set_orientation(self, orientation: Orientation):
        self.orientation = orientation
        self.width, self.height = size_for(orientation)
        self._write(pack_orientation(orientation))

    def screen_on(self):
        self._write(pack_header(0, 0, 0, 0, Command.SCREEN_ON))

    def screen_off(self):
        self._write(pack_header(0, 0, 0, 0, Command.SCREEN_OFF))

    def draw_image(self, image: Image.Image, x: int = 0, y: int = 0):
        """Send an image whose top-left is at (x, y). Coordinates are in the
        current orientation's space; the device rotates internally."""
        w, h = image.size
        # Clip to screen
        if x >= self.width or y >= self.height:
            return
        if x + w > self.width:
            image = image.crop((0, 0, self.width - x, h))
            w = image.width
        if y + h > self.height:
            image = image.crop((0, 0, w, self.height - y))
            h = image.height
        if w <= 0 or h <= 0:
            return
        self._write(pack_header(x, y, x + w - 1, y + h - 1, Command.DISPLAY_BITMAP))
        data = rgb565_le(image)
        chunk = self.width * 8  # matches upstream chunking
        for i in range(0, len(data), chunk):
            self._write(data[i:i + chunk])


class SimulatedLcd(_DisplayBase):
    """In-memory framebuffer. Each draw is pasted onto the frame; save() writes a PNG.

    Pixels are round-tripped through RGB565 so previews show the real colour depth.
    """

    def __init__(self, orientation: Orientation, out_dir: Path | None = None):
        super().__init__(orientation)
        self.out_dir = Path(out_dir) if out_dir else None
        self.frame = Image.new("RGB", (self.width, self.height), (0, 0, 0))
        self.updates: list[tuple[int, int, int, int]] = []
        self.bytes_sent = 0

    def set_orientation(self, orientation: Orientation):
        self.orientation = orientation
        self.width, self.height = size_for(orientation)
        self.frame = Image.new("RGB", (self.width, self.height), (0, 0, 0))

    def draw_image(self, image: Image.Image, x: int = 0, y: int = 0):
        img = _rgb565_roundtrip(image)
        self.frame.paste(img, (x, y))
        self.updates.append((x, y, x + image.width - 1, y + image.height - 1))
        self.bytes_sent += 6 + image.width * image.height * 2

    def save(self, name: str) -> Path:
        assert self.out_dir is not None
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / name
        self.frame.save(path)
        return path


def _rgb565_roundtrip(image: Image.Image) -> Image.Image:
    data = rgb565_le(image)
    w, h = image.size
    out = bytearray(w * h * 3)
    for i in range(w * h):
        v = data[2 * i] | (data[2 * i + 1] << 8)
        r = (v >> 11) & 0x1F
        g = (v >> 5) & 0x3F
        b = v & 0x1F
        out[3 * i] = (r << 3) | (r >> 2)
        out[3 * i + 1] = (g << 2) | (g >> 4)
        out[3 * i + 2] = (b << 3) | (b >> 2)
    return Image.frombytes("RGB", (w, h), bytes(out))
