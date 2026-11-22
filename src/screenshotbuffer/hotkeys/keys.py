"""Translation between Qt key sequences and native hotkey codes.

Hotkeys are stored as Qt portable key-sequence strings such as
``"Ctrl+Print"``. The native backends need Windows virtual-key codes or
macOS virtual key codes (``kVK_*``), which are layout-independent physical
key positions.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from PySide6.QtCore import QKeyCombination, Qt
from PySide6.QtGui import QKeySequence

K = Qt.Key
M = Qt.KeyboardModifier

MODIFIER_KEYS = {
    K.Key_Control,
    K.Key_Shift,
    K.Key_Alt,
    K.Key_Meta,
    K.Key_AltGr,
    K.Key_Super_L,
    K.Key_Super_R,
    K.Key_CapsLock,
    K.Key_NumLock,
    K.Key_unknown,
}


def _build_win_table() -> dict[int, int]:
    t: dict[int, int] = {}
    for i in range(26):
        t[K.Key_A.value + i] = 0x41 + i
    for i in range(10):
        t[K.Key_0.value + i] = 0x30 + i
    for i in range(24):
        t[K.Key_F1.value + i] = 0x70 + i
    named = {
        K.Key_Print: 0x2C,
        K.Key_Pause: 0x13,
        K.Key_ScrollLock: 0x91,
        K.Key_Insert: 0x2D,
        K.Key_Delete: 0x2E,
        K.Key_Home: 0x24,
        K.Key_End: 0x23,
        K.Key_PageUp: 0x21,
        K.Key_PageDown: 0x22,
        K.Key_Left: 0x25,
        K.Key_Up: 0x26,
        K.Key_Right: 0x27,
        K.Key_Down: 0x28,
        K.Key_Space: 0x20,
        K.Key_Escape: 0x1B,
        K.Key_Tab: 0x09,
        K.Key_Return: 0x0D,
        K.Key_Backspace: 0x08,
        K.Key_Minus: 0xBD,
        K.Key_Equal: 0xBB,
        K.Key_Comma: 0xBC,
        K.Key_Period: 0xBE,
        K.Key_Slash: 0xBF,
        K.Key_Semicolon: 0xBA,
        K.Key_Apostrophe: 0xDE,
        K.Key_BracketLeft: 0xDB,
        K.Key_BracketRight: 0xDD,
        K.Key_Backslash: 0xDC,
        K.Key_QuoteLeft: 0xC0,
    }
    for k, v in named.items():
        t[k.value] = v
    return t


def _build_mac_table() -> dict[int, int]:
    letters = {
        "A": 0x00, "S": 0x01, "D": 0x02, "F": 0x03, "H": 0x04, "G": 0x05, "Z": 0x06,
        "X": 0x07, "C": 0x08, "V": 0x09, "B": 0x0B, "Q": 0x0C, "W": 0x0D, "E": 0x0E,
        "R": 0x0F, "Y": 0x10, "T": 0x11, "O": 0x1F, "U": 0x20, "I": 0x22, "P": 0x23,
        "L": 0x25, "J": 0x26, "K": 0x28, "N": 0x2D, "M": 0x2E,
    }  # fmt: skip
    digits = {1: 0x12, 2: 0x13, 3: 0x14, 4: 0x15, 6: 0x16, 5: 0x17, 9: 0x19, 7: 0x1A, 8: 0x1C, 0: 0x1D}
    fkeys = [0x7A, 0x78, 0x63, 0x76, 0x60, 0x61, 0x62, 0x64, 0x65, 0x6D, 0x67, 0x6F,
             0x69, 0x6B, 0x71, 0x6A, 0x40, 0x4F, 0x50, 0x5A]  # fmt: skip
    t: dict[int, int] = {}
    for ch, code in letters.items():
        t[ord(ch)] = code  # Qt letter keys equal their uppercase ASCII codes
    for d, code in digits.items():
        t[K.Key_0.value + d] = code
    for i, code in enumerate(fkeys):
        t[K.Key_F1.value + i] = code
    named = {
        K.Key_Equal: 0x18,
        K.Key_Minus: 0x1B,
        K.Key_BracketRight: 0x1E,
        K.Key_BracketLeft: 0x21,
        K.Key_Return: 0x24,
        K.Key_Apostrophe: 0x27,
        K.Key_Semicolon: 0x29,
        K.Key_Backslash: 0x2A,
        K.Key_Comma: 0x2B,
        K.Key_Slash: 0x2C,
        K.Key_Period: 0x2F,
        K.Key_Tab: 0x30,
        K.Key_Space: 0x31,
        K.Key_QuoteLeft: 0x32,
        K.Key_Backspace: 0x33,
        K.Key_Escape: 0x35,
        K.Key_Insert: 0x72,  # "Help" on Apple keyboards
        K.Key_Home: 0x73,
        K.Key_PageUp: 0x74,
        K.Key_Delete: 0x75,
        K.Key_End: 0x77,
        K.Key_PageDown: 0x79,
        K.Key_Left: 0x7B,
        K.Key_Right: 0x7C,
        K.Key_Down: 0x7D,
        K.Key_Up: 0x7E,
    }
    for k, v in named.items():
        t[k.value] = v
    return t


WIN_VK = _build_win_table()
MAC_KEYCODE = _build_mac_table()
# PC keyboards attached to a Mac send F13 for Print Screen.
MAC_KEYCODE[K.Key_Print.value] = MAC_KEYCODE[K.Key_F13.value]

_WIN_VK_REVERSE = {v: k for k, v in WIN_VK.items()}
_MAC_KEYCODE_REVERSE = {v: k for k, v in MAC_KEYCODE.items() if k != K.Key_Print.value}

# Windows RegisterHotKey modifier flags
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x1, 0x2, 0x4, 0x8
# Carbon modifier flags
MAC_CMD, MAC_SHIFT, MAC_OPTION, MAC_CONTROL = 0x100, 0x200, 0x800, 0x1000


@dataclass(frozen=True, slots=True)
class Hotkey:
    key: int  # Qt::Key value
    modifiers: int  # Qt::KeyboardModifier flags (Ctrl/Shift/Alt/Meta only)

    def to_string(self) -> str:
        return sequence_from(self.key, self.modifiers).toString(QKeySequence.SequenceFormat.PortableText)

    def display(self) -> str:
        text = sequence_from(self.key, self.modifiers).toString(QKeySequence.SequenceFormat.NativeText)
        if sys.platform == "win32" and self.key == K.Key_Print.value:
            # Match the keycap label users know instead of Qt's "Print".
            text = text[: -len("Print")] + "PrtScn" if text.endswith("Print") else text
        return text

    def win_native(self) -> tuple[int, int] | None:
        vk = WIN_VK.get(self.key)
        if vk is None:
            return None
        mods = 0
        if self.modifiers & M.ControlModifier.value:
            mods |= MOD_CONTROL
        if self.modifiers & M.ShiftModifier.value:
            mods |= MOD_SHIFT
        if self.modifiers & M.AltModifier.value:
            mods |= MOD_ALT
        if self.modifiers & M.MetaModifier.value:
            mods |= MOD_WIN
        return mods, vk

    def mac_native(self) -> tuple[int, int] | None:
        code = MAC_KEYCODE.get(self.key)
        if code is None:
            return None
        # Qt on macOS: ControlModifier = Command, MetaModifier = Control.
        mods = 0
        if self.modifiers & M.ControlModifier.value:
            mods |= MAC_CMD
        if self.modifiers & M.ShiftModifier.value:
            mods |= MAC_SHIFT
        if self.modifiers & M.AltModifier.value:
            mods |= MAC_OPTION
        if self.modifiers & M.MetaModifier.value:
            mods |= MAC_CONTROL
        return mods, code


_MOD_MASK = M.ControlModifier.value | M.ShiftModifier.value | M.AltModifier.value | M.MetaModifier.value


def sequence_from(key: int, modifiers: int) -> QKeySequence:
    return QKeySequence(QKeyCombination(M(modifiers & _MOD_MASK), K(key)))


def parse(text: str) -> Hotkey | None:
    """Parse a portable key-sequence string; None if empty or not a single chord."""
    if not text:
        return None
    seq = QKeySequence.fromString(text, QKeySequence.SequenceFormat.PortableText)
    if seq.count() != 1:
        return None
    combo = seq[0]
    key = combo.key().value
    if key in {k.value for k in MODIFIER_KEYS}:
        return None
    return Hotkey(key, combo.keyboardModifiers().value & _MOD_MASK)


def display(text: str) -> str:
    hk = parse(text)
    return hk.display() if hk else ""


def key_from_native(native_virtual_key: int) -> int | None:
    """Qt key for a platform virtual key code (QKeyEvent.nativeVirtualKey()).

    Used when recording hotkeys so that e.g. Shift+9 is stored as "Shift+9"
    rather than the layout-dependent "Shift+(".
    """
    if sys.platform == "darwin":
        return _MAC_KEYCODE_REVERSE.get(native_virtual_key)
    if sys.platform == "win32":
        return _WIN_VK_REVERSE.get(native_virtual_key)
    return None
