"""On-screen region tools: the passive outline, the interactive editor and
the drag-to-select overlay."""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QScreen,
)
from PySide6.QtWidgets import QWidget

from .. import native
from ..config import REGION_MIN_SIZE
from ..geometry import Rect, rect_from_drag, resize_rect, snap_edges, snap_move
from . import style
from .preview import draw_pill, pill_size

HANDLE = 9  # handle square size
HANDLE_HIT = 8.0  # extra hit slop around handles
BAND = 9.0  # resize band just outside each edge
SNAP = 10.0
OUTLINE = 2.0
PILL_GAP = 8.0
PILL_FONT = 8.5
EDITOR_HINT = "Shift: keep ratio   Alt/Ctrl: from center   Arrows: nudge   Esc: cancel"
SELECTOR_HINT = "Drag to select the area to record    Shift: square    Alt/Ctrl: from center    Esc: cancel"
if native.IS_MAC:
    EDITOR_HINT = EDITOR_HINT.replace("Alt/Ctrl", "Option/Cmd")
    SELECTOR_HINT = SELECTOR_HINT.replace("Alt/Ctrl", "Option/Cmd")

_HANDLE_CURSORS = {
    "nw": Qt.CursorShape.SizeFDiagCursor,
    "se": Qt.CursorShape.SizeFDiagCursor,
    "ne": Qt.CursorShape.SizeBDiagCursor,
    "sw": Qt.CursorShape.SizeBDiagCursor,
    "n": Qt.CursorShape.SizeVerCursor,
    "s": Qt.CursorShape.SizeVerCursor,
    "e": Qt.CursorShape.SizeHorCursor,
    "w": Qt.CursorShape.SizeHorCursor,
}


def to_rect(r: QRect | QRectF) -> Rect:
    return Rect(r.x(), r.y(), r.width(), r.height())


def to_qrect(r: Rect) -> QRect:
    rr = r.rounded()
    return QRect(int(rr.x), int(rr.y), int(rr.w), int(rr.h))


def screen_snap_lines() -> tuple[list[float], list[float]]:
    xs: list[float] = []
    ys: list[float] = []
    for s in QGuiApplication.screens():
        for g in (s.geometry(), s.availableGeometry()):
            xs += [g.x(), g.x() + g.width()]
            ys += [g.y(), g.y() + g.height()]
    return xs, ys


def screen_at_rect(r: QRect) -> QScreen:
    return QGuiApplication.screenAt(r.center()) or QGuiApplication.primaryScreen()


def _modifiers(mods: Qt.KeyboardModifier) -> tuple[bool, bool]:
    """(keep_aspect, from_center). Shift = uniform scale; Alt/Option or Ctrl/Cmd = symmetric."""
    keep = bool(mods & Qt.KeyboardModifier.ShiftModifier)
    center = bool(mods & (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ControlModifier))
    return keep, center


def size_text(r: QRect, screen: QScreen) -> str:
    """Size of the region as it will be captured, in image pixels."""
    dpr = screen.devicePixelRatio()
    return f"{round(r.width() * dpr)} x {round(r.height() * dpr)} px"


class RegionOutline(QWidget):
    """Thin click-through border drawn just outside the captured region.

    It sits entirely outside the region, so it never shows up in captures even
    where the OS cannot exclude translucent windows from screen capture.
    """

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setWindowTitle("Screenshot Buffer region")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        native.prepare_floating_window(self)
        self._paused = False

    def set_region(self, r: QRect) -> None:
        m = int(OUTLINE) + 1
        self.setGeometry(r.adjusted(-m, -m, m, m))

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        self.update()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if native.IS_MAC:
            native.exclude_from_capture(self)
        native.float_above_everything(self)

    def paintEvent(self, _e: QPaintEvent) -> None:
        p = QPainter(self)
        color = QColor(style.WARNING if self._paused else style.ACCENT)
        color.setAlpha(230)
        pen = QPen(color, OUTLINE)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        if self._paused:
            pen.setDashPattern([3, 3])
        p.setPen(pen)
        half = OUTLINE / 2
        p.drawRect(QRectF(half, half, self.width() - OUTLINE, self.height() - OUTLINE))
        p.end()


