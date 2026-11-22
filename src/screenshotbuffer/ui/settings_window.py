"""Main settings window. Every change applies immediately."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QRect, QSignalBlocker, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, config
from ..config import ACTION_LABELS, ACTIONS
from ..hotkeys import keys
from .hotkey_edit import HotkeyEdit
from .style import app_icon

if TYPE_CHECKING:
    from ..app import App


def _hint(text: str, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(wrap)
    label.setStyleSheet("color: palette(placeholder-text);")
    return label


def _spin(lo: int, hi: int, suffix: str = "") -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setSuffix(suffix)
    s.setKeyboardTracking(False)
    s.setAlignment(Qt.AlignmentFlag.AlignRight)
    return s


REGION_EDIT_HELP = [
    ("Drag inside", "Move the region"),
    ("Drag a handle or edge", "Resize"),
    ("Shift + drag handle", "Keep aspect ratio (uniform scale)"),
    ("Alt / Ctrl + drag handle", "Resize symmetrically around the center"),
    ("Shift + drag inside", "Move along one axis only"),
    ("Arrow keys", "Nudge 1 px (Shift: 10 px)"),
    ("Enter, double-click or Done", "Finish editing"),
    ("Esc", "Cancel and restore the previous region"),
]


def _mac_names(text: str) -> str:
    if config.IS_MAC:
        return text.replace("Alt / Ctrl", "Option / Cmd").replace("Alt/Ctrl", "Option/Cmd")
    return text


class ShortcutsDialog(QDialog):
    """Cheat sheet for every shortcut, reachable from the tray menu."""

    def __init__(self, app: App) -> None:
        super().__init__(None)
        self._app = app
        self.setWindowTitle("Keyboard shortcuts - Screenshot Buffer")
        self.setWindowIcon(app_icon())
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        glob = QGroupBox("Global shortcuts (work in any application)")
        self._grid = QGridLayout(glob)
        self._grid.setHorizontalSpacing(24)
        self._grid.setVerticalSpacing(8)
        layout.addWidget(glob)

        region = QGroupBox("While adjusting the capture region")
        rgrid = QGridLayout(region)
        rgrid.setHorizontalSpacing(24)
        rgrid.setVerticalSpacing(6)
        for row, (keys_text, what) in enumerate(REGION_EDIT_HELP):
            k = QLabel(f"<b>{_mac_names(keys_text)}</b>")
            rgrid.addWidget(k, row, 0)
            rgrid.addWidget(QLabel(what), row, 1)
        layout.addWidget(region)

        preview = QGroupBox("Preview window")
        pgrid = QGridLayout(preview)
        pgrid.setHorizontalSpacing(24)
        pgrid.setVerticalSpacing(6)
        for row, (k, what) in enumerate(
            [
                ("Drag", "Move (drag the border to resize)"),
                ("Mouse wheel", "Change the delay by 0.5 s (Shift: 0.1 s)"),
                ("Double-click", "Copy the frame shown"),
                ("Right-click", "More options"),
            ]
        ):
            pgrid.addWidget(QLabel(f"<b>{k}</b>"), row, 0)
            pgrid.addWidget(QLabel(what), row, 1)
        layout.addWidget(preview)

        self._key_grids = [self._grid, rgrid, pgrid]
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        change = buttons.addButton("Change shortcuts...", QDialogButtonBox.ButtonRole.ActionRole)
        change.clicked.connect(self._change)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self.refresh()
        self._align_columns()

    def refresh(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        errors = self._app.hotkeys.errors()
        for row, action in enumerate(ACTIONS):
            text = keys.display(self._app.settings.hotkeys.get(action, ""))
            key_label = QLabel(f"<b>{text}</b>" if text else "<i>not set</i>")
            key_label.setTextFormat(Qt.TextFormat.RichText)
            self._grid.addWidget(key_label, row, 0)
            self._grid.addWidget(QLabel(ACTION_LABELS[action]), row, 1)
            err = errors.get(action)
            if err and text:
                warn = QLabel(f"Not active: {err}")
                warn.setStyleSheet("color: #d9534f;")
                self._grid.addWidget(warn, row, 2)
        self._align_columns()

    def _align_columns(self) -> None:
        # Line the key column up across the three groups.
        grids = getattr(self, "_key_grids", [self._grid])
        width = 0
        for grid in grids:
            for row in range(grid.rowCount()):
                item = grid.itemAtPosition(row, 0)
                if item is not None and item.widget() is not None:
                    width = max(width, item.widget().sizeHint().width())
        for grid in grids:
            grid.setColumnMinimumWidth(0, width)
            grid.setColumnStretch(0, 0)
            grid.setColumnStretch(grid.columnCount() - 1, 1)

    def _change(self) -> None:
        self.close()
        self._app.show_settings(focus_shortcuts=True)


class SettingsWindow(QWidget):
    def __init__(self, app: App) -> None:
        super().__init__(None)
        self._app = app
        self._updating = 0
        self._screens_key: tuple = ()
        self.setWindowTitle("Screenshot Buffer")
        self.setWindowIcon(app_icon())

        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.addWidget(self._build_recording())
        root.addWidget(self._build_area())
        root.addWidget(self._build_preview())
        self._shortcuts_box = self._build_shortcuts()
        root.addWidget(self._shortcuts_box)

        bottom = QHBoxLayout()
        quit_btn = QPushButton("Quit Screenshot Buffer")
        quit_btn.clicked.connect(app.quit)
        bottom.addWidget(quit_btn)
        bottom.addSpacing(8)
        bottom.addWidget(_hint(f"Version {__version__}"))
        bottom.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.close)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

        self._stats_timer = QTimer(self, interval=500)
        self._stats_timer.timeout.connect(self._refresh_stats)

        app.state_changed.connect(self.refresh)
        self.refresh()
        self.setMinimumWidth(600)

    # ----- sections ------------------------------------------------------
    def _build_recording(self) -> QGroupBox:
        box = QGroupBox("Recording")
        form = QFormLayout(box)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.delay = QDoubleSpinBox()
        self.delay.setRange(config.DELAY_MIN, config.DELAY_MAX)
        self.delay.setDecimals(1)
        self.delay.setSingleStep(0.5)
        self.delay.setSuffix(" s")
        self.delay.setKeyboardTracking(False)
        self.delay.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.delay.setMinimumWidth(90)
        self.delay.valueChanged.connect(lambda v: self._apply(delay=round(v, 1)))
        form.addRow("Copy the screen from", self._with_hint(self.delay, "before the shortcut is pressed"))

        self.fps = QDoubleSpinBox()
        self.fps.setRange(config.FPS_MIN, config.FPS_MAX)
        self.fps.setDecimals(0)
        self.fps.setSuffix(" fps")
        self.fps.setKeyboardTracking(False)
        self.fps.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.fps.setMinimumWidth(90)
        self.fps.valueChanged.connect(lambda v: self._apply(fps=float(v)))
        form.addRow("Capture rate", self._with_hint(self.fps, "higher catches shorter animation steps"))

        self.memory = _spin(config.MEMORY_MIN_MB, config.MEMORY_MAX_MB, " MB")
        self.memory.setSingleStep(256)
        self.memory.setMinimumWidth(90)
        self.memory.valueChanged.connect(lambda v: self._apply(memory_limit_mb=int(v)))
        form.addRow(
            "Memory limit", self._with_hint(self.memory, "frames are compressed; unchanged frames are free")
        )

        status_row = QHBoxLayout()
        self.stats = QLabel()
        self.stats.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        status_row.addWidget(self.stats, 1)
        self.pause_btn = QPushButton()
        self.pause_btn.clicked.connect(self._app.toggle_pause)
        status_row.addWidget(self.pause_btn)
        form.addRow("Buffer", status_row)
        return box

    def _with_hint(self, w: QWidget, hint: str) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(w)
        lay.addWidget(_hint(hint), 1)
        return row

    def _build_area(self) -> QGroupBox:
        box = QGroupBox("Recorded area")
        grid = QGridLayout(box)
        grid.setColumnStretch(1, 1)

        self.src_screen = QRadioButton("Full screen")
        self.src_region = QRadioButton("Region")
        group = QButtonGroup(self)
        group.addButton(self.src_screen)
        group.addButton(self.src_region)
        self.src_screen.toggled.connect(self._source_toggled)
        self.src_region.toggled.connect(self._source_toggled)

        self.screen_combo = QComboBox()
        self.screen_combo.currentIndexChanged.connect(self._screen_chosen)
        grid.addWidget(self.src_screen, 0, 0)
        grid.addWidget(self.screen_combo, 0, 1, 1, 2)

        grid.addWidget(self.src_region, 1, 0)
        coords = QHBoxLayout()
        coords.setSpacing(6)
        self.rx, self.ry = _spin(-100000, 100000), _spin(-100000, 100000)
        self.rw = _spin(config.REGION_MIN_SIZE, 100000)
        self.rh = _spin(config.REGION_MIN_SIZE, 100000)
        for label, spin in (("X", self.rx), ("Y", self.ry), ("W", self.rw), ("H", self.rh)):
            lab = QLabel(label)
            lab.setStyleSheet("color: palette(placeholder-text);")
            coords.addWidget(lab)
            spin.setMinimumWidth(72)
            spin.valueChanged.connect(self._region_spun)
            coords.addWidget(spin)
        coords.addStretch(1)
        grid.addLayout(coords, 1, 1, 1, 2)

        buttons = QHBoxLayout()
        select = QPushButton("Select new region...")
        select.clicked.connect(self._app.start_region_selection)
        self.adjust_btn = QPushButton("Adjust on screen...")
        self.adjust_btn.clicked.connect(self._app.edit_region)
        buttons.addWidget(select)
        buttons.addWidget(self.adjust_btn)
        buttons.addStretch(1)
        grid.addLayout(buttons, 2, 1, 1, 2)

        self.outline = QCheckBox("Show an outline around the region while recording")
        self.outline.toggled.connect(lambda on: self._apply(show_region_outline=on))
        grid.addWidget(self.outline, 3, 1, 1, 2)
        grid.addWidget(
            _hint("Coordinates are in screen points; images use the display's full resolution.", wrap=True),
            4,
            1,
            1,
            2,
        )
        return box

    def _build_preview(self) -> QGroupBox:
        box = QGroupBox("Preview and feedback")
        form = QFormLayout(box)
        row = QHBoxLayout()
        self.preview_on = QCheckBox("Show the delayed-frame preview")
        self.preview_on.toggled.connect(lambda on: self._updating or self._app.set_preview_visible(on))
        row.addWidget(self.preview_on)
        row.addStretch(1)
        reset = QPushButton("Reset position")
        reset.clicked.connect(self._app.reset_preview_geometry)
        row.addWidget(reset)
        form.addRow(row)

        self.notify = QComboBox()
        self.notify.addItem("Only when the preview is hidden", config.NOTIFY_WHEN_HIDDEN)
        self.notify.addItem("Always", config.NOTIFY_ALWAYS)
        self.notify.addItem("Never", config.NOTIFY_NEVER)
        self.notify.currentIndexChanged.connect(
            lambda _i: self._apply(notify_on_copy=self.notify.currentData())
        )
        form.addRow("Notification after copying", self.notify)
        return box

    def _build_shortcuts(self) -> QGroupBox:
        box = QGroupBox("Global shortcuts")
        grid = QGridLayout(box)
        grid.setColumnStretch(0, 1)
        self.hotkey_edits: dict[str, HotkeyEdit] = {}
        self.hotkey_status: dict[str, QLabel] = {}
        for row, action in enumerate(ACTIONS):
            grid.addWidget(QLabel(ACTION_LABELS[action]), row * 2, 0)
            edit = HotkeyEdit()
            edit.setMinimumWidth(170)
            edit.hotkey_changed.connect(lambda text, a=action: self._app.set_hotkey(a, text))
            edit.recording_changed.connect(self._recording_changed)
            grid.addWidget(edit, row * 2, 1)
            clear = QToolButton()
            clear.setText("Clear")
            clear.setToolTip("Remove this shortcut")
            clear.clicked.connect(lambda _=False, a=action: self._app.set_hotkey(a, ""))
            grid.addWidget(clear, row * 2, 2)
            status = QLabel()
            status.setStyleSheet("color: #d9534f;")
            status.setWordWrap(True)
            status.hide()
            grid.addWidget(status, row * 2 + 1, 0, 1, 3)
            self.hotkey_edits[action] = edit
            self.hotkey_status[action] = status

        bottom = QHBoxLayout()
        defaults = QPushButton("Restore defaults")
        defaults.clicked.connect(self._app.restore_default_hotkeys)
        bottom.addWidget(defaults)
        cheat = QPushButton("All shortcuts...")
        cheat.clicked.connect(self._app.show_shortcuts)
        bottom.addWidget(cheat)
        bottom.addStretch(1)
        grid.addLayout(bottom, len(ACTIONS) * 2, 0, 1, 3)
        if self._app.hotkeys.unavailable_reason:
            grid.addWidget(
                _hint(self._app.hotkeys.unavailable_reason, wrap=True), len(ACTIONS) * 2 + 1, 0, 1, 3
            )
        return box

    # ----- syncing -------------------------------------------------------
    def _apply(self, **changes: object) -> None:
        if not self._updating:
            self._app.update_settings(**changes)

    def _source_toggled(self, on: bool) -> None:
        if self._updating or not on:
            return
        if self.src_region.isChecked():
            self._app.use_region()
        else:
            self._app.set_source(config.SOURCE_SCREEN)

    def _screen_chosen(self, _i: int) -> None:
        if not self._updating:
            self._app.set_screen(self.screen_combo.currentData() or "")

    def _region_spun(self, _v: int) -> None:
        if not self._updating:
            self._app.set_region(QRect(self.rx.value(), self.ry.value(), self.rw.value(), self.rh.value()))

    def _recording_changed(self, on: bool) -> None:
        if on:
            self._app.hotkeys.suspend()
        else:
            self._app.hotkeys.resume()
            self.refresh()

    def _inputs(self) -> list[QWidget]:
        return [
            self.delay,
            self.fps,
            self.memory,
            self.src_screen,
            self.src_region,
            self.screen_combo,
            self.rx,
            self.ry,
            self.rw,
            self.rh,
            self.outline,
            self.preview_on,
            self.notify,
        ]

    def refresh(self) -> None:
        """Show the app's current state. Never feeds changes back into the app."""
        s = self._app.settings
        self._updating += 1
        blockers = [QSignalBlocker(w) for w in self._inputs()]
        try:
            self.delay.setValue(s.delay)
            self.fps.setValue(s.fps)
            self.memory.setValue(s.memory_limit_mb)
            self.src_screen.setChecked(s.source == config.SOURCE_SCREEN)
            self.src_region.setChecked(s.source == config.SOURCE_REGION)
            self._fill_screens()
            idx = self.screen_combo.findData(self._app.current_screen().name())
            self.screen_combo.setCurrentIndex(max(0, idx))

            region = self._app.region_rect()
            values = (region.x(), region.y(), region.width(), region.height())
            for spin, v in zip((self.rx, self.ry, self.rw, self.rh), values, strict=True):
                spin.setValue(v)
            self.outline.setChecked(s.show_region_outline)
            self.preview_on.setChecked(s.preview_visible)
            self.notify.setCurrentIndex(max(0, self.notify.findData(s.notify_on_copy)))

            errors = self._app.hotkeys.errors()
            for action, edit in self.hotkey_edits.items():
                edit.set_value(s.hotkeys.get(action, ""))
                err = errors.get(action) if s.hotkeys.get(action) else None
                status = self.hotkey_status[action]
                status.setText(f"Not active: {err}." if err else "")
                status.setVisible(bool(err))
            self.pause_btn.setText("Resume recording" if self._app.paused else "Pause recording")
        finally:
            for b in blockers:
                b.unblock()
            self._updating -= 1
        self._refresh_stats()

    def _fill_screens(self) -> None:
        screens = QGuiApplication.screens()
        primary = QGuiApplication.primaryScreen()
        key = tuple((s.name(), s.geometry().getRect(), s.devicePixelRatio(), s is primary) for s in screens)
        if key == self._screens_key:
            return
        self._screens_key = key
        self.screen_combo.clear()
        for i, scr in enumerate(screens, start=1):
            g, dpr = scr.geometry(), scr.devicePixelRatio()
            parts = [f"Display {i}"]
            if scr.model().strip():
                parts.append(scr.model().strip())
            label = " - ".join(parts) + f" ({round(g.width() * dpr)} x {round(g.height() * dpr)})"
            if scr is primary:
                label += ", primary"
            self.screen_combo.addItem(label, scr.name())

    def _refresh_stats(self) -> None:
        self.stats.setText(self._app.status_text())

    def focus_shortcuts(self) -> None:
        edit = self.hotkey_edits[ACTIONS[0]]
        edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.refresh()
        self._stats_timer.start()

    def closeEvent(self, e: QCloseEvent) -> None:
        self._stats_timer.stop()
        for edit in self.hotkey_edits.values():
            edit.clearFocus()
        super().closeEvent(e)
