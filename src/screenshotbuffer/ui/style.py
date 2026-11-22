"""Shared colors, fonts and the programmatically drawn app icon."""

from __future__ import annotations

import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap

ACCENT = QColor(0x2F, 0x8C, 0xFF)
ACCENT_DARK = QColor(0x1F, 0x6F, 0xD6)
SUCCESS = QColor(0x34, 0xC7, 0x59)
WARNING = QColor(0xFF, 0x9F, 0x0A)
PANEL_BG = QColor(0x16, 0x17, 0x1A)
PANEL_BORDER = QColor(0x3A, 0x3C, 0x42)
TEXT = QColor(0xF2, 0xF3, 0xF5)
TEXT_DIM = QColor(0xA9, 0xAD, 0xB5)
PILL_BG = QColor(0, 0, 0, 170)


def ui_font(point_size: float, bold: bool = False) -> QFont:
    """Font sized in Windows points (96 dpi), so text looks the same size on macOS (72 dpi)."""
    f = QFont()
    f.setPointSizeF(point_size * (96 / 72) if sys.platform == "darwin" else point_size)
    f.setBold(bold)
    return f


def _draw_icon(size: int, color: QColor, paused: bool) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 32.0
    pen = QPen(color, 2.6 * s)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    # Monitor outline
    p.drawRoundedRect(QRectF(3 * s, 5 * s, 26 * s, 19 * s), 3 * s, 3 * s)
    p.drawLine(QPointF(11 * s, 28.5 * s), QPointF(21 * s, 28.5 * s))
    center = QPointF(16 * s, 14.5 * s)
    if paused:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(QRectF(12 * s, 10 * s, 3 * s, 9 * s), 0.8 * s, 0.8 * s)
        p.drawRoundedRect(QRectF(17 * s, 10 * s, 3 * s, 9 * s), 0.8 * s, 0.8 * s)
    else:
        # "Rewind in time" arrow: open circle with an arrowhead.
        r = 5.2 * s
        arc = QPainterPath()
        arc.arcMoveTo(QRectF(center.x() - r, center.y() - r, 2 * r, 2 * r), 120)
        arc.arcTo(QRectF(center.x() - r, center.y() - r, 2 * r, 2 * r), 120, 290)
        p.setPen(QPen(color, 2.2 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawPath(arc)
        start = arc.pointAtPercent(0)
        head = QPainterPath()
        head.moveTo(start + QPointF(-3.2 * s, -0.6 * s))
        head.lineTo(start + QPointF(0.9 * s, -1.4 * s))
        head.lineTo(start + QPointF(-0.6 * s, 2.6 * s))
        head.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawPath(head)
        # Clock hand
        p.setPen(QPen(color, 1.8 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(center, center + QPointF(0, -2.6 * s))
        p.drawLine(center, center + QPointF(2.0 * s, 1.2 * s))
    p.end()
    return pm


def app_icon(paused: bool = False, tray: bool = False) -> QIcon:
    icon = QIcon()
    if tray and sys.platform == "darwin":
        # Template image: macOS tints it to match the menu bar.
        for size in (16, 32, 44):
            icon.addPixmap(_draw_icon(size, QColor(0, 0, 0), paused))
        icon.setIsMask(True)
        return icon
    color = QColor(0x9A, 0x9E, 0xA6) if paused else ACCENT
    for size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        icon.addPixmap(_draw_icon(size, color, paused))
    return icon
