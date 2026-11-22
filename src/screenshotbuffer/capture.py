"""Background screen capture into a :class:`FrameBuffer`."""

from __future__ import annotations

import logging
import sys
import threading
import time
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from .buffer import FrameBuffer, Pixels

log = logging.getLogger(__name__)

THUMB_MAX = 800  # longest side of the preview thumbnail, in pixels


@dataclass(frozen=True, slots=True)
class CaptureTarget:
    """Rectangle to grab, in the units the platform capture API expects.

    Windows: native (physical) desktop pixels. macOS: points.
    """

    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class CaptureStats:
    fps: float
    grab_ms: float
    error: str | None


def _configure_mss() -> None:
    if sys.platform == "win32":
        import mss.windows.gdi as gdi

        # CAPTUREBLT makes the mouse cursor flicker on every grab. With DWM
        # composition (always on since Windows 8) it is not needed to include
        # layered windows.
        gdi.CAPTUREBLT = 0
    elif sys.platform == "darwin":
        import mss.darwin as darwin

        # Capture at native Retina resolution instead of 1x points.
        darwin.IMAGE_OPTIONS = darwin.kCGWindowImageBoundsIgnoreFraming | (
            darwin.kCGWindowImageShouldBeOpaque
        )


def make_thumbnail(image: Pixels) -> Pixels | None:
    if max(image.width, image.height) <= THUMB_MAX:
        return None
    src = QImage(image.data, image.width, image.height, image.stride, QImage.Format.Format_RGB32)
    thumb = src.scaled(
        THUMB_MAX,
        THUMB_MAX,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    ).convertToFormat(QImage.Format.Format_RGB32)
    return Pixels(thumb.width(), thumb.height(), thumb.bytesPerLine(), bytes(thumb.constBits()))


class CaptureWorker:
    def __init__(self, buffer: FrameBuffer) -> None:
        self.buffer = buffer
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = False
        self._paused = False
        self._fps = 10.0
        self._target: CaptureTarget | None = None
        self._stats = CaptureStats(0.0, 0.0, None)
        self._thread = threading.Thread(target=self._run, name="capture", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stop = True
        self._wake.set()
        self._thread.join(timeout=3)

    def configure(
        self,
        *,
        fps: float | None = None,
        target: CaptureTarget | None = None,
        paused: bool | None = None,
    ) -> None:
        with self._lock:
            if fps is not None:
                self._fps = max(0.1, fps)
            if target is not None:
                self._target = target
            if paused is not None:
                self._paused = paused
        self._wake.set()

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    def stats(self) -> CaptureStats:
        with self._lock:
            return self._stats

    def _run(self) -> None:
        import mss

        _configure_mss()
        # MSS must be created on the thread that uses it.
        with mss.MSS() as sct:
            tick: float | None = None  # scheduled time of the previous grab
            prev_start: float | None = None
            fps_avg = 0.0
            errors = 0
            while True:
                with self._lock:
                    if self._stop:
                        return
                    paused, target, interval = self._paused, self._target, 1.0 / self._fps
                if paused or target is None:
                    self._set_stats(0.0, 0.0, None)
                    tick = prev_start = None
                    fps_avg = 0.0
                    self._wake.wait()
                    self._wake.clear()
                    continue

                # Settings changes wake us early; re-read them, then keep waiting
                # until the next tick of the (possibly new) frame rate.
                if tick is not None:
                    due = tick + max(interval, min(2.0, 0.1 * errors))
                    now = time.monotonic()
                    if now < due:
                        if self._wake.wait(due - now):
                            self._wake.clear()
                        continue
                    # Stay on the fixed schedule unless we fell more than a tick behind.
                    tick = due if now - due < interval else now
                else:
                    tick = time.monotonic()

                start = time.monotonic()
                error = None
                grab_ms = 0.0
                try:
                    shot = sct.grab(
                        {
                            "left": target.left,
                            "top": target.top,
                            "width": target.width,
                            "height": target.height,
                        }
                    )
                    grab_ms = (time.monotonic() - start) * 1000
                    image = Pixels(shot.width, shot.height, shot.width * 4, shot.raw)
                    self.buffer.push(start, image, make_thumbnail)
                    errors = 0
                except Exception as e:  # e.g. the desktop is locked or a monitor vanished
                    errors += 1
                    error = str(e) or type(e).__name__
                    if errors == 1:
                        log.warning("Screen capture failed: %s", error)

                if prev_start is not None:
                    inst = 1.0 / max(1e-6, start - prev_start)
                    fps_avg = inst if fps_avg == 0 else fps_avg * 0.8 + inst * 0.2
                prev_start = start
                self._set_stats(fps_avg, grab_ms, error)

    def _set_stats(self, fps: float, grab_ms: float, error: str | None) -> None:
        with self._lock:
            self._stats = CaptureStats(fps, grab_ms, error)
