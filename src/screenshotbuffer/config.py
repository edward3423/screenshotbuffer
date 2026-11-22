"""Persistent user settings.

Settings are stored as JSON in the per-user config directory. Loading is
tolerant: unknown keys are ignored and invalid values fall back to defaults,
so a hand-edited or older settings file never prevents the app from starting.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

IS_MAC = sys.platform == "darwin"

# Hotkey action identifiers, in the order they are presented to the user.
ACTION_COPY = "copy"
ACTION_TOGGLE_PREVIEW = "toggle_preview"
ACTION_SELECT_REGION = "select_region"
ACTION_TOGGLE_PAUSE = "toggle_pause"
ACTIONS = (ACTION_COPY, ACTION_TOGGLE_PREVIEW, ACTION_SELECT_REGION, ACTION_TOGGLE_PAUSE)

ACTION_LABELS = {
    ACTION_COPY: "Copy delayed frame to clipboard",
    ACTION_TOGGLE_PREVIEW: "Show / hide preview",
    ACTION_SELECT_REGION: "Select capture region",
    ACTION_TOGGLE_PAUSE: "Pause / resume recording",
}

# Key sequences use Qt's portable text format. On macOS Qt maps "Ctrl" to the
# Command key and "Meta" to the Control key. Macs have no Print Screen key, so
# the defaults there use Cmd+Shift+digit combinations that macOS leaves free.
if IS_MAC:
    DEFAULT_HOTKEYS = {
        ACTION_COPY: "Ctrl+Shift+9",
        ACTION_TOGGLE_PREVIEW: "Ctrl+Shift+0",
        ACTION_SELECT_REGION: "Ctrl+Shift+8",
        ACTION_TOGGLE_PAUSE: "",
    }
else:
    DEFAULT_HOTKEYS = {
        ACTION_COPY: "Ctrl+Print",
        ACTION_TOGGLE_PREVIEW: "Shift+Print",
        ACTION_SELECT_REGION: "Ctrl+Shift+Print",
        ACTION_TOGGLE_PAUSE: "",
    }

SOURCE_SCREEN = "screen"
SOURCE_REGION = "region"

NOTIFY_ALWAYS = "always"
NOTIFY_WHEN_HIDDEN = "when_preview_hidden"
NOTIFY_NEVER = "never"

FPS_MIN, FPS_MAX = 1.0, 60.0
DELAY_MIN, DELAY_MAX = 0.0, 120.0
MEMORY_MIN_MB, MEMORY_MAX_MB = 64, 32768
REGION_MIN_SIZE = 16


@dataclass
class Settings:
    fps: float = 10.0
    delay: float = 3.0
    source: str = SOURCE_SCREEN
    # QScreen.name() of the monitor recorded in full-screen mode; "" = primary.
    screen_name: str = ""
    # Capture region in Qt logical (device-independent) desktop coordinates.
    region: list[int] | None = None
    show_region_outline: bool = True
    memory_limit_mb: int = 1024
    preview_visible: bool = True
    preview_geometry: list[int] | None = None
    notify_on_copy: str = NOTIFY_WHEN_HIDDEN
    hotkeys: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_HOTKEYS))

    @classmethod
    def from_dict(cls, data: Any) -> Settings:
        s = cls()
        if not isinstance(data, dict):
            return s
        s.fps = _number(data.get("fps"), s.fps, FPS_MIN, FPS_MAX)
        s.delay = _number(data.get("delay"), s.delay, DELAY_MIN, DELAY_MAX)
        if data.get("source") in (SOURCE_SCREEN, SOURCE_REGION):
            s.source = data["source"]
        if isinstance(data.get("screen_name"), str):
            s.screen_name = data["screen_name"]
        s.region = _rect(data.get("region"), REGION_MIN_SIZE)
        if s.source == SOURCE_REGION and s.region is None:
            s.source = SOURCE_SCREEN
        if isinstance(data.get("show_region_outline"), bool):
            s.show_region_outline = data["show_region_outline"]
        s.memory_limit_mb = int(
            _number(data.get("memory_limit_mb"), s.memory_limit_mb, MEMORY_MIN_MB, MEMORY_MAX_MB)
        )
        if isinstance(data.get("preview_visible"), bool):
            s.preview_visible = data["preview_visible"]
        s.preview_geometry = _rect(data.get("preview_geometry"), 1)
        if data.get("notify_on_copy") in (NOTIFY_ALWAYS, NOTIFY_WHEN_HIDDEN, NOTIFY_NEVER):
            s.notify_on_copy = data["notify_on_copy"]
        hotkeys = data.get("hotkeys")
        if isinstance(hotkeys, dict):
            for action in ACTIONS:
                value = hotkeys.get(action)
                if isinstance(value, str):
                    s.hotkeys[action] = value
        return s

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def copy(self) -> Settings:
        return Settings.from_dict(self.to_dict())


def _number(value: Any, default: float, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    if value != value:  # NaN
        return default
    return float(min(hi, max(lo, value)))


def _rect(value: Any, min_size: int) -> list[int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
        return None
    x, y, w, h = (round(v) for v in value)
    if w < min_size or h < min_size:
        return None
    return [x, y, w, h]


def default_config_dir() -> Path:
    override = os.environ.get("SCREENSHOTBUFFER_CONFIG_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif IS_MAC:
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "ScreenshotBuffer"


class SettingsStore:
    """Loads and atomically saves :class:`Settings` as JSON."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or default_config_dir()
        self.path = self.directory / "settings.json"

    def load(self) -> Settings:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return Settings()
        except OSError:
            log.exception("Could not read %s; using defaults", self.path)
            return Settings()
        try:
            return Settings.from_dict(json.loads(text))
        except json.JSONDecodeError:
            log.warning("Settings file %s is corrupt; using defaults", self.path)
            return Settings()

    def save(self, settings: Settings) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(settings.to_dict(), indent=2, sort_keys=True)
        fd, tmp = tempfile.mkstemp(prefix=".settings-", suffix=".json", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
