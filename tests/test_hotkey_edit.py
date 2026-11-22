import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from screenshotbuffer.ui.hotkey_edit import HotkeyEdit

M = Qt.KeyboardModifier


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def edit():
    w = HotkeyEdit()
    w.show()
    changes: list[str] = []
    recording: list[bool] = []
    w.hotkey_changed.connect(changes.append)
    w.recording_changed.connect(recording.append)
    w.changes, w.recording = changes, recording
    yield w
    w.close()


def start(w: HotkeyEdit) -> None:
    QTest.mouseClick(w, Qt.MouseButton.LeftButton)
    assert w.recording == [True]


def test_records_a_chord(edit):
    start(edit)
    QTest.keyClick(edit, Qt.Key.Key_K, M.ControlModifier | M.ShiftModifier)
    assert edit.value() == "Ctrl+Shift+K"
    assert edit.changes == ["Ctrl+Shift+K"]
    assert edit.recording == [True, False]


def test_print_screen_is_recorded_from_key_release(edit):
    # Windows delivers only a key release for Print Screen.
    start(edit)
    QTest.keyRelease(edit, Qt.Key.Key_Print, M.ControlModifier)
    assert edit.value() == "Ctrl+Print"


def test_modifier_alone_does_not_finish(edit):
    start(edit)
    QTest.keyPress(edit, Qt.Key.Key_Control, M.ControlModifier)
    assert edit.changes == []
    assert edit.recording == [True]


def test_escape_cancels_and_backspace_clears(edit):
    edit.set_value("Ctrl+Print")
    start(edit)
    QTest.keyClick(edit, Qt.Key.Key_Escape)
    assert edit.value() == "Ctrl+Print" and edit.changes == []
    QTest.mouseClick(edit, Qt.MouseButton.LeftButton)
    QTest.keyClick(edit, Qt.Key.Key_Backspace)
    assert edit.value() == "" and edit.changes == [""]


def test_tab_is_captured_while_recording(edit):
    start(edit)
    QTest.keyClick(edit, Qt.Key.Key_Tab, M.AltModifier)
    assert edit.value() == "Alt+Tab"


def test_raw_print_screen_release_is_recorded(edit):
    # Windows path: the native filter reports a lone Print Screen release.
    start(edit)
    edit.setFocus()
    QApplication.processEvents()
    edit.print_screen_released(M.ControlModifier | M.AltModifier)
    assert edit.value() == "Ctrl+Alt+Print"
    assert edit.recording == [True, False]


def test_raw_print_screen_ignored_when_not_recording(edit):
    edit.print_screen_released(M.ControlModifier)
    assert edit.value() == "" and edit.changes == []
