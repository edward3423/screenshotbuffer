"""A line edit that records a single global-hotkey chord."""

from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import (
    QAbstractNativeEventFilter,
    QByteArray,
    QCoreApplication,
    QEvent,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QFocusEvent, QKeyEvent, QKeySequence, QMouseEvent
from PySide6.QtWidgets import QLineEdit

from ..hotkeys import keys

_MODS = (
    Qt.KeyboardModifier.ControlModifier
    | Qt.KeyboardModifier.ShiftModifier
    | Qt.KeyboardModifier.AltModifier
    | Qt.KeyboardModifier.MetaModifier
)
_MODIFIER_KEY_VALUES = {k.value for k in keys.MODIFIER_KEYS}

WM_KEYUP, WM_SYSKEYUP = 0x0101, 0x0105
VK_SNAPSHOT = 0x2C


def _windows_modifiers() -> Qt.KeyboardModifier:
    """Modifier keys currently held, read directly from Windows."""
    state = ctypes.windll.user32.GetKeyState
    mods = Qt.KeyboardModifier.NoModifier
    held = lambda vk: bool(state(vk) & 0x8000)  # noqa: E731
    if held(0x11):
        mods |= Qt.KeyboardModifier.ControlModifier
    if held(0x10):
        mods |= Qt.KeyboardModifier.ShiftModifier
    if held(0x12):
        mods |= Qt.KeyboardModifier.AltModifier
    if held(0x5B) or held(0x5C):
        mods |= Qt.KeyboardModifier.MetaModifier
    return mods


class _PrintScreenFilter(QAbstractNativeEventFilter):
    """Windows sends only a key *release* for Print Screen, and Qt drops a
    release it never saw a press for. Catch the raw message instead."""

    def __init__(self, edit: HotkeyEdit) -> None:
        super().__init__()
        self._edit = edit

    def nativeEventFilter(self, event_type: QByteArray | bytes, message: int) -> tuple[bool, int]:
        if bytes(event_type) != b"windows_generic_MSG":
            return False, 0
        from ctypes import wintypes

        msg = wintypes.MSG.from_address(int(message))
        if msg.message in (WM_KEYUP, WM_SYSKEYUP) and msg.wParam == VK_SNAPSHOT:
            mods = _windows_modifiers()
            # Don't change widgets from inside the native event dispatch.
            QTimer.singleShot(0, lambda: self._edit.print_screen_released(mods))
            return True, 0
        return False, 0


class HotkeyEdit(QLineEdit):
    """Click, then press the desired key combination.

    Esc cancels, Backspace/Delete clears. Print Screen is supported even
    though Windows only reports its key release.
    """

    recording_changed = Signal(bool)
    hotkey_changed = Signal(str)

    def __init__(self, parent=None) -> None:
        self._value = ""
        self._recording = False
        self._native_filter: _PrintScreenFilter | None = None
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setPlaceholderText("Not set")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click, then press the new shortcut. Esc cancels, Backspace clears.")

    def value(self) -> str:
        return self._value

    def set_value(self, value: str) -> None:
        self._value = value
        if not self._recording:
            self._show_value()

    def _show_value(self) -> None:
        self.setText(keys.display(self._value))

    def _set_recording(self, on: bool) -> None:
        if on == self._recording:
            return
        self._recording = on
        self._watch_print_screen(on)
        if on:
            self.setText("")
            self.setPlaceholderText("Press shortcut...")
        else:
            self.setPlaceholderText("Not set")
            self._show_value()
        self.recording_changed.emit(on)

    def _watch_print_screen(self, on: bool) -> None:
        if sys.platform != "win32":
            return
        app = QCoreApplication.instance()
        if on and self._native_filter is None and app is not None:
            self._native_filter = _PrintScreenFilter(self)
            app.installNativeEventFilter(self._native_filter)
        elif not on and self._native_filter is not None:
            if app is not None:
                app.removeNativeEventFilter(self._native_filter)
            self._native_filter = None

    def print_screen_released(self, mods: Qt.KeyboardModifier) -> None:
        if self._recording and self.hasFocus():
            self._commit(Qt.Key.Key_Print.value, mods & _MODS)

    def _commit(self, key: int, mods: Qt.KeyboardModifier) -> None:
        hk = keys.Hotkey(key, mods.value & _MODS.value)
        self._set_recording(False)
        self._value = hk.to_string()
        self._show_value()
        self.hotkey_changed.emit(self._value)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        super().mousePressEvent(e)
        if e.button() == Qt.MouseButton.LeftButton:
            self._set_recording(True)

    def focusOutEvent(self, e: QFocusEvent) -> None:
        self._set_recording(False)
        super().focusOutEvent(e)

    def event(self, e: QEvent) -> bool:
        # While recording, swallow everything (Tab, application shortcuts...).
        if self._recording and e.type() == QEvent.Type.ShortcutOverride:
            e.accept()
            return True
        if self._recording and e.type() == QEvent.Type.KeyPress:
            self.keyPressEvent(e)  # type: ignore[arg-type]
            return True
        return super().event(e)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if not self._recording:
            if e.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._set_recording(True)
            elif e.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
                self._clear()
            else:
                super().keyPressEvent(e)
            return
        key = e.key()
        mods = e.modifiers() & _MODS
        if key == Qt.Key.Key_Escape and not mods:
            self._set_recording(False)
            return
        if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete) and not mods:
            self._set_recording(False)
            self._clear()
            return
        if key in _MODIFIER_KEY_VALUES:
            self._show_partial(mods)
            return
        self._commit(keys.recorded_key(key, e.nativeVirtualKey()), mods)

    def keyReleaseEvent(self, e: QKeyEvent) -> None:
        if not self._recording:
            super().keyReleaseEvent(e)
            return
        if e.key() == Qt.Key.Key_Print:
            # Print Screen may arrive as a lone release (see _PrintScreenFilter).
            self._commit(Qt.Key.Key_Print.value, e.modifiers() & _MODS)
            return
        self._show_partial(e.modifiers() & _MODS)

    def _show_partial(self, mods: Qt.KeyboardModifier) -> None:
        if not mods:
            self.setText("")
            return
        text = QKeySequence(mods.value).toString(QKeySequence.SequenceFormat.NativeText)
        self.setText(text)

    def _clear(self) -> None:
        self._value = ""
        self._show_value()
        self.hotkey_changed.emit("")
