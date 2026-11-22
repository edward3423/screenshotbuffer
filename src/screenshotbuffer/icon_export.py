"""Export the app icon for packaging.

Usage:
    python -m screenshotbuffer.icon_export ico OUTPUT.ico          (Windows)
    python -m screenshotbuffer.icon_export iconset OUTPUT.iconset  (macOS, for iconutil)
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QGuiApplication

from .ui.style import ACCENT, draw_icon

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def ico_bytes(pngs: dict[int, bytes]) -> bytes:
    """Assemble an ICO container whose images are PNG-compressed (Vista+)."""
    sizes = sorted(pngs)
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = len(header) + 16 * len(sizes)
    entries, blobs = b"", b""
    for size in sizes:
        data = pngs[size]
        dim = 0 if size >= 256 else size  # 0 means 256 in ICO directories
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset + len(blobs))
        blobs += data
    return header + entries + blobs


def png_bytes(size: int) -> bytes:
    """The icon rendered at ``size`` pixels as PNG. Needs a QGuiApplication."""
    buf = QByteArray()
    dev = QBuffer(buf)
    dev.open(QIODevice.OpenModeFlag.WriteOnly)
    draw_icon(size, ACCENT, paused=False).save(dev, "PNG")
    return bytes(buf.data())


def write_ico(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ico_bytes({size: png_bytes(size) for size in SIZES}))


def write_iconset(directory: Path) -> None:
    """The PNG set ``iconutil -c icns`` turns into an .icns file."""
    directory.mkdir(parents=True, exist_ok=True)
    for base in (16, 32, 128, 256, 512):
        (directory / f"icon_{base}x{base}.png").write_bytes(png_bytes(base))
        (directory / f"icon_{base}x{base}@2x.png").write_bytes(png_bytes(base * 2))


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in ("ico", "iconset"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    _app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    target = Path(sys.argv[2])
    if sys.argv[1] == "ico":
        write_ico(target)
    else:
        write_iconset(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