class RegionEditor(QWidget):
    """Interactive move/resize frame around the capture region.

    Mouse: drag inside to move, drag a handle or edge to resize.
    Shift: keep aspect ratio (resize) / lock to one axis (move).
    Alt (Option) or Ctrl (Cmd): resize symmetrically around the center.
    Keyboard: arrows nudge (Shift = 10 px), Enter = done, Esc = cancel.
    """

    region_changed = Signal(QRect)
    finished = Signal(bool)  # accepted

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool,
        )
        self.setWindowTitle("Adjust capture region")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        native.prepare_floating_window(self)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._region = QRect(100, 100, 640, 360)
        self._original = QRect(self._region)
        # drag state: (kind, handle, start global pos, start rect)
        self._drag: tuple[str, str, QPointF, Rect] | None = None
        self._done_rect = QRectF()
        self._origin = QPoint()  # window top-left in global coordinates
        self._pills: list[tuple[QRectF, str, bool]] = []

    # ----- public API --------------------------------------------------
    def begin(self, region: QRect) -> None:
        self._original = QRect(region)
        self._region = QRect(region)
        self._drag = None
        self._relayout()
        native.bring_to_front(self)
        native.float_above_everything(self, native.LEVEL_POPUP)
        self.setFocus()

    def region(self) -> QRect:
        return QRect(self._region)

    def finish(self, accepted: bool) -> None:
        if not self.isVisible():
            return
        self._drag = None
        if not accepted and self._region != self._original:
            self._region = QRect(self._original)
            self.region_changed.emit(QRect(self._region))
        self.hide()
        self.finished.emit(accepted)

    # ----- layout ------------------------------------------------------
    def _relayout(self) -> None:
        r = QRectF(self._region)
        screen = screen_at_rect(self._region)
        sg = QRectF(screen.geometry())
        reach = HANDLE + 2 + HANDLE_HIT
        frame = r.adjusted(-reach, -reach, reach, reach)

        items = [
            (size_text(self._region, screen), True),
            ("Done", True),
            (EDITOR_HINT, False),
        ]
        sizes = [pill_size(t, bold=primary, point_size=PILL_FONT) for t, primary in items]
        widths = [sz.width() for sz in sizes]
        pill_h = max(sz.height() for sz in sizes)
        row_w = sum(widths) + PILL_GAP / 2 * (len(items) - 1)
        if row_w > max(r.width(), 360) + 2 * reach:
            items, widths = items[:2], widths[:2]
            row_w = sum(widths) + PILL_GAP / 2
        above_y = r.top() - reach - pill_h - 2
        below_y = r.bottom() + reach + 2
        if above_y >= sg.top():
            y = above_y
        elif below_y + pill_h <= sg.bottom():
            y = below_y
        else:
            y = r.top() + 8
        x = round(min(max(r.left(), sg.left() + 4), sg.right() - row_w - 4))
        y = round(y)
        global_pills: list[tuple[QRectF, str, bool]] = []
        for (text, primary), w in zip(items, widths, strict=True):
            global_pills.append((QRectF(x, y, w, pill_h), text, primary))
            x += w + round(PILL_GAP / 2)

        bounds = frame
        for rect, _, _ in global_pills:
            bounds = bounds.united(rect)
        win = bounds.adjusted(-2, -2, 2, 2).toAlignedRect()
        self._origin = win.topLeft()
        off = QPointF(-win.x(), -win.y())
        self._pills = [(rect.translated(off), text, primary) for rect, text, primary in global_pills]
        self._done_rect = self._pills[1][0] if len(self._pills) > 1 else QRectF()
        if self.geometry() != win:
            self.setGeometry(win)
        self.update()

    def _local_region(self) -> QRectF:
        return QRectF(self._region).translated(-self._origin.x(), -self._origin.y())

    def _handle_rects(self) -> dict[str, QRectF]:
        """Pixel-aligned handle squares centered on the outline."""
        r = self._local_region()
        half = OUTLINE / 2
        xs = {"w": r.left() - half, "c": r.left() + r.width() / 2, "e": r.right() + half}
        ys = {"n": r.top() - half, "c": r.top() + r.height() / 2, "s": r.bottom() + half}
        out = {}
        for name in ("nw", "n", "ne", "e", "se", "s", "sw", "w"):
            hx = "e" if "e" in name else "w" if "w" in name else "c"
            hy = "s" if "s" in name else "n" if "n" in name else "c"
            left = math.floor(xs[hx] - HANDLE / 2 + 0.5)
            top = math.floor(ys[hy] - HANDLE / 2 + 0.5)
            out[name] = QRectF(left, top, HANDLE, HANDLE)
        return out

    def _hit(self, pos: QPointF) -> tuple[str, str]:
        """('done'|'resize'|'move'|'none', handle)."""
        if self._done_rect.contains(pos):
            return "done", ""
        for name, rect in self._handle_rects().items():
            if rect.adjusted(-HANDLE_HIT / 2, -HANDLE_HIT / 2, HANDLE_HIT / 2, HANDLE_HIT / 2).contains(pos):
                return "resize", name
        r = self._local_region()
        if r.contains(pos):
            return "move", ""
        band = r.adjusted(-BAND, -BAND, BAND, BAND)
        if band.contains(pos):
            handle = ""
            if pos.y() < r.top():
                handle += "n"
            elif pos.y() > r.bottom():
                handle += "s"
            if pos.x() < r.left():
                handle += "w"
            elif pos.x() > r.right():
                handle += "e"
            if handle:
                return "resize", handle
        return "none", ""

    # ----- painting ----------------------------------------------------
    def paintEvent(self, _e: QPaintEvent) -> None:
        p = QPainter(self)
        r = self._local_region()
        # Nearly invisible fill so the interior and edge band receive the mouse
        # (fully transparent pixels are click-through).
        catch = QColor(0, 0, 0, 1)
        p.fillRect(r.adjusted(-BAND, -BAND, BAND, BAND), catch)

        pen = QPen(style.ACCENT, OUTLINE)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        p.setPen(pen)
        half = OUTLINE / 2
        p.drawRect(r.adjusted(-half, -half, half, half))

        for rect in self._handle_rects().values():
            p.fillRect(rect, style.ACCENT_DARK)
            p.fillRect(rect.adjusted(1, 1, -1, -1), QColor(255, 255, 255))

        for rect, text, primary in self._pills:
            if text == "Done":
                bg = style.ACCENT
                fg = QColor(255, 255, 255)
            else:
                bg = QColor(20, 21, 24, 225)
                fg = style.TEXT if primary else style.TEXT_DIM
            draw_pill(
                p, rect.topLeft(), text, bg=bg, fg=fg, bold=primary or text == "Done", point_size=PILL_FONT
            )
        p.end()

    # ----- interaction -------------------------------------------------
    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.RightButton:
            self.finish(True)
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        kind, handle = self._hit(e.position())
        if kind == "done":
            self.finish(True)
        elif kind in ("move", "resize"):
            self._drag = (kind, handle, e.globalPosition(), to_rect(self._region))
            self.grabMouse()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._drag is None:
            kind, handle = self._hit(e.position())
            if kind == "resize":
                self.setCursor(QCursor(_HANDLE_CURSORS[handle]))
            elif kind == "move":
                self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
            elif kind == "done":
                self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            else:
                self.unsetCursor()
            return
        self._update_drag(e.globalPosition(), e.modifiers())

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if self._drag is not None and e.button() == Qt.MouseButton.LeftButton:
            self._update_drag(e.globalPosition(), e.modifiers())
            self._drag = None
            self.releaseMouse()

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self._hit(e.position())[0] == "move":
            self.finish(True)

    def _update_drag(self, pos: QPointF, mods: Qt.KeyboardModifier) -> None:
        assert self._drag is not None
        kind, handle, start, rect = self._drag
        dx, dy = pos.x() - start.x(), pos.y() - start.y()
        keep, center = _modifiers(mods)
        xs, ys = screen_snap_lines()
        if kind == "move":
            if keep:  # lock to the dominant axis
                if abs(dx) >= abs(dy):
                    dy = 0
                else:
                    dx = 0
            new = rect.translated(dx, dy)
            if not center:
                new = snap_move(new, xs if dx else [], ys if dy else [], SNAP)
        else:
            new = resize_rect(
                rect, handle, dx, dy, keep_aspect=keep, from_center=center, min_size=REGION_MIN_SIZE
            )
            if not keep and not center:
                new = snap_edges(new, handle, xs, ys, SNAP)
                if new.w < REGION_MIN_SIZE or new.h < REGION_MIN_SIZE:
                    new = resize_rect(rect, handle, dx, dy, min_size=REGION_MIN_SIZE)
        self._set_region(to_qrect(new))

    def _set_region(self, r: QRect) -> None:
        if r != self._region:
            self._region = r
            self._relayout()
            self.region_changed.emit(QRect(r))

    def keyPressEvent(self, e: QKeyEvent) -> None:
        key = e.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.finish(True)
            return
        if key == Qt.Key.Key_Escape:
            self.finish(False)
            return
        step = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        moves = {
            Qt.Key.Key_Left: (-step, 0),
            Qt.Key.Key_Right: (step, 0),
            Qt.Key.Key_Up: (0, -step),
            Qt.Key.Key_Down: (0, step),
        }
        if key in moves:
            dx, dy = moves[key]
            self._set_region(self._region.translated(dx, dy))
            return
        self._refresh_modifiers()
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e: QKeyEvent) -> None:
        self._refresh_modifiers()
        super().keyReleaseEvent(e)

    def _refresh_modifiers(self) -> None:
        # Pressing or releasing Shift/Alt mid-drag takes effect immediately.
        if self._drag is not None:
            self._update_drag(QCursor.pos().toPointF(), QGuiApplication.queryKeyboardModifiers())

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if native.IS_MAC:
            native.exclude_from_capture(self)


