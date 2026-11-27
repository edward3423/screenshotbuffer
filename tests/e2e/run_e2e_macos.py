"""End-to-end test on a real macOS desktop.

Run with:

    uv run python tests/e2e/run_e2e_macos.py [--app "/Applications/Screenshot Buffer.app"]
    uv run python tests/e2e/run_e2e_macos.py --no-keys

By default it launches the real app (separate process, temporary settings;
with --app a built bundle, otherwise the source tree) and presses the real
global hotkeys with synthesized keyboard input. That needs Accessibility
permission for the terminal running the test.

With --no-keys the app runs inside this process instead and each hotkey is
delivered by calling the handler the Carbon hotkey callback calls. Capture,
buffer, preview, clipboard and capture exclusion are all still real; only the
keypress itself is skipped.

Either way it shows a window whose color changes on a known schedule and
verifies:

  1. The copy hotkey (Cmd+Shift+9) puts the frame from exactly `delay` seconds
     ago on the clipboard.
  2. The clipboard image has the region's size (in physical pixels).
  3. The preview window overlapping the region is not in the image.
  4. Cmd+Shift+0 hides and shows the preview window, and that is persisted.
  5. Copying (which shows the notification) does not take focus from the
     active app (out of process only).

The terminal needs Screen Recording permission. The user's clipboard is saved
before and restored afterwards. Nothing should cover the test window.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import Quartz
from AppKit import NSApplication, NSWorkspace
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

KC_CMD, KC_SHIFT, KC_9, KC_0 = 0x37, 0x38, 0x19, 0x1D
FLAGS = {KC_CMD: Quartz.kCGEventFlagMaskCommand, KC_SHIFT: Quartz.kCGEventFlagMaskShift}
KEYS = {"copy": (KC_CMD, KC_SHIFT, KC_9), "toggle_preview": (KC_CMD, KC_SHIFT, KC_0)}


def press(*keys: int) -> None:
    """Press keys in order (modifiers first) and release them in reverse."""
    flags = 0
    for k in keys:
        flags |= FLAGS.get(k, 0)
        ev = Quartz.CGEventCreateKeyboardEvent(None, k, True)
        Quartz.CGEventSetFlags(ev, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        time.sleep(0.02)
    for k in reversed(keys):
        ev = Quartz.CGEventCreateKeyboardEvent(None, k, False)
        Quartz.CGEventSetFlags(ev, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        flags &= ~FLAGS.get(k, 0)
        time.sleep(0.02)


def find_window(title: str, pid: int) -> bool:
    """Whether a window with this title belonging to `pid` is on screen."""
    infos = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)
    return any(
        i.get("kCGWindowOwnerPID") == pid and (i.get("kCGWindowName") or "") == title for i in infos or []
    )


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


def frontmost_pid() -> int:
    return int(NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier())


def snapshot_clipboard() -> QMimeData:
    src = QGuiApplication.clipboard().mimeData()
    copy = QMimeData()
    if src is not None:
        for fmt in src.formats():
            copy.setData(fmt, src.data(fmt))
        if src.hasImage():
            copy.setImageData(src.imageData())
    return copy


def close_enough(a: QColor, b: QColor, tol: int = 6) -> bool:
    # Color management can shift a channel by a step or two.
    return all(abs(x - y) <= tol for x, y in zip(a.getRgb()[:3], b.getRgb()[:3], strict=True))


class OutOfProcess:
    """The app as its own process, driven by real keypresses."""

    def __init__(self, cfg: Path) -> None:
        env = dict(os.environ, SCREENSHOTBUFFER_CONFIG_DIR=str(cfg))
        env.pop("SCREENSHOTBUFFER_SHOW_IN_CAPTURE", None)
        if "--app" in sys.argv:
            bundle = Path(sys.argv[sys.argv.index("--app") + 1])
            command = [str(next((bundle / "Contents" / "MacOS").iterdir()))]
        else:
            command = [sys.executable, "-m", "screenshotbuffer"]
        self.proc = subprocess.Popen(
            command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
        self.pid = self.proc.pid

    def exited(self) -> bool:
        return self.proc.poll() is not None

    def trigger(self, action: str) -> None:
        press(*KEYS[action])

    def close(self) -> str:
        self.proc.terminate()
        try:
            out, _ = self.proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            out, _ = self.proc.communicate()
        return out.decode(errors="replace").strip()


class InProcess:
    """The app inside this process; hotkeys go straight to its handler."""

    def __init__(self, cfg: Path, qapp: QApplication) -> None:
        os.environ.pop("SCREENSHOTBUFFER_SHOW_IN_CAPTURE", None)
        sys.path.insert(0, str(ROOT / "src"))
        from screenshotbuffer.app import App
        from screenshotbuffer.config import SettingsStore

        self.app = App(qapp, SettingsStore(cfg))
        self.app.start()
        self.pid = os.getpid()

    def exited(self) -> bool:
        return False

    def trigger(self, action: str) -> None:
        self.app._on_hotkey(action, time.monotonic())

    def close(self) -> str:
        self.app._preview_timer.stop()
        self.app.selector.stop()
        self.app.hotkeys.close()
        self.app.worker.stop()
        self.app._save()
        self.app.tray.hide()
        for w in (self.app.preview, self.app.outline, self.app.editor, self.app.toast):
            w.hide()
        return ""


def main() -> int:
    no_keys = "--no-keys" in sys.argv
    hiservices = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    if not no_keys and not hiservices.AXIsProcessTrusted():
        print(
            "This terminal needs Accessibility permission to press the hotkeys "
            "(System Settings > Privacy & Security > Accessibility), or run with --no-keys."
        )
        return 2
    qapp = QApplication(sys.argv)
    saved_clipboard = snapshot_clipboard()
    win = ColorWindow()
    win.show()
    wait(qapp, 0.5)
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
        # Show the notification on every copy: it must not take focus.
        "notify_on_copy": "always",
    }
    (cfg / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    target = InProcess(cfg, qapp) if no_keys else OutOfProcess(cfg)
    failures: list[str] = []
    try:
        if not wait_until(qapp, lambda: target.exited() or find_window(PREVIEW_TITLE, target.pid), 15):
            raise RuntimeError("preview window did not appear")
        if target.exited():
            raise RuntimeError(
                "the app exited at once; another Screenshot Buffer instance is probably running "
                "(quit it from the menu bar and run the test again)"
            )
        wait(qapp, DELAY + 1.5)  # let the buffer fill

        for attempt in range(3):
            if not no_keys:
                # Be the active app, like whatever the user is working in.
                NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
                win.activateWindow()
                wait_until(qapp, lambda: frontmost_pid() == os.getpid(), 1)
            # Press when `now - DELAY` is in the middle of a color period.
            phase = ((time.monotonic() - DELAY) - win.start) % PERIOD
            wait(qapp, (PERIOD / 2 - phase) % PERIOD)
            pressed = time.monotonic()
            expected = win.color_at(pressed - DELAY)
            current = win.color_at(pressed)
            QGuiApplication.clipboard().clear()
            target.trigger("copy")
            ok = wait_until(qapp, lambda: QGuiApplication.clipboard().mimeData().hasImage(), 3)
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
                if not close_enough(got, expected):
                    failures.append(
                        f"attempt {attempt}: {name} pixel {got.name()} != expected {expected.name()} "
                        f"(current color {current.name()})"
                    )
            print(f"copy {attempt}: expected {expected.name()}, got {left.name()} / {under_preview.name()}")
            if not no_keys:
                wait(qapp, 0.3)
                if frontmost_pid() == target.pid:
                    failures.append(f"attempt {attempt}: copying made Screenshot Buffer the active app")
            if attempt == 0 and "--save" in sys.argv:
                img.save(sys.argv[sys.argv.index("--save") + 1])
            wait(qapp, 0.3)

        target.trigger("toggle_preview")
        if not wait_until(qapp, lambda: not find_window(PREVIEW_TITLE, target.pid), 3):
            failures.append("Cmd+Shift+0 did not hide the preview")
        target.trigger("toggle_preview")
        if not wait_until(qapp, lambda: find_window(PREVIEW_TITLE, target.pid), 3):
            failures.append("Cmd+Shift+0 did not show the preview again")
        wait(qapp, 0.8)
        saved = json.loads((cfg / "settings.json").read_text(encoding="utf-8"))
        if saved.get("preview_visible") is not True:
            failures.append("preview visibility was not persisted")
    except RuntimeError as e:
        failures.append(str(e))
    finally:
        log = target.close()
        QGuiApplication.clipboard().setMimeData(saved_clipboard)
        wait(qapp, 0.2)
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
