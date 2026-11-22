import pytest
from PySide6.QtCore import Qt

from screenshotbuffer.hotkeys import keys

K = Qt.Key


@pytest.fixture(scope="module", autouse=True)
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_parse_and_roundtrip():
    hk = keys.parse("Ctrl+Print")
    assert hk is not None
    assert hk.key == K.Key_Print.value
    assert hk.modifiers == Qt.KeyboardModifier.ControlModifier.value
    assert keys.parse(hk.to_string()) == hk


@pytest.mark.parametrize("text", ["", "Ctrl+A, Ctrl+B", "Ctrl+"])
def test_parse_rejects(text):
    assert keys.parse(text) is None


def test_windows_native_codes():
    assert keys.parse("Ctrl+Print").win_native() == (keys.MOD_CONTROL, 0x2C)
    assert keys.parse("Shift+Print").win_native() == (keys.MOD_SHIFT, 0x2C)
    assert keys.parse("Ctrl+Shift+Alt+Meta+F5").win_native() == (
        keys.MOD_CONTROL | keys.MOD_SHIFT | keys.MOD_ALT | keys.MOD_WIN,
        0x74,
    )
    assert keys.parse("Alt+Z").win_native() == (keys.MOD_ALT, 0x5A)
    assert keys.parse("Ctrl+9").win_native() == (keys.MOD_CONTROL, 0x39)


def test_mac_native_codes_swap_ctrl_and_meta():
    # Qt's Ctrl is the Command key on macOS; Meta is Control.
    assert keys.parse("Ctrl+Shift+9").mac_native() == (keys.MAC_CMD | keys.MAC_SHIFT, 0x19)
    assert keys.parse("Meta+Alt+A").mac_native() == (keys.MAC_CONTROL | keys.MAC_OPTION, 0x00)
    assert keys.parse("Ctrl+Print").mac_native() == (keys.MAC_CMD, 0x69)  # F13


def test_every_default_hotkey_is_mappable_on_both_platforms():
    from screenshotbuffer import config

    for text in config.DEFAULT_HOTKEYS.values():
        if text:
            hk = keys.parse(text)
            assert hk is not None
            assert hk.win_native() is not None
            assert hk.mac_native() is not None


def test_tables_are_injective():
    assert len(set(keys.WIN_VK.values())) == len(keys.WIN_VK)
    mac = {k: v for k, v in keys.MAC_KEYCODE.items() if k != K.Key_Print.value}
    assert len(set(mac.values())) == len(mac)


def test_display_is_human_readable():
    text = keys.display("Ctrl+Print")
    assert "Print" in text or "PrtSc" in text or "Prt" in text
    assert keys.display("") == ""
