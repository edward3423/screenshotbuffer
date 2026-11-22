import struct

import pytest
from PySide6.QtGui import QIcon

from screenshotbuffer.icon_export import SIZES, ico_bytes, write_ico, write_iconset


@pytest.fixture(scope="module", autouse=True)
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_ico_directory_layout():
    data = ico_bytes({16: b"a" * 10, 256: b"b" * 20})
    reserved, kind, count = struct.unpack_from("<HHH", data)
    assert (reserved, kind, count) == (0, 1, 2)
    w16, _, _, _, _, bpp, size16, off16 = struct.unpack_from("<BBBBHHII", data, 6)
    w256, _, _, _, _, _, size256, off256 = struct.unpack_from("<BBBBHHII", data, 22)
    assert (w16, bpp, size16, off16) == (16, 32, 10, 38)
    assert (w256, size256, off256) == (0, 20, 48)  # 0 encodes 256
    assert data[off256:] == b"b" * 20


def test_written_icon_loads_with_all_sizes(tmp_path):
    path = tmp_path / "sub" / "app.ico"
    write_ico(path)
    icon = QIcon(str(path))
    assert not icon.isNull()
    assert max(s.width() for s in icon.availableSizes()) == max(SIZES)


def test_iconset_has_the_files_iconutil_expects(tmp_path):
    write_iconset(tmp_path / "app.iconset")
    names = sorted(p.name for p in (tmp_path / "app.iconset").iterdir())
    assert "icon_16x16.png" in names and "icon_512x512@2x.png" in names
    assert len(names) == 10
