"""Platform-specific window and process tweaks."""

from __future__ import annotations

import ctypes
import logging
import os
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QWidget

log = logging.getLogger(__name__)

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# Window levels (macOS)
LEVEL_STATUS = 25
LEVEL_POPUP = 101

if IS_WIN:
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    _user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
    WDA_EXCLUDEFROMCAPTURE = 0x11


def _ns_window(widget: QWidget):  # type: ignore[no-untyped-def]
    import objc

    view = objc.objc_object(c_void_p=int(widget.winId()))
    return view.window()


def prepare_floating_window(widget: QWidget) -> None:
    """Window flags/attributes shared by all of our always-on-top helper windows."""
    if IS_MAC:
        # Qt tool windows hide when the app deactivates, and ours almost never
        # is the active app.
        widget.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)


def exclude_from_capture(widget: QWidget) -> bool:
    """Hide the window from screenshots and screen recordings (but not from the user).

    Windows: requires Windows 10 2004+ and does not work for translucent
    (layered) windows. macOS: NSWindowSharingNone, honored by the
    CoreGraphics capture API we use.

    Setting SCREENSHOTBUFFER_SHOW_IN_CAPTURE=1 disables this, which is useful
    for taking screenshots of the app itself.
    """
    if os.environ.get("SCREENSHOTBUFFER_SHOW_IN_CAPTURE") == "1":
        return False
    try:
        if IS_WIN:
            ok = bool(_user32.SetWindowDisplayAffinity(int(widget.winId()), WDA_EXCLUDEFROMCAPTURE))
            if not ok:
                log.warning(
                    "SetWindowDisplayAffinity failed for %s (error %s)",
                    type(widget).__name__,
                    ctypes.get_last_error(),
                )
            return ok
        if IS_MAC:
            _ns_window(widget).setSharingType_(0)  # NSWindowSharingNone
            return True
    except Exception:
        log.exception("Could not exclude window from capture")
    return False


def float_above_everything(widget: QWidget, level: int = LEVEL_STATUS) -> None:
    """macOS: keep the window visible on every Space and over full-screen apps.

    Qt re-applies its own window level when showing, so call this after show.
    """
    if not IS_MAC:
        return

    def apply() -> None:
        try:
            win = _ns_window(widget)
            # CanJoinAllSpaces | Stationary | IgnoresCycle | FullScreenAuxiliary
            win.setCollectionBehavior_((1 << 0) | (1 << 4) | (1 << 6) | (1 << 8))
            win.setLevel_(level)
            win.setHidesOnDeactivate_(False)
        except Exception:
            log.exception("Could not adjust window level")

    apply()
    QTimer.singleShot(0, apply)


def raise_without_activating(widget: QWidget) -> None:
    """Stack the window above others without taking focus from the active app.

    On macOS QWidget.raise_() also activates the whole application
    (Qt's QT_MAC_SET_RAISE_PROCESS default), which defocuses whatever the
    user is working in.
    """
    if IS_MAC:
        try:
            _ns_window(widget).orderFrontRegardless()
            return
        except Exception:
            log.exception("Could not order window front")
    widget.raise_()


def make_background_app() -> None:
    """macOS: run as a menu-bar app without a Dock icon."""
    if not IS_MAC:
        return
    try:
        from AppKit import NSApplication

        NSApplication.sharedApplication().setActivationPolicy_(1)  # Accessory
    except Exception:
        log.exception("Could not set activation policy")


def bring_to_front(widget: QWidget) -> None:
    if IS_MAC:
        try:
            from AppKit import NSApplication

            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        except Exception:
            log.exception("Could not activate app")
    widget.show()
    widget.setWindowState(
        (widget.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive
    )
    widget.raise_()
    widget.activateWindow()


def has_screen_capture_permission() -> bool:
    if not IS_MAC:
        return True
    try:
        import Quartz

        return bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception:
        log.exception("Could not query screen recording permission")
        return True


def request_screen_capture_permission() -> None:
    if not IS_MAC:
        return
    try:
        import Quartz

        Quartz.CGRequestScreenCaptureAccess()
    except Exception:
        log.exception("Could not request screen recording permission")
