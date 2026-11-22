import pytest

from screenshotbuffer.geometry import (
    Rect,
    ScreenInfo,
    logical_to_native,
    rect_from_drag,
    resize_rect,
    screen_for_rect,
    snap_edges,
    snap_move,
)

R = Rect(100, 100, 200, 100)  # aspect 2:1, center (200, 150)


def approx(r: Rect) -> tuple[float, ...]:
    return tuple(round(v, 6) for v in (r.x, r.y, r.w, r.h))


@pytest.mark.parametrize(
    ("handle", "dx", "dy", "expected"),
    [
        ("e", 50, 999, (100, 100, 250, 100)),
        ("w", -50, 999, (50, 100, 250, 100)),
        ("s", 999, 30, (100, 100, 200, 130)),
        ("n", 999, -30, (100, 70, 200, 130)),
        ("se", 10, 20, (100, 100, 210, 120)),
        ("nw", -10, -20, (90, 80, 210, 120)),
        ("ne", 10, -20, (100, 80, 210, 120)),
        ("sw", -10, 20, (90, 100, 210, 120)),
    ],
)
def test_plain_resize_moves_only_dragged_edges(handle, dx, dy, expected):
    assert approx(resize_rect(R, handle, dx, dy)) == expected


def test_from_center_mirrors_drag():
    assert approx(resize_rect(R, "e", 50, 0, from_center=True)) == (50, 100, 300, 100)
    assert approx(resize_rect(R, "se", 10, 10, from_center=True)) == (90, 90, 220, 120)
    r = resize_rect(R, "nw", -25, 5, from_center=True)
    assert (r.cx, r.cy) == (R.cx, R.cy)


def test_keep_aspect_corner_uses_larger_change():
    r = resize_rect(R, "se", 100, 10, keep_aspect=True)
    assert approx(r) == (100, 100, 300, 150)  # anchored at top-left
    r = resize_rect(R, "nw", -10, -100, keep_aspect=True)
    assert approx(r) == (-100, 0, 400, 200)  # anchored at bottom-right
    assert (r.right, r.bottom) == (R.right, R.bottom)


def test_keep_aspect_side_handle_grows_other_axis_symmetrically():
    r = resize_rect(R, "e", 100, 0, keep_aspect=True)
    assert approx(r) == (100, 75, 300, 150)
    assert r.cy == R.cy
    r = resize_rect(R, "n", 0, -100, keep_aspect=True)
    assert approx(r) == (0, 0, 400, 200)
    assert r.cx == R.cx and r.bottom == R.bottom


def test_keep_aspect_and_from_center_combined():
    r = resize_rect(R, "se", 50, 0, keep_aspect=True, from_center=True)
    assert (r.cx, r.cy) == (R.cx, R.cy)
    assert r.w / r.h == pytest.approx(2.0)
    assert r.w == 300


def test_never_flips_and_respects_min_size():
    r = resize_rect(R, "e", -500, 0, min_size=16)
    assert approx(r) == (100, 100, 16, 100)
    r = resize_rect(R, "w", 500, 0, min_size=16)
    assert approx(r) == (284, 100, 16, 100)  # right edge stays put
    r = resize_rect(R, "se", -500, -500, keep_aspect=True, min_size=16)
    assert r.h == 16 and r.w == 32


def test_unknown_handle():
    with pytest.raises(ValueError):
        resize_rect(R, "x", 0, 0)


def test_rect_from_drag():
    assert approx(rect_from_drag(10, 10, 40, 30)) == (10, 10, 30, 20)
    assert approx(rect_from_drag(40, 30, 10, 10)) == (10, 10, 30, 20)
    assert approx(rect_from_drag(10, 10, 40, 20, square=True)) == (10, 10, 30, 30)
    assert approx(rect_from_drag(40, 40, 10, 30, square=True)) == (10, 10, 30, 30)
    assert approx(rect_from_drag(50, 50, 60, 55, from_center=True)) == (40, 45, 20, 10)


def test_snapping():
    r = Rect(5, 300, 100, 100)
    assert approx(snap_move(r, [0, 1920], [0, 1080], 10)) == (0, 300, 100, 100)
    r = Rect(1815, 300, 100, 100)  # right edge at 1915
    assert approx(snap_move(r, [0, 1920], [], 10)) == (1820, 300, 100, 100)
    r = Rect(500, 500, 100, 100)
    assert approx(snap_move(r, [0, 1920], [0, 1080], 10)) == (500, 500, 100, 100)
    r = Rect(4, 4, 1910, 100)
    snapped = snap_edges(r, "se", [0, 1920], [0, 1080], 10)
    assert approx(snapped) == (4, 4, 1916, 100)  # only the dragged right edge snaps


def test_logical_to_native_matches_qt_scaling():
    primary = ScreenInfo(Rect(0, 0, 1280, 720), 1.5)  # 1920x1080 at 150 %
    assert approx(logical_to_native(Rect(100, 100, 200, 100), primary)) == (150, 150, 300, 150)
    # A second monitor keeps its native origin unscaled.
    second = ScreenInfo(Rect(1920, 0, 1280, 720), 2.0)
    assert approx(logical_to_native(Rect(2020, 10, 100, 50), second)) == (2120, 20, 200, 100)


def test_screen_for_rect():
    a = ScreenInfo(Rect(0, 0, 1920, 1080), 1.0)
    b = ScreenInfo(Rect(1920, 0, 1920, 1080), 1.0)
    assert screen_for_rect(Rect(1800, 0, 400, 100), [a, b]) is b
    assert screen_for_rect(Rect(100, 100, 10, 10), [a, b]) is a
    assert screen_for_rect(Rect(5000, 0, 10, 10), [a, b]) is b
    assert screen_for_rect(Rect(0, 0, 1, 1), []) is None
