"""Application controller: wires capture, hotkeys, tray and windows together."""

from __future__ import annotations

import contextlib
import getpass
import logging
import logging.handlers
import sys
import time

from PySide6.QtCore import QObject, QRect, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication, QImage, QKeySequence
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon, QWidget

from . import __version__, config, native
from .buffer import FrameBuffer, Pixels
from .capture import CaptureTarget, CaptureWorker
from .config import (
    ACTION_COPY,
    ACTION_SELECT_REGION,
    ACTION_TOGGLE_PAUSE,
    ACTION_TOGGLE_PREVIEW,
    ACTIONS,
    Settings,
    SettingsStore,
)
from .geometry import Rect, ScreenInfo, logical_to_native, screen_for_rect
from .hotkeys import HotkeyManager, keys
from .ui import style
from .ui.preview import PreviewWindow
from .ui.region import RegionEditor, RegionOutline, RegionSelector
from .ui.settings_window import SettingsWindow, ShortcutsDialog
from .ui.toast import Toast

log = logging.getLogger(__name__)

APP_NAME = "Screenshot Buffer"
PREVIEW_DEFAULT = (384, 216)
PREVIEW_MARGIN = 16
EXTRA_RETENTION = 2.0  # seconds kept beyond the delay, so small delay increases work at once


def to_qimage(px: Pixels) -> QImage:
    img = QImage(px.data, px.width, px.height, px.stride, QImage.Format.Format_RGB32)
    return img.copy()  # detach from the Python buffer


