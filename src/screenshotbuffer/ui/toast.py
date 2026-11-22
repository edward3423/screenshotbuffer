"""Small transient notification shown in the top-right corner."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QGuiApplication, QImage, QPainter, QPaintEvent, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from .. import native
from . import style

PAD = 12
THUMB_W = 96
MARGIN = 16


class Toast(QWidget):
    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput,
        )
        self.setWindowTitle("Screenshot Buffer notification")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        native.prepare_floating_window(self)
        self._title = ""
        self._body = ""
        self._accent = style.SUCCESS
        self._thumb: QPixmap | None = None
        self._timer = QTimer(self, singleShot=True)
        self._timer.timeout.connect(self.hide)

    def show_message(
        self,
        title: str,
        body: str = "",
        *,
        thumb: QImage | None = None,
        accent: QColor = style.SUCCESS,
        ms: int = 1800,
    ) -> None:
        self._title, self._body, self._accent = title, body, accent
        dpr = self.devicePixelRatioF()
        self._thumb = None
        if thumb is not None and not thumb.isNull():
            scaled = thumb.scaledToWidth(round(THUMB_W * dpr), Qt.TransformationMode.SmoothTransformation)
            self._thumb = QPixmap.fromImage(scaled)
            self._thumb.setDevicePixelRatio(dpr)

        title_fm = QFontMetrics(style.ui_font(10, True))
        body_fm = QFontMetrics(style.ui_font(9))
        text_w = max(title_fm.horizontalAdvance(title), body_fm.horizontalAdvance(body) if body else 0)
        text_h = title_fm.height() + (body_fm.height() + 2 if body else 0)
        thumb_size = self._thumb.deviceIndependentSize().toSize() if self._thumb else QSize(0, 0)
        w = PAD + 4 + (thumb_size.width() + PAD if self._thumb else 0) + text_w + PAD
        h = max(text_h, thumb_size.height()) + 2 * PAD
        self.resize(w, h)

        # Top-left corner of the primary screen, away from the preview (top right).
        area = QGuiApplication.primaryScreen().availableGeometry()
        self.move(QPoint(area.left() + MARGIN, area.top() + MARGIN))
        self.show()
        self.raise_()
        self.update()
        self._timer.start(ms)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        native.exclude_from_capture(self)
        native.float_above_everything(self, native.LEVEL_POPUP)

    def paintEvent(self, _e: QPaintEvent) -> None:
        p = QPainter(self)
        r = self.rect()
        p.fillRect(r, style.PANEL_BG)
        p.fillRect(QRect(0, 0, 4, r.height()), self._accent)
        x = PAD + 4
        if self._thumb is not None:
            size = self._thumb.deviceIndependentSize()
            y = (r.height() - size.height()) / 2
            p.drawPixmap(QRectF(x, y, size.width(), size.height()).topLeft(), self._thumb)
            p.setPen(QPen(style.PANEL_BORDER, 1))
            p.drawRect(QRectF(x - 0.5, y - 0.5, size.width() + 1, size.height() + 1))
            x += round(size.width()) + PAD
        title_font = style.ui_font(10, True)
        body_font = style.ui_font(9)
        title_h = QFontMetrics(title_font).height()
        body_h = QFontMetrics(body_font).height() + 2 if self._body else 0
        y = (r.height() - title_h - body_h) / 2
        p.setFont(title_font)
        p.setPen(style.TEXT)
        p.drawText(
            QRectF(x, y, r.width() - x - PAD, title_h),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._title,
        )
        if self._body:
            p.setFont(body_font)
            p.setPen(style.TEXT_DIM)
            p.drawText(
                QRectF(x, y + title_h + 2, r.width() - x - PAD, body_h - 2),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                self._body,
            )
        p.setPen(QPen(style.PANEL_BORDER, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(0.5, 0.5, r.width() - 1, r.height() - 1))
        p.end()