class _SelectorScreen(QWidget):
    """Opaque full-screen overlay for one monitor showing a dimmed freeze-frame."""

    selected = Signal(QRect)
    cancelled = Signal()

    def __init__(self, screen: QScreen, snapshot: QPixmap) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool,
        )
        self.setWindowTitle("Select capture region")
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        native.prepare_floating_window(self)
        self.setScreen(screen)
        self.setGeometry(screen.geometry())
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._screen = screen
        self._snapshot = snapshot
        self._anchor: QPointF | None = None
        self._rect: QRectF | None = None
        self._cursor = QPointF(-1, -1)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        native.exclude_from_capture(self)
        native.float_above_everything(self, native.LEVEL_POPUP)

    def paintEvent(self, _e: QPaintEvent) -> None:
        p = QPainter(self)
        full = QRectF(self.rect())
        p.drawPixmap(full, self._snapshot, QRectF(self._snapshot.rect()))
        dim = QPainterPath()
        dim.addRect(full)
        if self._rect is not None:
            hole = QPainterPath()
            hole.addRect(self._rect)
            dim = dim.subtracted(hole)
        p.fillPath(dim, QColor(0, 0, 0, 130))

        if self._rect is not None:
            pen = QPen(style.ACCENT, OUTLINE)
            pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
            p.setPen(pen)
            p.drawRect(self._rect.adjusted(-1, -1, 1, 1))
            qr = self._rect.toRect()
            text = size_text(qr, self._screen)
            anchor = QPointF(self._rect.left(), self._rect.top() - 6)
            align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom
            if anchor.y() < 30:
                anchor = QPointF(self._rect.left(), self._rect.bottom() + 6)
                align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
            draw_pill(p, anchor, text, align=align, bold=True)
        elif self.rect().contains(self._cursor.toPoint()):
            # Crosshair guides help line the selection up with slide edges.
            guide = QColor(style.ACCENT)
            guide.setAlpha(150)
            p.setPen(QPen(guide, 1))
            p.drawLine(QPointF(self._cursor.x() + 0.5, 0), QPointF(self._cursor.x() + 0.5, self.height()))
            p.drawLine(QPointF(0, self._cursor.y() + 0.5), QPointF(self.width(), self._cursor.y() + 0.5))

        if self._anchor is None:
            draw_pill(
                p,
                QPointF(self.width() / 2, 24),
                SELECTOR_HINT,
                align=Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                bg=QColor(20, 21, 24, 235),
                point_size=10,
            )
        p.end()

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.RightButton:
            self.cancelled.emit()
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self._anchor = e.position()
            self._rect = None
            self.update()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        self._cursor = e.position()
        if self._anchor is not None:
            self._update_rect(e.position(), e.modifiers())
        self.update()

    def _update_rect(self, pos: QPointF, mods: Qt.KeyboardModifier) -> None:
        assert self._anchor is not None
        square, center = _modifiers(mods)
        r = rect_from_drag(
            self._anchor.x(), self._anchor.y(), pos.x(), pos.y(), square=square, from_center=center
        )
        bounded = QRectF(r.x, r.y, r.w, r.h).intersected(QRectF(self.rect()))
        self._rect = QRectF(bounded.toRect())

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if e.button() != Qt.MouseButton.LeftButton or self._anchor is None:
            return
        self._update_rect(e.position(), e.modifiers())
        self._anchor = None
        r = self._rect
        if r is None or r.width() < REGION_MIN_SIZE or r.height() < REGION_MIN_SIZE:
            self._rect = None
            self.update()
            return
        self.selected.emit(r.toRect().translated(self.geometry().topLeft()))

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            return
        if self._anchor is not None:
            self._update_rect(QPointF(self.mapFromGlobal(QCursor.pos())), e.modifiers())
            self.update()

    def keyReleaseEvent(self, e: QKeyEvent) -> None:
        if self._anchor is not None:
            self._update_rect(QPointF(self.mapFromGlobal(QCursor.pos())), e.modifiers())
            self.update()


class RegionSelector(QWidget):
    """Coordinates one overlay per monitor. Not shown itself."""

    selected = Signal(QRect)
    cancelled = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._overlays: list[_SelectorScreen] = []

    def active(self) -> bool:
        return bool(self._overlays)

    def start(self) -> None:
        if self._overlays:
            native.bring_to_front(self._overlays[0])
            return
        cursor_screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        for screen in QGuiApplication.screens():
            snapshot = screen.grabWindow(0)
            if snapshot.isNull():
                snapshot = QPixmap(QSize(1, 1))
                snapshot.fill(QColor(30, 30, 30))
            overlay = _SelectorScreen(screen, snapshot)
            overlay.selected.connect(self._on_selected)
            overlay.cancelled.connect(self._on_cancelled)
            self._overlays.append(overlay)
            overlay.show()
            if screen is cursor_screen:
                native.bring_to_front(overlay)
                overlay.setFocus()

    def stop(self) -> None:
        for o in self._overlays:
            o.close()
        self._overlays = []

    def _on_selected(self, r: QRect) -> None:
        self.stop()
        self.selected.emit(r)

    def _on_cancelled(self) -> None:
        self.stop()
        self.cancelled.emit()
