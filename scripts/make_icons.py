"""Regenerate app icons from src/emt/gui/resources/emt_app_icon.svg.

    python scripts/make_icons.py

Writes:
    src/emt/gui/resources/icons/emt_<size>.png   runtime window/taskbar icon (loaded by the app)
    packaging/emt.ico                            Windows executable icon (PyInstaller --icon)
    packaging/emt.icns                           macOS app bundle icon (macOS only: needs iconutil)

Sizes of 32 px and below are drawn with thicker lines so the glyph stays
legible. The outputs are committed, so builds don't need to run this.
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "src/emt/gui/resources/emt_app_icon.svg"
PNG_DIR = ROOT / "src/emt/gui/resources/icons"
PACKAGING = ROOT / "packaging"
SIZES = [16, 24, 32, 48, 64, 128, 256, 512, 1024]
SMALL_STROKE = {16: 30, 24: 26, 32: 23}  # stroke-width (SVG units) for small renders; normal is 17


def render(svg_text: str, size: int):
    from PySide6.QtCore import QByteArray, QRectF, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    r = QSvgRenderer(QByteArray(svg_text.encode()))
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r.render(p, QRectF(0, 0, size, size))
    p.end()
    return img


def svg_for(size: int, base: str) -> str:
    if size in SMALL_STROKE:
        return re.sub(r'stroke-width="17"', f'stroke-width="{SMALL_STROKE[size]}"', base)
    return base


def write_ico(pngs: dict[int, bytes], path: Path) -> None:
    """Multi-resolution .ico with PNG-compressed entries (supported since Windows Vista)."""
    sizes = [s for s in (16, 24, 32, 48, 64, 128, 256) if s in pngs]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries, blobs = b"", b""
    for s in sizes:
        data = pngs[s]
        dim = 0 if s >= 256 else s  # 0 means 256 in the ICO directory
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset + len(blobs))
        blobs += data
    path.write_bytes(header + entries + blobs)


def write_icns(png_dir: Path, path: Path) -> bool:
    if sys.platform != "darwin" or not shutil.which("iconutil"):
        print("skipping .icns (needs macOS iconutil)")
        return False
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "emt.iconset"
        iconset.mkdir()
        for s in (16, 32, 128, 256, 512):
            shutil.copy(png_dir / f"emt_{s}.png", iconset / f"icon_{s}x{s}.png")
            shutil.copy(png_dir / f"emt_{s * 2}.png" if (png_dir / f"emt_{s * 2}.png").exists()
                        else png_dir / f"emt_{s}.png", iconset / f"icon_{s}x{s}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(path)], check=True)
    return True


def main() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(ROOT / "src"))
    from emt.gui.app import _fix_qt_plugin_path

    _fix_qt_plugin_path()
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])  # noqa: F841 (needed for QPainter)
    base = SVG.read_text()
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    pngs: dict[int, bytes] = {}
    for s in SIZES:
        img = render(svg_for(s, base), s)
        img.save(str(PNG_DIR / f"emt_{s}.png"))
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "PNG")
        pngs[s] = bytes(ba.data())
    write_ico(pngs, PACKAGING / "emt.ico")
    made_icns = write_icns(PNG_DIR, PACKAGING / "emt.icns")
    print(f"wrote {len(SIZES)} PNGs, packaging/emt.ico" + (", packaging/emt.icns" if made_icns else ""))


if __name__ == "__main__":
    main()
