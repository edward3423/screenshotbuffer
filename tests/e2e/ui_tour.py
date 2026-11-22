"""Opens every UI surface of the real app and saves screenshots for review.

Run with:  uv run python tests/e2e/ui_tour.py OUTPUT_DIR

Uses a temporary settings directory and makes app windows visible to
screen capture so they appear in the screenshots.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["SCREENSHOTBUFFER_SHOW_IN_CAPTURE"] = "1"
os.environ["SCREENSHOTBUFFER_CONFIG_DIR"] = tempfile.mkdtemp(prefix="sbuf-tour-")

import mss
import mss.tools
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from screenshotbuffer.app import App
from screenshotbuffer.config import SettingsStore
from screenshotbuffer.ui import style

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "ui-tour")
OUT.mkdir(parents=True, exist_ok=True)


def pump(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.01)


def shot(name: str, rect: QRect | None = None, pad: int = 24) -> None:
    pump(0.25)
    with mss.MSS() as s:
        if rect is None:
            mon = s.monitors[1]
        else:
            r = rect.adjusted(-pad, -pad, pad, pad)
            mon = {"left": max(0, r.x()), "top": max(0, r.y()), "width": r.width(), "height": r.height()}
        img = s.grab(mon)
        mss.tools.to_png(img.rgb, img.size, output=str(OUT / f"{name}.png"))
    print("saved", name)


def frame(w: QWidget) -> QRect:
    return w.frameGeometry()


def main() -> None:
    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setWindowIcon(style.app_icon())
    app = App(qapp, SettingsStore())
    app.start()
    pump(2.0)

    app.show_settings()
    pump(0.8)
    sw = app._settings_window
    shot("01-settings", frame(sw))
    shot("02-preview", app.preview.geometry())

    app.show_shortcuts()
    pump(0.5)
    shot("03-shortcuts", frame(app._shortcuts_dialog))
    app._shortcuts_dialog.close()
    sw.close()

    menu = app.tray.contextMenu()
    menu.aboutToShow.emit()
    menu.adjustSize()
    menu.grab().save(str(OUT / "04-tray-menu.png"))
    print("saved 04-tray-menu")

    app.start_region_selection()
    pump(0.6)
    shot("05-selector-idle")
    overlay = app.selector._overlays[0]
    QTest.mousePress(overlay, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(500, 300))
    QTest.mouseMove(overlay, QPoint(900, 520))
    pump(0.3)
    shot("06-selector-drag")
    QTest.mouseRelease(overlay, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(1100, 650))
    pump(0.8)
    shot("07-editor", app.editor.geometry(), pad=40)

    # Drag the bottom-right handle with Shift (keep aspect ratio).
    ed = app.editor
    region = ed.region()
    se = ed.mapFromGlobal(region.bottomRight() + QPoint(6, 6))
    QTest.mousePress(ed, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, se)
    QTest.mouseMove(ed, se + QPoint(150, 10))
    QTest.mouseRelease(ed, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, se + QPoint(150, 10))
    pump(0.3)
    r2 = ed.region()
    print("aspect before", region.width() / region.height(), "after", r2.width() / r2.height(), r2)
    shot("08-editor-resized", ed.geometry(), pad=40)

    QTest.keyClick(ed, Qt.Key.Key_Return)
    pump(0.6)
    print("outline visible", app.outline.isVisible(), app.outline.geometry(), "editor", ed.isVisible())
    shot("09-outline", app.region_rect(), pad=30)

    app.update_settings(notify_on_copy="always")
    app.copy_delayed()
    pump(0.4)
    shot("10-toast-and-flash", app.preview.geometry().united(app.toast.geometry()), pad=30)

    app.toggle_pause()
    pump(0.6)
    shot("11-paused", app.preview.geometry().united(app.region_rect()), pad=30)
    app.toggle_pause()

    app.set_preview_visible(False)
    app.copy_delayed()
    pump(0.4)
    shot("12-toast-preview-hidden", app.toast.geometry(), pad=30)

    app.quit()


if __name__ == "__main__":
    main()
