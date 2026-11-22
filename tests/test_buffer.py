from screenshotbuffer.buffer import FrameBuffer, Pixels


def px(value: int, w: int = 4, h: int = 2) -> Pixels:
    return Pixels(w, h, w * 4, bytes([value]) * (w * h * 4))


def test_empty_buffer_has_no_frame():
    b = FrameBuffer(retention=5, memory_limit=10**9)
    assert b.frame_at(100.0) is None
    assert b.latest() is None


def test_frame_at_returns_frame_on_screen_at_that_time():
    b = FrameBuffer(retention=100, memory_limit=10**9)
    b.push(10.0, px(1))
    b.push(11.0, px(2))
    b.push(12.0, px(3))
    assert b.frame_at(10.5).pixels().data[0] == 1
    assert b.frame_at(11.0).pixels().data[0] == 2
    assert b.frame_at(11.99).pixels().data[0] == 2
    assert b.frame_at(50.0).pixels().data[0] == 3


def test_time_before_buffer_returns_oldest():
    b = FrameBuffer(retention=100, memory_limit=10**9)
    b.push(10.0, px(1))
    b.push(11.0, px(2))
    assert b.frame_at(0.0).pixels().data[0] == 1


def test_identical_frames_are_collapsed():
    b = FrameBuffer(retention=100, memory_limit=10**9)
    assert b.push(1.0, px(7)) is True
    assert b.push(2.0, px(7)) is False
    assert b.push(3.0, px(7)) is False
    assert b.stats(3.0).frames == 1
    assert b.push(4.0, px(8)) is True
    # The collapsed frame still covers 1.0..4.0
    assert b.frame_at(3.5).pixels().data[0] == 7


def test_size_change_is_a_new_frame():
    b = FrameBuffer(retention=100, memory_limit=10**9)
    b.push(1.0, px(7, 4, 2))
    assert b.push(2.0, px(7, 2, 4)) is True


def test_pruning_keeps_frame_covering_horizon():
    b = FrameBuffer(retention=3, memory_limit=10**9)
    for t in range(10):
        b.push(float(t), px(t))
    # Now = 9, horizon = 6: frame 6 must remain (it was on screen at t=6).
    assert b.frame_at(6.0).pixels().data[0] == 6
    assert b.frame_at(0.0).pixels().data[0] == 6
    assert b.stats(9.0).frames == 4


def test_static_frame_survives_beyond_retention():
    b = FrameBuffer(retention=3, memory_limit=10**9)
    b.push(0.0, px(1))
    for t in range(1, 100):
        b.push(float(t), px(1))
    # The slide that has been up the whole time is still what was on screen 3 s ago.
    assert b.frame_at(96.0).pixels().data[0] == 1


def test_memory_limit_drops_oldest_but_keeps_latest():
    big = 64 * 64
    b = FrameBuffer(retention=1000, memory_limit=1)
    for t in range(5):
        b.push(float(t), Pixels(big, 1, big * 4, bytes(range(256)) * (big * 4 // 256) + bytes([t])))
    assert b.stats(5.0).frames == 1
    assert b.latest().seq == 5


def test_thumbnail_factory_only_called_for_new_frames():
    calls = []

    def thumb(p: Pixels) -> Pixels:
        calls.append(p)
        return px(99, 1, 1)

    b = FrameBuffer(retention=100, memory_limit=10**9)
    b.push(1.0, px(1), thumb)
    b.push(2.0, px(1), thumb)
    b.push(3.0, px(2), thumb)
    assert len(calls) == 2
    t = b.latest().thumbnail()
    assert (t.width, t.height, t.data[0]) == (1, 1, 99)


def test_thumbnail_falls_back_to_full_frame():
    b = FrameBuffer(retention=100, memory_limit=10**9)
    b.push(1.0, px(5))
    t = b.latest().thumbnail()
    assert (t.width, t.height, t.data[0]) == (4, 2, 5)


def test_clear():
    b = FrameBuffer(retention=100, memory_limit=10**9)
    b.push(1.0, px(1))
    b.clear()
    assert b.frame_at(1.0) is None
    assert b.push(2.0, px(1)) is True
