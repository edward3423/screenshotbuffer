"""Build the release archive for the current platform.

    uv run --group build python packaging/build.py

Produces dist/ScreenshotBuffer-windows-x64.zip or
dist/ScreenshotBuffer-macos-<arch>.zip, after running the built app's
self-test. Exits non-zero if anything fails.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"
BUILD = ROOT / "build"
DIST = ROOT / "dist"
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"


def run(cmd: list[str | Path], **kwargs: object) -> None:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kwargs)  # type: ignore[arg-type]


def version() -> str:
    with open(ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)["project"]["version"]


def asset_name() -> str:
    if IS_WIN:
        return "ScreenshotBuffer-windows-x64.zip"
    if IS_MAC:
        arch = platform.machine()
        return f"ScreenshotBuffer-macos-{'arm64' if arch == 'arm64' else 'x86_64'}.zip"
    raise SystemExit("Screenshot Buffer builds are only supported on Windows and macOS.")


def make_icon() -> Path:
    icon_dir = BUILD / "icon"
    icon_dir.mkdir(parents=True, exist_ok=True)
    if IS_WIN:
        ico = icon_dir / "ScreenshotBuffer.ico"
        run([sys.executable, "-m", "screenshotbuffer.icon_export", "ico", ico])
        return ico
    iconset = icon_dir / "ScreenshotBuffer.iconset"
    run([sys.executable, "-m", "screenshotbuffer.icon_export", "iconset", iconset])
    icns = icon_dir / "ScreenshotBuffer.icns"
    run(["iconutil", "-c", "icns", "-o", icns, iconset])
    return icns


def pyinstaller(icon: Path, ver: str) -> Path:
    env = dict(os.environ, SB_ICON=str(icon), SB_VERSION=ver)
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            PACKAGING / "ScreenshotBuffer.spec",
            "--noconfirm",
            "--clean",
            "--distpath",
            BUILD / "pyinstaller-dist",
            "--workpath",
            BUILD / "pyinstaller-work",
        ],
        env=env,
    )
    out = BUILD / "pyinstaller-dist"
    return out / ("Screenshot Buffer.app" if IS_MAC else "ScreenshotBuffer")


def executable(bundle: Path) -> Path:
    if IS_MAC:
        return bundle / "Contents" / "MacOS" / "ScreenshotBuffer"
    return bundle / "ScreenshotBuffer.exe"


def self_test(bundle: Path) -> None:
    report = BUILD / "self-test.txt"
    report.unlink(missing_ok=True)
    result = subprocess.run([str(executable(bundle)), "--self-test", str(report)], timeout=180)
    text = report.read_text(encoding="utf-8") if report.exists() else "(no report written)\n"
    print("--- self-test ---\n" + text, flush=True)
    if result.returncode != 0 or not text.rstrip().endswith("OK"):
        raise SystemExit(f"Self-test of the built app failed (exit code {result.returncode}).")


def archive(bundle: Path, name: str) -> Path:
    DIST.mkdir(exist_ok=True)
    target = DIST / name
    target.unlink(missing_ok=True)
    if IS_MAC:
        # ditto keeps the symlinks and code signature intact.
        run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", bundle, target])
    else:
        shutil.make_archive(str(target.with_suffix("")), "zip", root_dir=bundle.parent, base_dir=bundle.name)
    return target


def main() -> None:
    ver = version()
    name = asset_name()
    print(f"Building Screenshot Buffer {ver} -> {name}", flush=True)
    shutil.rmtree(BUILD, ignore_errors=True)
    bundle = pyinstaller(make_icon(), ver)

    if IS_MAC:
        # Ad-hoc signature: required on Apple Silicon, and gives macOS a stable
        # identity for the Screen Recording permission within this version.
        run(["codesign", "--force", "--deep", "--sign", "-", bundle])
        run(["codesign", "--verify", "--deep", "--strict", bundle])
    else:
        shutil.copy2(PACKAGING / "windows" / "uninstall.ps1", bundle / "uninstall.ps1")
    (bundle / "VERSION.txt" if IS_WIN else BUILD / "VERSION.txt").write_text(ver + "\n", encoding="utf-8")

    self_test(bundle)
    out = archive(bundle, name)
    print(f"Built {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
