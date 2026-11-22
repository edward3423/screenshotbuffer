"""Global hotkeys on macOS via Carbon's RegisterEventHotKey.

This is the API Cocoa apps use for system-wide shortcuts. Unlike keyboard
event taps it needs no Accessibility or Input Monitoring permission, and it
delivers events on the main thread through the regular Cocoa run loop that
Qt already drives.
"""

from __future__ import annotations

import ctypes
import logging
import time
from collections.abc import Callable
from ctypes import POINTER, Structure, byref, c_int32, c_uint32, c_ulong, c_void_p

log = logging.getLogger(__name__)

_carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")


def _fourcc(s: str) -> int:
    return int.from_bytes(s.encode("ascii"), "big")


kEventClassKeyboard = _fourcc("keyb")
kEventHotKeyPressed = 5
kEventParamDirectObject = _fourcc("----")
typeEventHotKeyID = _fourcc("hkid")
eventHotKeyExistsErr = -9878
SIGNATURE = _fourcc("ScBf")


class EventHotKeyID(Structure):
    _fields_ = [("signature", c_uint32), ("id", c_uint32)]


class EventTypeSpec(Structure):
    _fields_ = [("eventClass", c_uint32), ("eventKind", c_uint32)]


# OSStatus (*EventHandlerProcPtr)(EventHandlerCallRef, EventRef, void *userData)
EventHandlerProc = ctypes.CFUNCTYPE(c_int32, c_void_p, c_void_p, c_void_p)

_carbon.GetApplicationEventTarget.argtypes = []
_carbon.GetApplicationEventTarget.restype = c_void_p
_carbon.InstallEventHandler.argtypes = [
    c_void_p,
    EventHandlerProc,
    c_ulong,
    POINTER(EventTypeSpec),
    c_void_p,
    POINTER(c_void_p),
]
_carbon.InstallEventHandler.restype = c_int32
_carbon.RemoveEventHandler.argtypes = [c_void_p]
_carbon.RemoveEventHandler.restype = c_int32
_carbon.RegisterEventHotKey.argtypes = [
    c_uint32,
    c_uint32,
    EventHotKeyID,
    c_void_p,
    c_uint32,
    POINTER(c_void_p),
]
_carbon.RegisterEventHotKey.restype = c_int32
_carbon.UnregisterEventHotKey.argtypes = [c_void_p]
_carbon.UnregisterEventHotKey.restype = c_int32
_carbon.GetEventParameter.argtypes = [
    c_void_p,
    c_uint32,
    c_uint32,
    POINTER(c_uint32),
    c_ulong,
    POINTER(c_ulong),
    c_void_p,
]
_carbon.GetEventParameter.restype = c_int32


class MacHotkeyBackend:
    """Must be created and used on the main (GUI) thread."""

    def __init__(self, callback: Callable[[int, float], None]) -> None:
        self._callback = callback
        self._refs: dict[int, c_void_p] = {}
        # Keep the ctypes callback alive for as long as the handler is installed.
        self._proc = EventHandlerProc(self._on_event)
        self._handler = c_void_p()
        spec = EventTypeSpec(kEventClassKeyboard, kEventHotKeyPressed)
        status = _carbon.InstallEventHandler(
            _carbon.GetApplicationEventTarget(), self._proc, 1, byref(spec), None, byref(self._handler)
        )
        if status != 0:
            raise OSError(status, "InstallEventHandler failed")

    def register(self, hotkey_id: int, native: tuple[int, int]) -> str | None:
        mods, keycode = native
        ref = c_void_p()
        status = _carbon.RegisterEventHotKey(
            keycode,
            mods,
            EventHotKeyID(SIGNATURE, hotkey_id),
            _carbon.GetApplicationEventTarget(),
            0,
            byref(ref),
        )
        if status == 0:
            self._refs[hotkey_id] = ref
            return None
        if status == eventHotKeyExistsErr:
            return "already used by another application"
        return f"macOS error {status}"

    def unregister(self, hotkey_id: int) -> None:
        ref = self._refs.pop(hotkey_id, None)
        if ref is not None:
            _carbon.UnregisterEventHotKey(ref)

    def close(self) -> None:
        for hid in list(self._refs):
            self.unregister(hid)
        if self._handler:
            _carbon.RemoveEventHandler(self._handler)
            self._handler = c_void_p()

    def _on_event(self, _call_ref: int, event: int, _user_data: int) -> int:
        try:
            hk = EventHotKeyID()
            status = _carbon.GetEventParameter(
                event,
                kEventParamDirectObject,
                typeEventHotKeyID,
                None,
                ctypes.sizeof(hk),
                None,
                byref(hk),
            )
            if status == 0 and hk.signature == SIGNATURE:
                self._callback(int(hk.id), time.monotonic())
        except Exception:
            log.exception("Hotkey callback failed")
        return 0
