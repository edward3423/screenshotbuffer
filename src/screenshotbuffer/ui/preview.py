"""Always-on-top preview of the frame that a copy would grab right now."""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, QSizeF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QContextMenuEvent,
    QCursor,
    QFontMetricsF,
    QImage,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import QMenu, QWidget

from .. import native
from . import style

EDGE = 7  # px from the border that act as resize grips
MIN_SIZE = QSize(180, 110)

_CURSORS = {
    Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor,
    Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
    Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor,
    Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
    Qt.Edge.TopEdge | Qt.Edge.LeftEdge: Qt.CursorShape.SizeFDiagCursor,
    Qt.Edge.BottomEdge | Qt.Edge.RightEdge: Qt.CursorShape.SizeFDiagCursor,
    Qt.Edge.TopEdge | Qt.Edge.RightEdge: Qt.CursorShape.SizeBDiagCursor,
    Qt.Edge.BottomEdge | Qt.Edge.LeftEdge: Qt.CursorShape.SizeBDiagCursor,
}


def pill_size(text: str, *, bold: bool = False, point_size: float = 8.5) -> QSizeF:
    fm = QFontMetricsF(style.ui_font(point_size, bold))
    return QSizeF(math.ceil(fm.horizontalAdvance(text)) + 14, math.ceil(fm.height()) + 6)


def draw_pill(
    p: QPainter,
    anchor: QPointF,
    text: str,
    *,
    align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
    bg: QColor = style.PILL_BG,
    fg: QColor = style.TEXT,
    bold: bool = False,
    point_size: float = 8.5,
) -> QRectF:
    p.save()
    p.setFont(style.ui_font(point_size, bold))
    size = pill_size(text, bold=bold, point_size=point_size)
    w, h = size.width(), size.height()
    x, y = anchor.x(), anchor.y()
    if align & Qt.AlignmentFlag.AlignRight:
        x -= w
    elif align & Qt.AlignmentFlag.AlignHCenter:
        x -= w / 2
    if align & Qt.AlignmentFlag.AlignBottom:
        y -= h
    rect = QRectF(round(x), round(y), w, h)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(bg)
    p.drawRoundedRect(rect, h / 2, h / 2)
    p.setPen(fg)
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
    p.restore()
    return rect


