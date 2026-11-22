"""Pure rectangle math for the region editor and capture coordinate mapping.

Kept free of Qt so it can be unit tested exhaustively.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")


@dataclass(frozen=True, slots=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def left(self) -> float:
        return self.x

    @property
    def top(self) -> float:
        return self.y

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @classmethod
    def from_edges(cls, left: float, top: float, right: float, bottom: float) -> Rect:
        return cls(left, top, right - left, bottom - top)

    def translated(self, dx: float, dy: float) -> Rect:
        return Rect(self.x + dx, self.y + dy, self.w, self.h)

    def rounded(self) -> Rect:
        left, top = round(self.left), round(self.top)
        return Rect.from_edges(left, top, round(self.right), round(self.bottom))

    def contains_point(self, px: float, py: float) -> bool:
        return self.left <= px < self.right and self.top <= py < self.bottom

    def intersection_area(self, other: Rect) -> float:
        w = min(self.right, other.right) - max(self.left, other.left)
        h = min(self.bottom, other.bottom) - max(self.top, other.top)
        return max(0.0, w) * max(0.0, h)


def handle_axes(handle: str) -> tuple[int, int]:
    """Direction a handle moves its edges: -1 = left/top, 1 = right/bottom, 0 = none."""
    hx = 1 if "e" in handle else -1 if "w" in handle else 0
    hy = 1 if "s" in handle else -1 if "n" in handle else 0
    return hx, hy


def resize_rect(
    start: Rect,
    handle: str,
    dx: float,
    dy: float,
    *,
    keep_aspect: bool = False,
    from_center: bool = False,
    min_size: float = 1.0,
) -> Rect:
    """Resize ``start`` by dragging ``handle`` by (dx, dy).

    keep_aspect: preserve the width/height ratio (uniform scale). With a side
        handle the other dimension grows symmetrically about the center line.
    from_center: mirror the drag on the opposite side so the center stays put.

    The rectangle never shrinks below ``min_size`` and never flips over.
    """
    if handle not in HANDLES:
        raise ValueError(f"unknown handle {handle!r}")
    hx, hy = handle_axes(handle)
    k = 2.0 if from_center else 1.0
    w = start.w + hx * dx * k if hx else start.w
    h = start.h + hy * dy * k if hy else start.h

    if keep_aspect and start.w > 0 and start.h > 0:
        if hx and hy:
            scale = max(w / start.w, h / start.h)
        elif hx:
            scale = w / start.w
        else:
            scale = h / start.h
        scale = max(scale, min_size / start.w, min_size / start.h)
        w, h = start.w * scale, start.h * scale
    else:
        w, h = max(w, min_size), max(h, min_size)

    # Width changes without a horizontal handle only happen in aspect mode,
    # where they are centered; the same goes for height.
    if from_center or hx == 0:
        x = start.cx - w / 2
    elif hx > 0:
        x = start.left
    else:
        x = start.right - w
    if from_center or hy == 0:
        y = start.cy - h / 2
    elif hy > 0:
        y = start.top
    else:
        y = start.bottom - h
    return Rect(x, y, w, h)


def rect_from_drag(
    ax: float,
    ay: float,
    bx: float,
    by: float,
    *,
    square: bool = False,
    from_center: bool = False,
) -> Rect:
    """Rectangle drawn by dragging from anchor (ax, ay) to (bx, by)."""
    dx, dy = bx - ax, by - ay
    if square:
        side = max(abs(dx), abs(dy))
        dx = side if dx >= 0 else -side
        dy = side if dy >= 0 else -side
    if from_center:
        return Rect(ax - abs(dx), ay - abs(dy), 2 * abs(dx), 2 * abs(dy))
    return Rect(min(ax, ax + dx), min(ay, ay + dy), abs(dx), abs(dy))


def snap_value(value: float, targets: Iterable[float], threshold: float) -> float | None:
    """Nearest target within threshold of value, else None."""
    best: float | None = None
    best_dist = threshold
    for t in targets:
        d = abs(t - value)
        if d <= best_dist:
            best, best_dist = t, d
    return best


def snap_move(r: Rect, xs: Iterable[float], ys: Iterable[float], threshold: float) -> Rect:
    """Translate r so that its nearest edge lands on a nearby snap line."""
    xs, ys = list(xs), list(ys)
    dx = _best_offset((r.left, r.right), xs, threshold)
    dy = _best_offset((r.top, r.bottom), ys, threshold)
    return r.translated(dx, dy)


def snap_edges(r: Rect, handle: str, xs: Iterable[float], ys: Iterable[float], threshold: float) -> Rect:
    """Snap only the edges that ``handle`` moves."""
    hx, hy = handle_axes(handle)
    left, top, right, bottom = r.left, r.top, r.right, r.bottom
    xs, ys = list(xs), list(ys)
    if hx < 0:
        left = _snap_or(left, xs, threshold)
    elif hx > 0:
        right = _snap_or(right, xs, threshold)
    if hy < 0:
        top = _snap_or(top, ys, threshold)
    elif hy > 0:
        bottom = _snap_or(bottom, ys, threshold)
    return Rect.from_edges(left, top, right, bottom)


def _snap_or(value: float, targets: list[float], threshold: float) -> float:
    s = snap_value(value, targets, threshold)
    return value if s is None else s


def _best_offset(edges: tuple[float, ...], targets: list[float], threshold: float) -> float:
    best = 0.0
    best_dist = threshold + 1
    for e in edges:
        s = snap_value(e, targets, threshold)
        if s is not None and abs(s - e) < best_dist:
            best, best_dist = s - e, abs(s - e)
    return best


@dataclass(frozen=True, slots=True)
class ScreenInfo:
    """A monitor in Qt logical coordinates plus its device pixel ratio."""

    rect: Rect
    dpr: float


def screen_for_rect(r: Rect, screens: list[ScreenInfo]) -> ScreenInfo | None:
    """The screen with the largest overlap with r, else the one nearest its center."""
    if not screens:
        return None
    best = max(screens, key=lambda s: s.rect.intersection_area(r))
    if best.rect.intersection_area(r) > 0:
        return best

    def dist(s: ScreenInfo) -> float:
        return (s.rect.cx - r.cx) ** 2 + (s.rect.cy - r.cy) ** 2

    return min(screens, key=dist)


def logical_to_native(r: Rect, screen: ScreenInfo) -> Rect:
    """Map a logical rect to native pixels the way Qt 6 does on Windows.

    Qt keeps each screen's top-left corner unscaled and scales distances from
    it by the screen's device pixel ratio.
    """
    ox, oy = screen.rect.x, screen.rect.y
    s = screen.dpr
    return Rect(ox + (r.x - ox) * s, oy + (r.y - oy) * s, r.w * s, r.h * s).rounded()
