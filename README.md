# Screenshot Buffer

Keeps a rolling recording of your screen in memory and, when you press a
hotkey, copies what was on screen **t seconds ago** to the clipboard.

Made for taking lecture slides into Anki: by the time you can tell that a
slide's stepped animations are finished, the lecturer may already have moved
on. Press the hotkey anyway and you get the finished slide from a moment
earlier, ready to paste.

## Features

- Rolling buffer with adjustable frame rate (1-60 fps) and delay `t` (0-120 s).
- Records the full screen (pick the monitor) or a custom region.
- Always-on-top preview (top-right by default) showing exactly the frame a copy
  would grab right now. Drag to move, drag its border to resize, scroll to
  change the delay, double-click to copy. It is hidden from screen capture,
  so it never shows up in your screenshots, even when it covers the region.
- Region editor with handles: drag to move, drag handles/edges to resize,
  Shift = keep aspect ratio, Alt or Ctrl (Option or Cmd on macOS) = resize
  symmetrically around the center, Shift-drag inside = move along one axis,
  arrows nudge, edges snap to screen edges. Exact coordinates can also be typed
  in Settings.
- Tray (menu bar) icon whose menu lists every action with its shortcut, plus a
  "Keyboard shortcuts..." cheat sheet.
- Settings window; all changes apply immediately and are saved.
- Memory-efficient: identical frames are stored once (a static slide costs one
  frame), the rest are LZ4-compressed, and there is a memory cap.

## Shortcuts

| Action | Windows | macOS |
| --- | --- | --- |
| Copy the screen from t seconds ago | Ctrl+PrtScn | Cmd+Shift+9 |
| Show / hide preview | Shift+PrtScn | Cmd+Shift+0 |
| Select a new capture region | Ctrl+Shift+PrtScn | Cmd+Shift+8 |
| Pause / resume recording | (not set) | (not set) |

All of them can be changed in Settings. Macs have no Print Screen key, hence
the different defaults.

## Running

Requires [uv](https://docs.astral.sh/uv/).

```
uv run screenshotbuffer          # no console window
uv run python -m screenshotbuffer   # with log output in the terminal
```

On first launch the settings window opens; afterwards the app lives in the
tray. Launching it again while it runs just opens the settings window.

Settings and a log file live in `%APPDATA%\ScreenshotBuffer` (Windows) or
`~/Library/Application Support/ScreenshotBuffer` (macOS).

### macOS notes

- Grant **Screen Recording** permission (System Settings > Privacy & Security
  > Screen Recording) to the app that launches it (Terminal, iTerm, ...), then
  restart it. Without it macOS only returns the wallpaper.
- Global hotkeys use the Carbon hotkey API and need no Accessibility
  permission.
- The macOS code paths were written against the documented APIs but have not
  been run on a Mac yet; please report anything that misbehaves.

### Windows notes

- Hiding the preview from captures needs Windows 10 version 2004 or newer.
- If a shortcut is already taken by another program, Settings shows it as
  "Not active" so you can pick another one.

## Development

```
uv run pytest                                   # unit tests
uv run ruff check src tests && uv run ruff format --check src tests
uv run python tests/e2e/run_e2e_windows.py      # end-to-end on a real Windows desktop
uv run python tests/e2e/ui_tour.py OUT_DIR      # screenshots of every UI surface
```

The end-to-end test launches the real app, shows a window that changes color
on a fixed schedule, presses the real Ctrl+PrtScn / Shift+PrtScn with
synthesized input, and checks that the clipboard holds the frame from exactly
`t` seconds earlier, at the right size, without the preview or region outline
in it. It saves and restores your clipboard.

Set `SCREENSHOTBUFFER_SHOW_IN_CAPTURE=1` to make the app's own windows visible
in screenshots (useful for documenting the UI), and
`SCREENSHOTBUFFER_CONFIG_DIR` to use a separate settings directory.

### Layout

- `buffer.py` - thread-safe, time-indexed frame ring buffer (Qt-free)
- `capture.py` - capture thread (mss) feeding the buffer
- `geometry.py` - resize/snap math and logical-to-physical mapping (Qt-free)
- `hotkeys/` - RegisterHotKey (Windows) and Carbon (macOS) global hotkeys
- `native.py` - capture exclusion, window levels, macOS app policy
- `ui/` - preview, region tools, settings, toast
- `app.py` - controller, tray menu, single-instance entry point
