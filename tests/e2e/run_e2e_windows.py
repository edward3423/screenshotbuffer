r"""End-to-end test on a real Windows desktop.

Run with:  uv run python tests/e2e/run_e2e_windows.py [--exe PATH\TO\ScreenshotBuffer.exe]

Without --exe it tests the source tree; with it, a built app.

It launches the real app (separate process, temporary settings), shows a
window whose color changes on a known schedule, presses the real global
hotkeys with synthesized keyboard input and verifies:

  1. Ctrl+PrtScr puts the frame from exactly `delay` seconds ago on the clipboard.
  2. The clipboard image has the region's size.
  3. The preview window overlapping the region is not in the image.
  4. Shift+PrtScr hides and shows the preview window.

The user's clipboard is saved before and restored afterwards. The desktop
must be unlocked and nothing should cover the test window during the run.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QMimeData, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import QApplication, QWidget

ROOT = Path(__file__).resolve().parents[2]
DELAY = 1.0
FPS = 20.0
PERIOD = 0.4  # seconds per color
COLORS = [
    QColor(230, 40, 40),
    QColor(40, 200, 60),
    QColor(40, 90, 230),
    QColor(240, 200, 30),
    QColor(170, 50, 200),
    QColor(30, 200, 200),
]
REGION = QRect(200, 200, 480, 270)
PREVIEW_TITLE = "Screenshot Buffer preview"

user32 = ctypes.WinDLL("user32", use_last_error=True)
VK_CONTROL, VK_SHIFT, VK_SNAPSHOT = 0x11, 0x10, 0x2C
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x1, 0x2


def press(*vks: int) -> None:
    for vk in vks:
        flags = KEYEVENTF_EXTENDEDKEY if vk == VK_SNAPSHOT else 0
        user32.keybd_event(vk, 0, flags, 0)
        time.sleep(0.02)
    for vk in reversed(vks):
        flags = (KEYEVENTF_EXTENDEDKEY if vk == VK_SNAPSHOT else 0) | KEYEVENTF_KEYUP
        user32.keybd_event(vk, 0, flags, 0)
        time.sleep(0.02)


def find_window(title_prefix: str) -> tuple[bool, tuple[int, int, int, int]]:
    """(visible, rect) of the first top-level window of another process with this title."""
    found: list = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _l):
        wpid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        # The venv python.exe is a launcher, so the app runs in a child process.
        if wpid.value != os.getpid():
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value.startswith(title_prefix):
                r = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                found.append((bool(user32.IsWindowVisible(hwnd)), (r.left, r.top, r.right, r.bottom)))
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else (False, (0, 0, 0, 0))


class ColorWindow(QWidget):
    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Screenshot Buffer E2E target")
        self.setGeometry(REGION)
        self.start = time.monotonic()
        self.index = 0
        self.timer = QTimer(self, interval=5)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def color_at(self, t: float) -> QColor:
        return COLORS[int((t - self.start) / PERIOD) % len(COLORS)]

    def tick(self) -> None:
        i = int((time.monotonic() - self.start) / PERIOD) % len(COLORS)
        if i != self.index:
            self.index = i
            self.repaint()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), COLORS[self.index])
        p.end()


def wait(app: QApplication, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def wait_until(app: QApplication, pred, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.02)
    return False


def snapshot_clipboard() -> QMimeData:
    src = QGuiApplication.clipboard().mimeData()
    copy = QMimeData()
    if src is not None:
        for fmt in src.formats():
            copy.setData(fmt, src.data(fmt))
        if src.hasImage():
            copy.setImageData(src.imageData())
    return copy


def main() -> int:
    app = QApplication(sys.argv)
    saved_clipboard = snapshot_clipboard()
    win = ColorWindow()
    win.show()
    wait(app, 0.5)
    dpr = win.screen().devicePixelRatio()

    cfg = Path(tempfile.mkdtemp(prefix="sbuf-e2e-"))
    # Preview deliberately overlaps the right part of the recorded region.
    preview = [REGION.x() + 240, REGION.y() + 60, 320, 180]
    settings = {
        "fps": FPS,
        "delay": DELAY,
        "source": "region",
        "region": [REGION.x(), REGION.y(), REGION.width(), REGION.height()],
        "show_region_outline": True,
        "preview_visible": True,
        "preview_geometry": preview,
        "notify_on_copy": "never",
    }
    (cfg / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    env = dict(os.environ, SCREENSHOTBUFFER_CONFIG_DIR=str(cfg))
    env.pop("SCREENSHOTBUFFER_SHOW_IN_CAPTURE", None)
    if "--exe" in sys.argv:
        command = [sys.argv[sys.argv.index("--exe") + 1]]
    else:
        command = [sys.executable, "-m", "screenshotbuffer"]
    proc = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    failures: list[str] = []
    try:
        if not wait_until(app, lambda: proc.poll() is not None or find_window(PREVIEW_TITLE)[0], 15):
            raise RuntimeError("preview window did not appear")
        if proc.poll() is not None:
            raise RuntimeError(
                "the app exited at once; another Screenshot Buffer instance is probably running "
                "(quit it from the tray and run the test again)"
            )
        if not find_window(PREVIEW_TITLE)[0]:
            raise RuntimeError("preview window did not appear")
        wait(app, DELAY + 1.5)  # let the buffer fill

        for attempt in range(3):
            # Press when `now - DELAY` is in the middle of a color period.
            phase = ((time.monotonic() - DELAY) - win.start) % PERIOD
            wait(app, (PERIOD / 2 - phase) % PERIOD)
            pressed = time.monotonic()
            expected = win.color_at(pressed - DELAY)
            current = win.color_at(pressed)
            QGuiApplication.clipboard().clear()
            press(VK_CONTROL, VK_SNAPSHOT)
            ok = wait_until(app, lambda: QGuiApplication.clipboard().mimeData().hasImage(), 3)
            if not ok:
                failures.append(f"attempt {attempt}: clipboard got no image")
                continue
            img = QGuiApplication.clipboard().image()
            want_size = (round(REGION.width() * dpr), round(REGION.height() * dpr))
            if (img.width(), img.height()) != want_size:
                failures.append(f"attempt {attempt}: image size {img.width()}x{img.height()} != {want_size}")
            left = img.pixelColor(img.width() // 6, img.height() // 2)
            under_preview = img.pixelColor(img.width() * 3 // 4, img.height() // 2)
            # The region outline is drawn just outside the region and must not bleed in.
            corners = [
                ("top-left corner", img.pixelColor(0, 0)),
                ("bottom-right corner", img.pixelColor(img.width() - 1, img.height() - 1)),
            ]
            for name, got in [("left", left), ("under preview", under_preview), *corners]:
                if got.rgb() != expected.rgb():
                    failures.append(
                        f"attempt {attempt}: {name} pixel {got.name()} != expected {expected.name()} "
                        f"(current color {current.name()})"
                    )
            print(f"copy {attempt}: expected {expected.name()}, got {left.name()} / {under_preview.name()}")
            wait(app, 0.3)

        # Shift+PrtScr toggles the preview.
        press(VK_SHIFT, VK_SNAPSHOT)
        if not wait_until(app, lambda: not find_window(PREVIEW_TITLE)[0], 3):
            failures.append("Shift+PrtScr did not hide the preview")
        press(VK_SHIFT, VK_SNAPSHOT)
        if not wait_until(app, lambda: find_window(PREVIEW_TITLE)[0], 3):
            failures.append("Shift+PrtScr did not show the preview again")
        wait(app, 0.8)
        saved = json.loads((cfg / "settings.json").read_text(encoding="utf-8"))
        if saved.get("preview_visible") is not True:
            failures.append("preview visibility was not persisted")
    except RuntimeError as e:
        failures.append(str(e))
    finally:
        # Kill the whole tree: the venv python.exe is a launcher with a child.
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        try:
            out, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()
        QGuiApplication.clipboard().setMimeData(saved_clipboard)
        wait(app, 0.2)
        log = out.decode(errors="replace").strip()
        if log:
            print("--- app output ---")
            print(log)

    if failures:
        print("FAILED:")
        for f in failures:
            print("  -", f)
        return 1
    print("E2E PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
