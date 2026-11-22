"""Time-indexed ring buffer of captured frames.

Each entry represents what was on screen from its ``timestamp`` until the
next entry's timestamp. Identical consecutive captures are collapsed into a
single entry, so a static slide costs one frame of memory no matter how long
it stays up. Pixel data is LZ4-compressed, which is fast enough to keep up
with full-screen capture and shrinks typical screen content several-fold.

The buffer is Qt-free and thread-safe: the capture thread pushes, the GUI
thread reads.
"""

from __future__ import annotations

import bisect
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

import lz4.block


@dataclass(frozen=True, slots=True)
class Pixels:
    """An uncompressed 32-bit BGRX image (Qt's ``QImage.Format_RGB32``)."""

    width: int
    height: int
    stride: int
    data: bytes | bytearray


@dataclass(frozen=True, slots=True)
class Frame:
    seq: int
    timestamp: float
    width: int
    height: int
    stride: int
    _data: bytes
    thumb_width: int
    thumb_height: int
    thumb_stride: int
    _thumb: bytes

    @property
    def nbytes(self) -> int:
        return len(self._data) + len(self._thumb)

    def pixels(self) -> Pixels:
        return Pixels(self.width, self.height, self.stride, lz4.block.decompress(self._data))

    def thumbnail(self) -> Pixels:
        if not self._thumb:
            return self.pixels()
        return Pixels(
            self.thumb_width, self.thumb_height, self.thumb_stride, lz4.block.decompress(self._thumb)
        )


@dataclass(frozen=True, slots=True)
class BufferStats:
    frames: int
    nbytes: int
    span: float


def _compress(data: bytes | bytearray) -> bytes:
    return lz4.block.compress(data, mode="fast", store_size=True)


class FrameBuffer:
    def __init__(self, retention: float, memory_limit: int) -> None:
        self._lock = threading.Lock()
        self._frames: deque[Frame] = deque()
        self._timestamps: deque[float] = deque()
        self._nbytes = 0
        self._seq = 0
        self._last_raw: Pixels | None = None
        self._retention = retention
        self._memory_limit = memory_limit

    def configure(self, *, retention: float | None = None, memory_limit: int | None = None) -> None:
        with self._lock:
            if retention is not None:
                self._retention = retention
            if memory_limit is not None:
                self._memory_limit = memory_limit
            self._enforce_memory_limit()

    def push(
        self,
        timestamp: float,
        image: Pixels,
        make_thumb: Callable[[Pixels], Pixels | None] | None = None,
    ) -> bool:
        """Add a capture. Returns False if it was identical to the previous one.

        ``make_thumb`` produces a downscaled copy for previews; it is only
        called for new frames.
        """
        with self._lock:
            last = self._last_raw
            if (
                last is not None
                and last.width == image.width
                and last.height == image.height
                and last.stride == image.stride
                and last.data == image.data
            ):
                self._prune(timestamp)
                return False
        # Compress outside the lock; lz4 releases the GIL so readers stay responsive.
        thumb = make_thumb(image) if make_thumb is not None else None
        data = _compress(image.data)
        tdata = _compress(thumb.data) if thumb is not None else b""
        with self._lock:
            self._seq += 1
            frame = Frame(
                seq=self._seq,
                timestamp=timestamp,
                width=image.width,
                height=image.height,
                stride=image.stride,
                _data=data,
                thumb_width=thumb.width if thumb else 0,
                thumb_height=thumb.height if thumb else 0,
                thumb_stride=thumb.stride if thumb else 0,
                _thumb=tdata,
            )
            self._frames.append(frame)
            self._timestamps.append(timestamp)
            self._nbytes += frame.nbytes
            self._last_raw = image
            self._prune(timestamp)
            self._enforce_memory_limit()
            return True

    def frame_at(self, t: float) -> Frame | None:
        """The frame that was on screen at time ``t``.

        If ``t`` predates the buffer (e.g. right after start-up), the oldest
        frame is returned, which is the closest thing available.
        """
        with self._lock:
            if not self._frames:
                return None
            i = bisect.bisect_right(self._timestamps, t) - 1
            return self._frames[max(i, 0)]

    def latest(self) -> Frame | None:
        with self._lock:
            return self._frames[-1] if self._frames else None

    def clear(self) -> None:
        with self._lock:
            self._frames.clear()
            self._timestamps.clear()
            self._nbytes = 0
            self._last_raw = None

    def stats(self, now: float) -> BufferStats:
        with self._lock:
            span = now - self._timestamps[0] if self._timestamps else 0.0
            return BufferStats(len(self._frames), self._nbytes, max(0.0, span))

    def _prune(self, now: float) -> None:
        # Frame 0 is still needed while frame 1 starts after the retention horizon.
        horizon = now - self._retention
        while len(self._timestamps) >= 2 and self._timestamps[1] <= horizon:
            self._pop_oldest()

    def _enforce_memory_limit(self) -> None:
        while self._nbytes > self._memory_limit and len(self._frames) > 1:
            self._pop_oldest()

    def _pop_oldest(self) -> None:
        frame = self._frames.popleft()
        self._timestamps.popleft()
        self._nbytes -= frame.nbytes
