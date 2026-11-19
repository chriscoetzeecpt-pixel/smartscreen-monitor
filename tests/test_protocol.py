"""Tiny self-checks for the protocol packing and sensor parsing. Run: python -m tests.test_protocol
(or with pytest). No hardware needed."""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from smartscreen import lcd_rev_a as L  # noqa: E402
from smartscreen.app import changed_rects  # noqa: E402
from smartscreen.sensors import extract_lhm, parse_lhm_value  # noqa: E402


def upstream_header(cmd, x, y, ex, ey):
    """Literal copy of mathoudebine/turing-smart-screen-python lcd_comm_rev_a.SendCommand."""
    b = bytearray(6)
    b[0] = (x >> 2)
    b[1] = (((x & 3) << 6) + (y >> 4))
    b[2] = (((y & 15) << 4) + (ex >> 6))
    b[3] = (((ex & 63) << 2) + (ey >> 8))
    b[4] = (ey & 255)
    b[5] = cmd
    return bytes(b)


def test_header_known_vector():
    # Full-screen portrait bitmap: (0,0)-(319,479), cmd 197
    assert L.pack_header(0, 0, 319, 479, 197).hex() == "000004fddfc5"
    # Brightness 30 % -> raw 178, in x field
    assert L.brightness_to_raw(30) == 178
    assert L.brightness_to_raw(100) == 0 and L.brightness_to_raw(0) == 255
    assert L.pack_header(178, 0, 0, 0, 110).hex() == "2c800000006e"


def test_header_matches_upstream_and_roundtrips():
    rng = random.Random(1)
    for _ in range(5000):
        x, y, ex, ey = (rng.randint(0, 1023) for _ in range(4))
        cmd = rng.choice(list(L.Command))
        h = L.pack_header(x, y, ex, ey, cmd)
        assert h == upstream_header(cmd, x, y, ex, ey)
        assert L.unpack_header(h) == (x, y, ex, ey, cmd)


def test_orientation_packet():
    p = L.pack_orientation(L.Orientation.LANDSCAPE)
    assert len(p) == 16
    assert p[:6] == bytes([0, 0, 0, 0, 0, 121])
    assert p[6] == 102
    assert p[7:9] == (480).to_bytes(2, "big") and p[9:11] == (320).to_bytes(2, "big")
    assert p[11:] == bytes(5)
    q = L.pack_orientation(L.Orientation.PORTRAIT)
    assert q[6] == 100 and q[7:11] == bytes([1, 64, 1, 224])


def test_rgb565():
    img = Image.new("RGB", (4, 1))
    img.putdata([(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)])
    data = L.rgb565_le(img)
    # red F800, green 07E0, blue 001F, white FFFF, little-endian
    assert data.hex() == "00f8e0071f00ffff"
    # arbitrary colour against the formula
    img = Image.new("RGB", (1, 1), (0x12, 0x9A, 0xC7))
    v = ((0x12 >> 3) << 11) | ((0x9A >> 2) << 5) | (0xC7 >> 3)
    assert L.rgb565_le(img) == v.to_bytes(2, "little")


def test_lhm_parsing():
    assert parse_lhm_value("45,3 °C") == 45.3 and parse_lhm_value("7.0 %") == 7.0
    tree = {"Text": "Sensor", "Children": [{"Text": "PC", "Children": [
        {"Text": "AMD Ryzen 7", "ImageURL": "images_icon/cpu.png", "Children": [
            {"Text": "Temperatures", "ImageURL": "images_icon/temperature.png", "Children": [
                {"Text": "Core (Tctl/Tdie)", "Value": "61,5 °C", "Children": [],
                 "SensorId": "/amdcpu/0/temperature/2", "Type": "Temperature"},
                {"Text": "CCD1 (Tdie)", "Value": "58,0 °C", "Children": [],
                 "SensorId": "/amdcpu/0/temperature/3", "Type": "Temperature"}]}]},
        {"Text": "AMD Radeon(TM) Graphics", "ImageURL": "images_icon/ati.png", "Children": [
            {"Text": "Temperatures", "Children": [
                {"Text": "GPU Core", "Value": "44.0 °C", "Children": []}]},
            {"Text": "Load", "Children": [
                {"Text": "GPU Core", "Value": "3.0 %", "Children": []}]}]},
        {"Text": "NVIDIA GeForce RTX", "ImageURL": "images_icon/nvidia.png", "Children": [
            {"Text": "Temperatures", "Children": [
                {"Text": "GPU Core", "Value": "63.0 °C", "Children": []}]}]}]}]}
    r = extract_lhm(tree)
    assert r["cpu_temp"] == 61.5
    assert r["igpu_temp"] == 44.0 and r["igpu_load"] == 3.0
    assert r["nv_temp"] == 63.0


def test_changed_rects():
    a = Image.new("RGB", (320, 480))
    b = a.copy()
    b.putpixel((100, 205), (255, 255, 255))
    assert changed_rects(a, b) == [(100, 205, 101, 206)]
    assert changed_rects(a, a.copy()) == []
    assert changed_rects(None, a) == [(0, 0, 320, 480)]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
    print("all checks passed")