class App(QObject):
    state_changed = Signal()

    def __init__(self, qapp: QApplication, store: SettingsStore) -> None:
        super().__init__()
        self.qapp = qapp
        self.store = store
        self.first_run = not store.path.exists()
        self.settings: Settings = store.load()
        self.paused = False

        self.buffer = FrameBuffer(self._retention(), self.settings.memory_limit_mb * 1024 * 1024)
        self.worker = CaptureWorker(self.buffer)

        self._save_timer = QTimer(self, singleShot=True, interval=400)
        self._save_timer.timeout.connect(self._save)

        self.hotkeys = HotkeyManager(self)
        self.hotkeys.activated.connect(self._on_hotkey)

        self.toast = Toast()
        self.preview = PreviewWindow()
        self.preview.copy_requested.connect(self.copy_delayed)
        self.preview.hide_requested.connect(lambda: self.set_preview_visible(False))
        self.preview.pause_toggle_requested.connect(self.toggle_pause)
        self.preview.settings_requested.connect(self.show_settings)
        self.preview.delay_step_requested.connect(self._step_delay)
        self.preview.geometry_committed.connect(self._preview_moved)

        self.outline = RegionOutline()
        self.editor = RegionEditor()
        self.editor.region_changed.connect(self._editor_region_changed)
        self.editor.finished.connect(self._editor_finished)
        self.selector = RegionSelector()
        self.selector.selected.connect(self._region_selected)
        self.selector.cancelled.connect(self._selection_cancelled)

        self._settings_window: SettingsWindow | None = None
        self._shortcuts_dialog: ShortcutsDialog | None = None
        self._hidden_dialogs: list[QWidget] = []

        self._preview_timer = QTimer(self, interval=50)
        self._preview_timer.timeout.connect(self._update_preview)

        self._build_tray()

        for screen in qapp.screens():
            self._watch_screen(screen)
        qapp.screenAdded.connect(self._on_screens_changed)
        qapp.screenAdded.connect(self._watch_screen)
        qapp.screenRemoved.connect(self._on_screens_changed)
        qapp.primaryScreenChanged.connect(self._on_screens_changed)

    # ----- lifecycle ---------------------------------------------------
    def start(self) -> None:
        native.make_background_app()
        if not native.has_screen_capture_permission():
            native.request_screen_capture_permission()
            QMessageBox.information(
                None,
                APP_NAME,
                "Screenshot Buffer needs the Screen Recording permission.\n\n"
                "Open System Settings > Privacy & Security > Screen Recording, enable it for "
                "the app that runs Screenshot Buffer (e.g. Terminal or Python), then restart.",
            )
        self.worker.configure(fps=self.settings.fps)
        self._apply_target()
        self.worker.start()
        errors = self.hotkeys.apply(self.settings.hotkeys)
        self._apply_preview_geometry()
        self._sync_visibility()
        self._preview_timer.start()
        self.tray.show()
        self._refresh_tray()

        failed = [config.ACTION_LABELS[a] for a, e in errors.items() if e]
        if failed:
            self.toast.show_message(
                "Some shortcuts are not active",
                "In use by another app: " + ", ".join(failed) + ". Change them in Settings.",
                accent=style.WARNING,
                ms=6000,
            )
        else:
            copy_key = self._hotkey_text(ACTION_COPY) or "the tray menu"
            preview_key = self._hotkey_text(ACTION_TOGGLE_PREVIEW)
            body = f"{copy_key}: copy the screen from {self.settings.delay:.1f} s ago"
            if preview_key:
                body += f"   {preview_key}: show/hide preview"
            self.toast.show_message(f"{APP_NAME} is recording", body, accent=style.ACCENT, ms=4500)
        if self.first_run:
            self._save()

    def quit(self) -> None:
        self._preview_timer.stop()
        self.selector.stop()
        self.hotkeys.close()
        self.worker.stop()
        self._save()
        self.tray.hide()
        self.qapp.quit()

    def _save(self) -> None:
        self._save_timer.stop()
        try:
            self.store.save(self.settings)
        except OSError:
            log.exception("Could not save settings")

    def _changed(self) -> None:
        self._save_timer.start()
        self._refresh_tray()
        self.state_changed.emit()

    # ----- settings ----------------------------------------------------
    def update_settings(self, **changes: object) -> None:
        data = self.settings.to_dict()
        data.update(changes)
        new = Settings.from_dict(data)  # validates and clamps
        old = self.settings
        self.settings = new
        if new.fps != old.fps:
            self.worker.configure(fps=new.fps)
        if new.delay != old.delay or new.memory_limit_mb != old.memory_limit_mb:
            self.buffer.configure(retention=self._retention(), memory_limit=new.memory_limit_mb * 1024 * 1024)
            self.preview.set_delay(new.delay)
        if new.source != old.source or new.screen_name != old.screen_name or new.region != old.region:
            self._apply_target()
        if (
            new.preview_visible != old.preview_visible
            or new.show_region_outline != old.show_region_outline
            or new.source != old.source
        ):
            self._sync_visibility()
        self._changed()

    def _retention(self) -> float:
        return self.settings.delay + EXTRA_RETENTION

    def _step_delay(self, step: float) -> None:
        value = round(min(config.DELAY_MAX, max(config.DELAY_MIN, self.settings.delay + step)), 1)
        self.update_settings(delay=value)

    # ----- capture area ------------------------------------------------
    def current_screen(self):
        for s in QGuiApplication.screens():
            if s.name() == self.settings.screen_name:
                return s
        return QGuiApplication.primaryScreen()

    def region_rect(self) -> QRect:
        if self.settings.region:
            return QRect(*self.settings.region)
        g = self.current_screen().geometry()
        w, h = round(g.width() * 0.6), round(g.height() * 0.6)
        return QRect(g.x() + (g.width() - w) // 2, g.y() + (g.height() - h) // 2, w, h)

    def capture_logical_rect(self) -> QRect:
        if self.settings.source == config.SOURCE_REGION:
            return self.region_rect()
        return self.current_screen().geometry()

    def _apply_target(self) -> None:
        r = self.capture_logical_rect()
        logical = Rect(r.x(), r.y(), r.width(), r.height())
        if sys.platform == "win32":
            # Windows capture works in physical pixels; Qt in device-independent ones.
            screens = [
                ScreenInfo(Rect(g.x(), g.y(), g.width(), g.height()), s.devicePixelRatio())
                for s in QGuiApplication.screens()
                for g in [s.geometry()]
            ]
            info = screen_for_rect(logical, screens)
            phys = logical_to_native(logical, info) if info else logical.rounded()
        else:
            phys = logical.rounded()  # macOS capture takes points
        target = CaptureTarget(int(phys.x), int(phys.y), max(1, int(phys.w)), max(1, int(phys.h)))
        self.worker.configure(target=target)
        self.outline.set_region(r)

    def set_source(self, source: str) -> None:
        if source == config.SOURCE_REGION and not self.settings.region:
            self.update_settings(source=source, region=list(self.region_rect().getRect()))
        else:
            self.update_settings(source=source)

    def use_region(self) -> None:
        if self.settings.region:
            self.set_source(config.SOURCE_REGION)
        else:
            self.start_region_selection()

    def set_screen(self, name: str) -> None:
        self.update_settings(screen_name=name, source=config.SOURCE_SCREEN)

    def set_region(self, r: QRect) -> None:
        r = QRect(
            r.x(), r.y(), max(config.REGION_MIN_SIZE, r.width()), max(config.REGION_MIN_SIZE, r.height())
        )
        self.update_settings(region=[r.x(), r.y(), r.width(), r.height()], source=config.SOURCE_REGION)
        if self.editor.isVisible() and self.editor.region() != r:
            self.editor.begin(r)

    def start_region_selection(self) -> None:
        if self.selector.active():
            self.selector.start()  # just brings it to the front
            return
        if self.editor.isVisible():
            self.editor.finish(True)
        # Get our own windows out of the way of the area being chosen. The
        # selector freezes the screen, so wait until they have actually
        # disappeared (Windows fades windows out).
        hid_dialogs = self._hide_dialogs()
        self.preview.hide()
        self.outline.hide()
        self.toast.hide()
        QTimer.singleShot(300 if hid_dialogs else 0, self.selector.start)

    def _hide_dialogs(self) -> bool:
        """Hide the settings/shortcuts windows while the user works on the region."""
        hid = False
        for w in (self._settings_window, self._shortcuts_dialog):
            if w is not None and w.isVisible():
                w.hide()
                if w not in self._hidden_dialogs:
                    self._hidden_dialogs.append(w)
                hid = True
        return hid

    def _restore_dialogs(self) -> None:
        dialogs, self._hidden_dialogs = self._hidden_dialogs, []
        for w in dialogs:
            native.bring_to_front(w)

    def _selection_cancelled(self) -> None:
        self._sync_visibility()
        self._restore_dialogs()

    def _region_selected(self, r: QRect) -> None:
        self.set_region(r)
        self.edit_region()

    def edit_region(self) -> None:
        if self.selector.active():
            return
        if self.settings.source != config.SOURCE_REGION or not self.settings.region:
            self.set_source(config.SOURCE_REGION)
        self._hide_dialogs()
        self.editor.begin(self.region_rect())
        self._sync_visibility()

    def _editor_region_changed(self, r: QRect) -> None:
        self.update_settings(region=[r.x(), r.y(), r.width(), r.height()], source=config.SOURCE_REGION)

    def _editor_finished(self, _accepted: bool) -> None:
        self._sync_visibility()
        self._restore_dialogs()

    def _on_screens_changed(self, *_args: object) -> None:
        QTimer.singleShot(200, self._screens_settled)

    def _watch_screen(self, screen) -> None:
        screen.geometryChanged.connect(self._on_screens_changed)
        screen.logicalDotsPerInchChanged.connect(self._on_screens_changed)

    def _screens_settled(self) -> None:
        self._apply_target()
        self._apply_preview_geometry()
        self.state_changed.emit()

    # ----- visibility --------------------------------------------------
    def _sync_visibility(self) -> None:
        if self.selector.active():
            return
        s = self.settings
        self.preview.set_delay(s.delay)
        self.preview.set_paused(self.paused)
        if s.preview_visible:
            if not self.preview.isVisible():
                self.preview.show()
        else:
            self.preview.hide()
        show_outline = (
            s.source == config.SOURCE_REGION and s.show_region_outline and not self.editor.isVisible()
        )
        self.outline.set_paused(self.paused)
        if show_outline:
            self.outline.set_region(self.region_rect())
            self.outline.show()
        else:
            self.outline.hide()

    def set_preview_visible(self, visible: bool) -> None:
        self.update_settings(preview_visible=bool(visible))

    def toggle_preview(self) -> None:
        self.set_preview_visible(not self.settings.preview_visible)

    def _default_preview_geometry(self) -> QRect:
        area = QGuiApplication.primaryScreen().availableGeometry()
        w, h = PREVIEW_DEFAULT
        return QRect(area.right() - w - PREVIEW_MARGIN + 1, area.top() + PREVIEW_MARGIN, w, h)

    def _apply_preview_geometry(self) -> None:
        g = self.settings.preview_geometry
        rect = QRect(*g) if g else self._default_preview_geometry()
        # Bring it back if its monitor is gone.
        visible = any(
            s.availableGeometry().intersects(rect.adjusted(20, 20, -20, -20))
            for s in QGuiApplication.screens()
        )
        if not visible:
            rect = self._default_preview_geometry()
        self.preview.setGeometry(rect)

    def reset_preview_geometry(self) -> None:
        self.update_settings(preview_geometry=None)
        self._apply_preview_geometry()
        self.set_preview_visible(True)

    def _preview_moved(self, r: QRect) -> None:
        self.update_settings(preview_geometry=[r.x(), r.y(), r.width(), r.height()])

    def _update_preview(self) -> None:
        if not self.preview.isVisible():
            return
        frame = self.buffer.frame_at(time.monotonic() - self.settings.delay)
        if frame is None:
            stats = self.worker.stats()
            self.preview.set_frame(None, None)
            if self.paused:
                self.preview.set_message("Recording is paused")
            elif stats.error:
                self.preview.set_message("Screen capture failed", stats.error)
            else:
                self.preview.set_message("Waiting for the first frame...")
            return
        self.preview.set_message(None)
        if self.preview.frame_key() != frame.seq:
            self.preview.set_frame(to_qimage(frame.thumbnail()), frame.seq)

    # ----- actions -----------------------------------------------------
    def _on_hotkey(self, action: str, timestamp: float) -> None:
        log.debug("Hotkey %s", action)
        if action == ACTION_COPY:
            self.copy_delayed(timestamp)
        elif action == ACTION_TOGGLE_PREVIEW:
            self.toggle_preview()
        elif action == ACTION_SELECT_REGION:
            self.start_region_selection()
        elif action == ACTION_TOGGLE_PAUSE:
            self.toggle_pause()

    def copy_delayed(self, timestamp: float | None = None) -> None:
        pressed = time.monotonic() if timestamp is None or isinstance(timestamp, bool) else timestamp
        frame = self.buffer.frame_at(pressed - self.settings.delay)
        if frame is None:
            self.toast.show_message(
                "Nothing recorded yet", "Wait a moment and try again.", accent=style.WARNING
            )
            return
        image = to_qimage(frame.pixels())
        QGuiApplication.clipboard().setImage(image)
        log.info("Copied %dx%d frame #%d", image.width(), image.height(), frame.seq)
        self.preview.flash()
        mode = self.settings.notify_on_copy
        preview_shown = self.preview.isVisible()
        if mode == config.NOTIFY_ALWAYS or (mode == config.NOTIFY_WHEN_HIDDEN and not preview_shown):
            self.toast.show_message(
                "Copied to clipboard",
                f"Screen from {self.settings.delay:.1f} s ago, {image.width()} x {image.height()} px",
                thumb=image,
            )

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        self.worker.configure(paused=self.paused)
        self.tray.setIcon(style.app_icon(paused=self.paused, tray=True))
        self._sync_visibility()
        if not self.settings.preview_visible:
            self.toast.show_message(
                "Recording paused" if self.paused else "Recording resumed",
                accent=style.WARNING if self.paused else style.ACCENT,
            )
        self._refresh_tray()
        self.state_changed.emit()

    # ----- hotkeys -----------------------------------------------------
    def _hotkey_text(self, action: str) -> str:
        if self.hotkeys.errors().get(action):
            return ""
        return keys.display(self.settings.hotkeys.get(action, ""))

    def set_hotkey(self, action: str, text: str) -> None:
        hotkeys = dict(self.settings.hotkeys)
        if text:
            # A shortcut can only do one thing: take it away from any other action.
            for other, value in hotkeys.items():
                if other != action and value and keys.parse(value) == keys.parse(text):
                    hotkeys[other] = ""
        hotkeys[action] = text
        self.settings.hotkeys = hotkeys
        self.hotkeys.apply(hotkeys)
        self._changed()

    def restore_default_hotkeys(self) -> None:
        self.settings.hotkeys = dict(config.DEFAULT_HOTKEYS)
        self.hotkeys.apply(self.settings.hotkeys)
        self._changed()

    # ----- windows -----------------------------------------------------
    def show_settings(self, focus_shortcuts: bool = False) -> None:
        if self._settings_window is None:
            self._settings_window = SettingsWindow(self)
        native.bring_to_front(self._settings_window)
        if focus_shortcuts:
            self._settings_window.focus_shortcuts()

    def show_shortcuts(self) -> None:
        if self._shortcuts_dialog is None:
            self._shortcuts_dialog = ShortcutsDialog(self)
            self.state_changed.connect(self._shortcuts_dialog.refresh)
        self._shortcuts_dialog.refresh()
        native.bring_to_front(self._shortcuts_dialog)

    def status_text(self) -> str:
        stats = self.worker.stats()
        b = self.buffer.stats(time.monotonic())
        mb = b.nbytes / (1024 * 1024)
        frames = f"{b.frames} frame{'s' if b.frames != 1 else ''}, {mb:.1f} MB, {b.span:.1f} s of history"
        if self.paused:
            return f"Paused - {frames}"
        if stats.error:
            return f"Capture error: {stats.error}"
        return f"{frames}  |  {stats.fps:.1f} fps, {stats.grab_ms:.0f} ms per capture"

    # ----- tray --------------------------------------------------------
    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(style.app_icon(tray=True), self)
        self.tray.activated.connect(self._tray_activated)
        menu = QMenu()
        self._menu = menu  # QSystemTrayIcon does not take ownership
        self._status_action = menu.addAction("")
        self._status_action.setEnabled(False)
        menu.addSeparator()

        self._hotkey_actions: dict[str, QAction] = {}
        handlers = {
            ACTION_COPY: lambda: self.copy_delayed(),
            ACTION_TOGGLE_PREVIEW: self.toggle_preview,
            ACTION_SELECT_REGION: self.start_region_selection,
            ACTION_TOGGLE_PAUSE: self.toggle_pause,
        }
        for action in ACTIONS:
            a = menu.addAction(config.ACTION_LABELS[action])
            a.triggered.connect(handlers[action])
            self._hotkey_actions[action] = a
        menu.addSeparator()

        group = QActionGroup(menu)
        self._screen_action = menu.addAction("Record full screen")
        self._screen_action.setCheckable(True)
        self._screen_action.triggered.connect(lambda: self.set_source(config.SOURCE_SCREEN))
        self._region_action = menu.addAction("Record region")
        self._region_action.setCheckable(True)
        self._region_action.triggered.connect(self.use_region)
        group.addAction(self._screen_action)
        group.addAction(self._region_action)
        self._adjust_action = menu.addAction("Adjust region on screen...")
        self._adjust_action.triggered.connect(self.edit_region)
        menu.addSeparator()

        menu.addAction("Keyboard shortcuts...").triggered.connect(self.show_shortcuts)
        menu.addAction("Settings...").triggered.connect(lambda: self.show_settings())
        menu.addSeparator()
        menu.addAction("Quit").triggered.connect(self.quit)
        menu.aboutToShow.connect(self._refresh_tray)
        self.tray.setContextMenu(menu)

    def _refresh_tray(self) -> None:
        s = self.settings
        if self.paused:
            self._status_action.setText("Paused")
        else:
            where = "region" if s.source == config.SOURCE_REGION else "full screen"
            self._status_action.setText(f"Recording {where} at {s.fps:.0f} fps")
        labels = {
            ACTION_COPY: f"Copy screen from {s.delay:.1f} s ago",
            ACTION_TOGGLE_PREVIEW: "Hide preview" if s.preview_visible else "Show preview",
            ACTION_SELECT_REGION: "Select new region...",
            ACTION_TOGGLE_PAUSE: "Resume recording" if self.paused else "Pause recording",
        }
        for action, a in self._hotkey_actions.items():
            shortcut = self._hotkey_text(action)
            if sys.platform == "darwin":
                # Native menus render key equivalents themselves (with the usual symbols).
                a.setText(labels[action])
                portable = s.hotkeys.get(action, "") if shortcut else ""
                a.setShortcut(QKeySequence.fromString(portable, QKeySequence.SequenceFormat.PortableText))
            else:
                # Text after a tab is drawn as the right-aligned shortcut column.
                a.setText(f"{labels[action]}	{shortcut}" if shortcut else labels[action])
        self._screen_action.setChecked(s.source == config.SOURCE_SCREEN)
        self._region_action.setChecked(s.source == config.SOURCE_REGION)
        copy_key = self._hotkey_text(ACTION_COPY)
        tip = f"{APP_NAME}: " + ("paused" if self.paused else f"recording, {s.delay:.1f} s delay")
        if copy_key:
            tip += f"\n{copy_key} copies the delayed screen"
        self.tray.setToolTip(tip)

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if sys.platform == "darwin":
            return  # the menu opens on click
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_settings()


# ----- entry point -----------------------------------------------------------
def _setup_logging(store: SettingsStore) -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    if sys.stderr is not None:
        h = logging.StreamHandler()
        h.setFormatter(fmt)
        root.addHandler(h)
    try:
        store.directory.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            store.directory / "screenshotbuffer.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass


def _server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return f"screenshotbuffer-{user}"


def _send_to_running_instance(command: bytes, timeout_ms: int = 500) -> bool:
    """Deliver a command to an already running instance. False if none is running."""
    sock = QLocalSocket()
    sock.connectToServer(_server_name())
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write(command)
    sock.waitForBytesWritten(timeout_ms)
    sock.disconnectFromServer()
    return True


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv if argv is None else argv)
    if "--self-test" in args:
        from .selftest import run as run_self_test

        i = args.index("--self-test")
        return run_self_test(args[i + 1] if i + 1 < len(args) else None)

    if sys.platform == "win32":
        import ctypes

        with contextlib.suppress(Exception):
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ScreenshotBuffer.App")

    qapp = QApplication(args)
    qapp.setApplicationName("ScreenshotBuffer")
    qapp.setApplicationDisplayName(APP_NAME)
    qapp.setApplicationVersion(__version__)
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setWindowIcon(style.app_icon())

    if "--quit" in args:
        # Used by the installers to stop a running copy before replacing it.
        # Nothing running is fine too: either way no instance is left.
        _send_to_running_instance(b"quit")
        return 0

    store = SettingsStore()
    _setup_logging(store)

    # Single instance: a second launch just opens the running instance's settings.
    if _send_to_running_instance(b"show"):
        log.info("Already running; asked the existing instance to show its settings")
        return 0
    name = _server_name()
    QLocalServer.removeServer(name)
    server = QLocalServer()
    server.listen(name)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        log.warning("No system tray available; opening the settings window instead")

    log.info("Screenshot Buffer %s starting", __version__)
    app = App(qapp, store)

    def on_connection() -> None:
        sock = server.nextPendingConnection()
        if sock is None:
            return
        sock.disconnected.connect(sock.deleteLater)

        def on_message() -> None:
            command = bytes(sock.readAll().data()).strip()
            if command == b"quit":
                log.info("Quit requested by another process")
                app.quit()
            else:
                app.show_settings()

        sock.readyRead.connect(on_message)

    server.newConnection.connect(on_connection)
    app.start()
    if app.first_run or not QSystemTrayIcon.isSystemTrayAvailable():
        app.show_settings()
    return qapp.exec()
