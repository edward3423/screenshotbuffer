"""Screenshot Buffer: copy what was on screen a few seconds ago."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("screenshotbuffer")
except PackageNotFoundError:  # running from a source tree that is not installed
    __version__ = "0.0.0"


def main() -> None:
    from .app import main as run

    raise SystemExit(run())
