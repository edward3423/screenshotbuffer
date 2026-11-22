# PyInstaller spec. Run through packaging/build.py, which sets SB_ICON and SB_VERSION.
# ruff: noqa: F821  (Analysis, PYZ, EXE, COLLECT, BUNDLE and SPECPATH are provided by PyInstaller)
import os
import sys

from PyInstaller.utils.hooks import copy_metadata

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ICON = os.environ["SB_ICON"]
VERSION = os.environ["SB_VERSION"]
IS_MAC = sys.platform == "darwin"

if IS_MAC:
    hidden = ["mss.darwin", "screenshotbuffer.hotkeys._macos"]
    other_platform = ["mss.windows", "screenshotbuffer.hotkeys._windows"]
else:
    hidden = ["mss.windows", "mss.windows.gdi", "screenshotbuffer.hotkeys._windows"]
    other_platform = ["mss.darwin", "mss.linux", "screenshotbuffer.hotkeys._macos"]

a = Analysis(
    [os.path.join(SPECPATH, "launch.py")],
    pathex=[os.path.join(ROOT, "src")],
    hiddenimports=hidden + ["screenshotbuffer.selftest"],
    # importlib.metadata needs this to report the version.
    datas=copy_metadata("screenshotbuffer"),
    excludes=["tkinter", "unittest", "pydoc", "PySide6.QtTest", "pytest", *other_platform],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ScreenshotBuffer",
    console=False,
    icon=ICON,
    upx=False,
    argv_emulation=False,
    codesign_identity=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="ScreenshotBuffer", upx=False)

if IS_MAC:
    app = BUNDLE(
        coll,
        name="Screenshot Buffer.app",
        icon=ICON,
        bundle_identifier="io.github.edward3423.screenshotbuffer",
        version=VERSION,
        info_plist={
            "CFBundleDisplayName": "Screenshot Buffer",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            # Menu-bar app: no Dock icon.
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
            # PySide6 6.12 wheels require macOS 14.
            "LSMinimumSystemVersion": "14.0",
        },
    )
