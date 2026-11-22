"""Global hotkeys on Windows via RegisterHotKey.

Hotkeys are owned by a dedicated thread running its own message loop, so they
keep working while the GUI thread is inside a native modal loop (menus,
window moves). RegisterHotKey also consumes the key press, so Windows' own
Print Screen handling does not run for a combination we own.
"""

from __future__ import annotations

import ctypes
import logging
import queue
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from typing import Any

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WM_APP = 0x8000
PM_NOREMOVE = 0x0000
MOD_NOREPEAT = 0x4000
ERROR_HOTKEY_ALREADY_REGISTERED = 1409

user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL
user32.PeekMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
    wintypes.UINT,
]
user32.PeekMessageW.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


class WindowsHotkeyBackend:
    def __init__(self, callback: Callable[[int, float], None]) -> None:
        self._callback = callback
        self._requests: queue.Queue[tuple[Callable[[], Any], dict[str, Any], threading.Event]] = queue.Queue()
        self._ready = threading.Event()
        self._tid = 0
        self._registered: set[int] = set()
        self._thread = threading.Thread(target=self._run, name="hotkeys", daemon=True)
        self._thread.start()
        if not self._ready.wait(5):
            raise RuntimeError("hotkey thread did not start")

    def register(self, hotkey_id: int, native: tuple[int, int]) -> str | None:
        mods, vk = native

        def do() -> str | None:
            if user32.RegisterHotKey(None, hotkey_id, mods | MOD_NOREPEAT, vk):
                self._registered.add(hotkey_id)
                return None
            err = ctypes.get_last_error()
            if err == ERROR_HOTKEY_ALREADY_REGISTERED:
                return "already used by another application"
            return f"Windows error {err}"

        return self._call(do)

    def unregister(self, hotkey_id: int) -> None:
        def do() -> None:
            if hotkey_id in self._registered:
                user32.UnregisterHotKey(None, hotkey_id)
                self._registered.discard(hotkey_id)

        self._call(do)

    def close(self) -> None:
        if not self._thread.is_alive():
            return
        for hid in list(self._registered):
            self.unregister(hid)
        user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        self._thread.join(timeout=2)

    def _call(self, fn: Callable[[], Any]) -> Any:
        box: dict[str, Any] = {}
        done = threading.Event()
        self._requests.put((fn, box, done))
        if not user32.PostThreadMessageW(self._tid, WM_APP, 0, 0):
            raise OSError(ctypes.get_last_error(), "PostThreadMessageW failed")
        if not done.wait(5):
            raise TimeoutError("hotkey thread did not respond")
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def _run(self) -> None:
        msg = wintypes.MSG()
        # Force creation of this thread's message queue before announcing it.
        user32.PeekMessageW(ctypes.byref(msg), None, WM_APP, WM_APP, PM_NOREMOVE)
        self._tid = kernel32.GetCurrentThreadId()
        self._ready.set()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                try:
                    self._callback(int(msg.wParam), time.monotonic())
                except Exception:
                    log.exception("Hotkey callback failed")
            elif msg.message == WM_APP:
                self._drain()
        self._drain()

    def _drain(self) -> None:
        while True:
            try:
                fn, box, done = self._requests.get_nowait()
            except queue.Empty:
                return
            try:
                box["result"] = fn()
            except Exception as e:
                box["error"] = e
            finally:
                done.set()
