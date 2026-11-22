import json

from screenshotbuffer import config
from screenshotbuffer.config import Settings, SettingsStore


def test_defaults_roundtrip(tmp_path):
    store = SettingsStore(tmp_path)
    assert store.load() == Settings()
    s = Settings(fps=24, delay=1.5, source="region", region=[1, 2, 300, 200])
    store.save(s)
    assert store.load() == s


def test_invalid_values_fall_back_to_defaults():
    s = Settings.from_dict(
        {
            "fps": "fast",
            "delay": float("nan"),
            "source": "nope",
            "region": [1, 2, 3],
            "memory_limit_mb": True,
            "notify_on_copy": 5,
            "hotkeys": {"copy": 7, "toggle_preview": "Alt+P", "bogus": "X"},
        }
    )
    d = Settings()
    assert s.fps == d.fps and s.delay == d.delay and s.source == d.source
    assert s.region is None and s.memory_limit_mb == d.memory_limit_mb
    assert s.hotkeys["copy"] == config.DEFAULT_HOTKEYS["copy"]
    assert s.hotkeys["toggle_preview"] == "Alt+P"
    assert "bogus" not in s.hotkeys


def test_values_are_clamped():
    s = Settings.from_dict({"fps": 1000, "delay": -4, "memory_limit_mb": 1})
    assert s.fps == config.FPS_MAX
    assert s.delay == config.DELAY_MIN
    assert s.memory_limit_mb == config.MEMORY_MIN_MB


def test_region_source_without_region_falls_back_to_screen():
    assert Settings.from_dict({"source": "region"}).source == config.SOURCE_SCREEN
    assert Settings.from_dict({"source": "region", "region": [0, 0, 5, 5]}).source == "screen"
    s = Settings.from_dict({"source": "region", "region": [0, 0, 50, 50]})
    assert s.source == "region" and s.region == [0, 0, 50, 50]


def test_corrupt_file_loads_defaults(tmp_path):
    (tmp_path / "settings.json").write_text("{not json", encoding="utf-8")
    assert SettingsStore(tmp_path).load() == Settings()


def test_save_is_atomic_and_leaves_no_temp_files(tmp_path):
    store = SettingsStore(tmp_path / "sub")
    store.save(Settings(delay=2))
    assert [p.name for p in (tmp_path / "sub").iterdir()] == ["settings.json"]
    assert json.loads(store.path.read_text(encoding="utf-8"))["delay"] == 2
