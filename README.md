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

## Install

### Windows 10 (2004+) / 11

Paste into PowerShell:

```powershell
irm https://github.com/edward3423/screenshotbuffer/releases/latest/download/install.ps1 | iex
```

This installs for your user only (no admin rights) into
`%LOCALAPPDATA%\Programs\ScreenshotBuffer`, adds **Screenshot Buffer** to the
Start menu and to Settings > Apps, and starts it. Run the same command again to
update; your settings are kept.

Also start it when you sign in:

```powershell
& ([scriptblock]::Create((irm https://github.com/edward3423/screenshotbuffer/releases/latest/download/install.ps1))) -StartAtLogin
```

Uninstall from Settings > Apps, or with `-Uninstall` in place of `-StartAtLogin`.

### macOS 14 (Sonoma) or newer

Paste into Terminal:

```bash
curl -fsSL https://github.com/edward3423/screenshotbuffer/releases/latest/download/install.sh | bash
```

This installs **Screenshot Buffer.app** into `/Applications` (or
`~/Applications` if that is not writable) and starts it. It lives in the menu
bar. Run the same command again to update.

Also start it when you log in:

```bash
curl -fsSL https://github.com/edward3423/screenshotbuffer/releases/latest/download/install.sh | bash -s -- --start-at-login
```

Uninstall with `bash -s -- --uninstall`.

On first start, macOS asks for **Screen Recording** permission. Allow it in
System Settings > Privacy & Security > Screen Recording, then start Screenshot
Buffer again. The app is not notarized by Apple; the install script downloads it
directly, so Gatekeeper does not block it. After an update macOS may ask for the
permission again.

Both scripts verify the download against the release's `SHA256SUMS`. A specific
version can be installed with `-Version v0.1.0` (PowerShell) or
`--version v0.1.0` (bash).

## Running from source

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

- When running from source, grant **Screen Recording** permission to the app
  that launches it (Terminal, iTerm, ...) instead. Without the permission macOS
  only returns the wallpaper.
- Global hotkeys use the Carbon hotkey API and need no Accessibility
  permission.
- Every build runs a self-test on GitHub's macOS machines (capture, hotkeys,
  windows), but the app has not yet been used day to day on a Mac; please
  report anything that misbehaves.

### Windows notes

- Hiding the preview from captures needs Windows 10 version 2004 or newer.
- If a shortcut is already taken by another program, Settings shows it as
  "Not active" so you can pick another one.

## Development

```
uv run pytest                                   # unit tests
uv run ruff check src tests && uv run ruff format --check src tests
uv run python tests/e2e/run_e2e_windows.py      # end-to-end on a real Windows desktop
uv run python tests/e2e/run_e2e_macos.py        # end-to-end on a real Mac (--no-keys: without Accessibility)
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

### Releases

Pushing a tag `vX.Y.Z` that matches the version in `pyproject.toml` makes
GitHub Actions run the tests, build Windows (x64) and macOS (Apple Silicon and
Intel) apps with PyInstaller, run each built app's `--self-test`, and publish
them with the install scripts and `SHA256SUMS` as a GitHub release. To build
locally for the current platform:

```
uv run --group build python packaging/build.py   # -> dist/ScreenshotBuffer-<platform>.zip
```

### Layout

- `buffer.py` - thread-safe, time-indexed frame ring buffer (Qt-free)
- `capture.py` - capture thread (mss) feeding the buffer
- `geometry.py` - resize/snap math and logical-to-physical mapping (Qt-free)
- `hotkeys/` - RegisterHotKey (Windows) and Carbon (macOS) global hotkeys
- `native.py` - capture exclusion, window levels, macOS app policy
- `ui/` - preview, region tools, settings, toast
- `app.py` - controller, tray menu, single-instance entry point (`--quit`, `--self-test`)
- `packaging/` - PyInstaller spec and build script; `scripts/` - install scripts
