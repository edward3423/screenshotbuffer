"""System-wide hotkeys with a small Qt-friendly facade."""

from __future__ import annotations

import logging
import sys
from typing import Protocol

from PySide6.QtCore import QObject, Signal

from . import keys

log = logging.getLogger(__name__)


class _Backend(Protocol):
    def register(self, hotkey_id: int, native: tuple[int, int]) -> str | None: ...
    def unregister(self, hotkey_id: int) -> None: ...
    def close(self) -> None: ...


class HotkeyManager(QObject):
    """Registers one global hotkey per action and emits ``activated``.

    ``activated(action, monotonic_time)`` is always delivered on the GUI
    thread. The timestamp is taken as soon as the OS reports the key press.
    """

    activated = Signal(str, float)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bindings: dict[str, str] = {}
        self._ids: dict[int, str] = {}
        self._errors: dict[str, str | None] = {}
        self._suspended = False
        self._backend: _Backend | None = None
        self.unavailable_reason: str | None = None
        try:
            if sys.platform == "win32":
                from ._windows import WindowsHotkeyBackend

                self._backend = WindowsHotkeyBackend(self._on_native)
            elif sys.platform == "darwin":
                from ._macos import MacHotkeyBackend

                self._backend = MacHotkeyBackend(self._on_native)
            else:
                self.unavailable_reason = "Global hotkeys are only supported on Windows and macOS."
        except Exception as e:
            log.exception("Could not initialise global hotkeys")
            self.unavailable_reason = f"Global hotkeys unavailable: {e}"

    def apply(self, bindings: dict[str, str]) -> dict[str, str | None]:
        """Replace all bindings. Returns an error message (or None) per action."""
        self._bindings = dict(bindings)
        if not self._suspended:
            self._register_all()
        return self.errors()

    def errors(self) -> dict[str, str | None]:
        return dict(self._errors)

    def bindings(self) -> dict[str, str]:
        return dict(self._bindings)

    def suspend(self) -> None:
        """Temporarily release all hotkeys, e.g. while the user records a new one."""
        if not self._suspended:
            self._suspended = True
            self._unregister_all()

    def resume(self) -> None:
        if self._suspended:
            self._suspended = False
            self._register_all()

    def close(self) -> None:
        self._unregister_all()
        if self._backend is not None:
            self._backend.close()
            self._backend = None

    def _register_all(self) -> None:
        self._unregister_all()
        self._errors = {}
        seen: dict[keys.Hotkey, str] = {}
        for i, (action, text) in enumerate(self._bindings.items(), start=1):
            if not text:
                self._errors[action] = None
                continue
            hk = keys.parse(text)
            native = None
            if hk is not None:
                native = hk.mac_native() if sys.platform == "darwin" else hk.win_native()
            if hk is None or native is None:
                self._errors[action] = "this key cannot be used as a global hotkey"
                continue
            if hk in seen:
                self._errors[action] = "same shortcut as another action"
                continue
            if self._backend is None:
                self._errors[action] = self.unavailable_reason
                continue
            try:
                err = self._backend.register(i, native)
            except Exception as e:
                log.exception("Registering hotkey %s failed", text)
                err = str(e)
            self._errors[action] = err
            if err is None:
                self._ids[i] = action
                seen[hk] = action
            else:
                log.warning("Hotkey %s for %s not registered: %s", text, action, err)

    def _unregister_all(self) -> None:
        if self._backend is not None:
            for hid in list(self._ids):
                try:
                    self._backend.unregister(hid)
                except Exception:
                    log.exception("Unregistering hotkey failed")
        self._ids.clear()

    def _on_native(self, hotkey_id: int, timestamp: float) -> None:
        # May run on the backend's thread; Qt queues the signal to the GUI thread.
        action = self._ids.get(hotkey_id)
        if action is not None:
            self.activated.emit(action, timestamp)
