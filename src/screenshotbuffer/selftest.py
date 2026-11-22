"""Smoke test of a (usually frozen) build: ``ScreenshotBuffer --self-test REPORT``.

Exercises every platform integration the app needs at runtime: screen
capture, the frame buffer, global hotkeys, native window tweaks and the
whole UI stack. Packaging problems (a missing module or plugin) show up here
instead of on a user's machine. The report is written to a file because
windowed builds have no console.
"""

from __future__ import annotations

import sys
import tempfile
import time
import traceback
from collections.abc import Callable
from pathlib import Path


def run(report_path: str | None) -> int:
    lines: list[str] = []
    ok = True

    def check(name: str, fn: Callable[[], object]) -> None:
        nonlocal ok
        try:
            detail = fn()
            lines.append(f"PASS {name}" + (f": {detail}" if detail not in (None, "") else ""))
        except Exception:
            ok = False
            lines.append(f"FAIL {name}\n{traceback.format_exc()}")

    from PySide6.QtWidgets import QApplication

    qapp = QApplication.instance() or QApplication([sys.argv[0]])

    from . import __version__

    lines.append(f"Screenshot Buffer {__version__} on {sys.platform}, frozen={getattr(sys, 'frozen', False)}")

    def capture() -> str:
        import mss

        from .capture import _configure_mss

        _configure_mss()
        with mss.MSS() as sct:
            mon = sct.monitors[1]
            shot = sct.grab({"left": mon["left"], "top": mon["top"], "width": 64, "height": 48})
        if len(shot.raw) < 64 * 48 * 4:
            raise AssertionError(f"short capture: {len(shot.raw)} bytes")
        return f"{shot.width}x{shot.height}"

    def frame_buffer() -> str:
        from .app import to_qimage
        from .buffer import FrameBuffer, Pixels
        from .capture import make_thumbnail

        buf = FrameBuffer(retention=5, memory_limit=10**8)
        w, h = 1600, 900  # large enough to exercise the thumbnail path
        buf.push(
            time.monotonic(), Pixels(w, h, w * 4, bytes(range(256)) * (w * h * 4 // 256)), make_thumbnail
        )
        frame = buf.latest()
        assert frame is not None
        full, thumb = to_qimage(frame.pixels()), to_qimage(frame.thumbnail())
        assert (full.width(), full.height()) == (w, h)
        return f"thumbnail {thumb.width()}x{thumb.height()}"

    def hotkeys() -> str:
        from .hotkeys import HotkeyManager

        manager = HotkeyManager()
        try:
            if manager.unavailable_reason:
                raise AssertionError(manager.unavailable_reason)
            errors = manager.apply({"copy": "Ctrl+Alt+Shift+F19"})
            if errors.get("copy"):
                raise AssertionError(errors["copy"])
        finally:
            manager.close()
        return "registered and released Ctrl+Alt+Shift+F19"

    def native_apis() -> str:
        from . import native

        permission = native.has_screen_capture_permission()
        if sys.platform == "darwin":
            import AppKit  # noqa: F401
            import objc  # noqa: F401
            import Quartz  # noqa: F401
        return f"screen capture permission: {permission}"

    def ui() -> str:
        from .app import App
        from .config import SettingsStore

        with tempfile.TemporaryDirectory() as tmp:
            app = App(qapp, SettingsStore(Path(tmp)))
            app.preview.show()
            app.show_settings()
            app.show_shortcuts()
            app.editor.begin(app.region_rect())
            app.editor.finish(True)
            for _ in range(20):
                qapp.processEvents()
                time.sleep(0.01)
            excluded = app.preview.excluded_from_capture
            app.selector.stop()
            app.hotkeys.close()
            app.tray.hide()
            for w in (app.preview, app.outline, app.editor, app.toast):
                w.hide()
            if app._settings_window is not None:
                app._settings_window.close()
            if app._shortcuts_dialog is not None:
                app._shortcuts_dialog.close()
        if sys.platform == "darwin" and not excluded:
            raise AssertionError("preview could not be excluded from screen capture")
        return f"preview excluded from capture: {excluded}"

    check("screen capture", capture)
    check("frame buffer", frame_buffer)
    check("global hotkeys", hotkeys)
    check("native APIs", native_apis)
    check("user interface", ui)
    lines.append("OK" if ok else "FAILED")

    text = "\n".join(lines) + "\n"
    if report_path:
        Path(report_path).write_text(text, encoding="utf-8")
    if sys.stdout is not None:
        print(text, end="")
    return 0 if ok else 1
