"""Generate a tiny but REAL PNG (stdlib only) for machine screenshots.

The image is a valid 8-bit RGB PNG that any decoder accepts, and its base64
is comfortably longer than 100 chars so it can be fed straight back into
``POST /v1/predict``.
"""

from __future__ import annotations

import base64
import struct
import zlib

SCREENSHOT_WIDTH = 64
SCREENSHOT_HEIGHT = 36


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def make_png(
    width: int = SCREENSHOT_WIDTH,
    height: int = SCREENSHOT_HEIGHT,
    rgb: tuple[int, int, int] = (30, 144, 255),
) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(rgb) * width
    idat = zlib.compress(row * height, level=9)
    return (
        b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")
    )


def screenshot_b64() -> str:
    return base64.b64encode(make_png()).decode("ascii")