class PreviewWindow(QWidget):
    delay_step_requested = Signal(float)
    copy_requested = Signal()
    hide_requested = Signal()
    pause_toggle_requested = Signal()
    settings_requested = Signal()
    geometry_committed = Signal(QRect)

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setWindowTitle("Screenshot Buffer preview")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        native.prepare_floating_window(self)
        self.setMinimumSize(MIN_SIZE)
        self.setMouseTracking(True)

        self._image: QImage | None = None
        self._image_key: object = None
        self._scaled: QPixmap | None = None
        self._scaled_for: tuple[object, QSize, float] | None = None
        self._delay = 3.0
        self._paused = False
        self._message: str | None = None
        self._detail: str | None = None
        self._hover = False
        self.excluded_from_capture = False

        self._flash_level = 0.0
        self._flash_timer = QTimer(self, interval=16)
        self._flash_timer.timeout.connect(self._fade_flash)

        self._commit_timer = QTimer(self, singleShot=True, interval=400)
        self._commit_timer.timeout.connect(lambda: self.geometry_committed.emit(self.geometry()))

        self._drag: tuple[str, QPoint, QRect, Qt.Edge] | None = None

    # ----- state -------------------------------------------------------
    def frame_key(self) -> object:
        return self._image_key

    def set_frame(self, image: QImage | None, key: object) -> None:
        if key == self._image_key and image is not None:
            return
        self._image = image
        self._image_key = key
        self.update()

    def set_delay(self, delay: float) -> None:
        if delay != self._delay:
            self._delay = delay
            self.update()

    def set_paused(self, paused: bool) -> None:
        if paused != self._paused:
            self._paused = paused
            self.update()

    def set_message(self, message: str | None, detail: str | None = None) -> None:
        if (message, detail) != (self._message, self._detail):
            self._message, self._detail = message, detail
            self.update()

    def flash(self) -> None:
        self._flash_level = 1.0
        self._flash_timer.start()
        self.update()

    def _fade_flash(self) -> None:
        self._flash_level = max(0.0, self._flash_level - 0.035)
        if self._flash_level == 0.0:
            self._flash_timer.stop()
        self.update()

    # ----- painting ----------------------------------------------------
    def paintEvent(self, _e: QPaintEvent) -> None:
        p = QPainter(self)
        r = self.rect()
        p.fillRect(r, style.PANEL_BG)
        inner = r.adjusted(1, 1, -1, -1)

        if self._image is not None and not self._image.isNull():
            pm = self._scaled_pixmap(inner.size())
            size = pm.deviceIndependentSize()
            x = inner.x() + (inner.width() - size.width()) / 2
            y = inner.y() + (inner.height() - size.height()) / 2
            p.drawPixmap(QPointF(x, y), pm)

        if self._message:
            p.save()
            p.setPen(style.TEXT_DIM)
            p.setFont(style.ui_font(9.5))
            text = self._message + (f"\n{self._detail}" if self._detail else "")
            p.drawText(
                inner.adjusted(12, 12, -12, -12),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                text,
            )
            p.restore()

        label = f"{self._delay:.1f} s ago"
        draw_pill(p, QPointF(8, 8), label, bold=True)
        if self._paused:
            draw_pill(
                p,
                QPointF(r.width() - 8, 8),
                "PAUSED",
                align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
                bg=style.WARNING,
                fg=QColor(0, 0, 0),
                bold=True,
            )
        elif self._flash_level > 0:
            c = QColor(style.SUCCESS)
            draw_pill(
                p,
                QPointF(r.width() - 8, 8),
                "Copied",
                align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
                bg=c,
                fg=QColor(0, 0, 0),
                bold=True,
            )
        if self._hover and r.height() >= 140:
            draw_pill(
                p,
                QPointF(r.width() / 2, r.height() - 8),
                "Scroll: change delay   Double-click: copy",
                align=Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                point_size=8,
            )

        # Border; flashes green after a copy.
        border = QColor(style.PANEL_BORDER)
        width = 1.0
        if self._flash_level > 0:
            a = self._flash_level
            g = style.SUCCESS
            border = QColor(
                round(border.red() + (g.red() - border.red()) * a),
                round(border.green() + (g.green() - border.green()) * a),
                round(border.blue() + (g.blue() - border.blue()) * a),
            )
            width = 1.0 + 2.0 * a
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        pen = QPen(border, width)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        half = width / 2
        p.drawRect(QRectF(half, half, r.width() - width, r.height() - width))
        p.end()

    def _scaled_pixmap(self, target: QSize) -> QPixmap:
        assert self._image is not None
        dpr = self.devicePixelRatioF()
        cache_key = (self._image_key, target, dpr)
        if self._scaled is None or self._scaled_for != cache_key:
            px = self._image.scaled(
                round(target.width() * dpr),
                round(target.height() * dpr),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            pm = QPixmap.fromImage(px)
            pm.setDevicePixelRatio(dpr)
            self._scaled = pm
            self._scaled_for = cache_key
        return self._scaled

    # ----- window behaviour --------------------------------------------
    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.excluded_from_capture = native.exclude_from_capture(self)
        native.float_above_everything(self)

    def moveEvent(self, e) -> None:
        super().moveEvent(e)
        if self.isVisible():
            self._commit_timer.start()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if self.isVisible():
            self._commit_timer.start()

    def enterEvent(self, _e) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, _e) -> None:
        self._hover = False
        self.unsetCursor()
        self.update()

    def _edges_at(self, pos: QPoint) -> Qt.Edge:
        edges = Qt.Edge(0)
        if pos.x() < EDGE:
            edges |= Qt.Edge.LeftEdge
        elif pos.x() >= self.width() - EDGE:
            edges |= Qt.Edge.RightEdge
        if pos.y() < EDGE:
            edges |= Qt.Edge.TopEdge
        elif pos.y() >= self.height() - EDGE:
            edges |= Qt.Edge.BottomEdge
        return edges

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            return
        edges = self._edges_at(e.position().toPoint())
        handle = self.windowHandle()
        if edges:
            if handle is None or not handle.startSystemResize(edges):
                self._drag = ("resize", e.globalPosition().toPoint(), self.geometry(), edges)
        elif handle is None or not handle.startSystemMove():
            self._drag = ("move", e.globalPosition().toPoint(), self.geometry(), Qt.Edge(0))

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._drag is None:
            edges = self._edges_at(e.position().toPoint())
            cursor = _CURSORS.get(edges)
            if cursor is None:
                self.unsetCursor()
            else:
                self.setCursor(QCursor(cursor))
            return
        kind, start, geo, edges = self._drag
        delta = e.globalPosition().toPoint() - start
        if kind == "move":
            self.move(geo.topLeft() + delta)
            return
        g = QRect(geo)
        if edges & Qt.Edge.LeftEdge:
            g.setLeft(min(geo.left() + delta.x(), geo.right() - MIN_SIZE.width()))
        if edges & Qt.Edge.RightEdge:
            g.setRight(max(geo.right() + delta.x(), geo.left() + MIN_SIZE.width()))
        if edges & Qt.Edge.TopEdge:
            g.setTop(min(geo.top() + delta.y(), geo.bottom() - MIN_SIZE.height()))
        if edges & Qt.Edge.BottomEdge:
            g.setBottom(max(geo.bottom() + delta.y(), geo.top() + MIN_SIZE.height()))
        self.setGeometry(g)

    def mouseReleaseEvent(self, _e: QMouseEvent) -> None:
        self._drag = None

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.copy_requested.emit()

    def wheelEvent(self, e: QWheelEvent) -> None:
        d = e.angleDelta()
        steps = (d.y() or d.x()) / 120
        if steps:
            # Shift (which some platforms turn into horizontal scrolling) = fine steps.
            fine = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.delay_step_requested.emit(steps * (0.1 if fine else 0.5))
        e.accept()

    def contextMenuEvent(self, e: QContextMenuEvent) -> None:
        menu = QMenu(self)
        menu.addAction("Copy this frame", self.copy_requested.emit)
        menu.addAction(
            "Resume recording" if self._paused else "Pause recording", self.pause_toggle_requested.emit
        )
        menu.addSeparator()
        menu.addAction("Hide preview", self.hide_requested.emit)
        settings = QAction("Settings...", menu)
        settings.triggered.connect(self.settings_requested.emit)
        menu.addAction(settings)
        menu.exec(e.globalPos())
