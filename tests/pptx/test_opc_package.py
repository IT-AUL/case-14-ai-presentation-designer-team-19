"""OpcPackage.open zip-bomb guards.

Before this, ``open`` read and decompressed every member into memory
(``{name: zf.read(name) for name in zf.namelist()}``) with no limit on
entry count or total decompressed size -- a small, highly-compressible
crafted archive could expand to exhaust memory on extraction. The guards
are checked from ZipInfo metadata (central directory) before any member
is decompressed, so a real limit test doesn't need to actually build a
multi-hundred-MB payload -- the module constants are monkeypatched down
to a size the test can build directly.
"""

import io
import zipfile
from pathlib import Path

import pytest
from deckdna.errors import DeckDNAError
from deckdna.pptx.opc import package as package_mod
from deckdna.pptx.opc.package import CONTENT_TYPES, PRESENTATION, OpcPackage

_MIN_CONTENT_TYPES = (
    b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
)
_MIN_PRESENTATION = (
    b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    b'<p:presentation xmlns:p="http://schemas.openxmlformats.org/'
    b'presentationml/2006/main"/>'
)


def _write_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def test_open_accepts_a_normal_small_package(tmp_path):
    out = tmp_path / "ok.pptx"
    _write_zip(
        out,
        {CONTENT_TYPES: _MIN_CONTENT_TYPES, PRESENTATION: _MIN_PRESENTATION},
    )
    pkg = OpcPackage.open(out)
    assert PRESENTATION in pkg.parts


def test_open_rejects_too_many_parts(tmp_path, monkeypatch):
    monkeypatch.setattr(package_mod, "_MAX_PARTS", 3)
    out = tmp_path / "many_parts.pptx"
    members = {CONTENT_TYPES: _MIN_CONTENT_TYPES, PRESENTATION: _MIN_PRESENTATION}
    for i in range(5):
        members[f"ppt/extra{i}.xml"] = b"<x/>"
    _write_zip(out, members)
    with pytest.raises(DeckDNAError) as exc:
        OpcPackage.open(out)
    assert exc.value.code == "package_corrupt"
    assert "part limit" in exc.value.message


def test_open_rejects_zip_bomb_by_declared_uncompressed_size(tmp_path, monkeypatch):
    monkeypatch.setattr(package_mod, "_MAX_UNCOMPRESSED_BYTES", 10_000)
    out = tmp_path / "bomb.pptx"
    # Highly compressible: a run of zero bytes deflates to a tiny member
    # but declares (and, on read, actually expands to) far more than the
    # patched-down limit -- exactly the shape of a real zip bomb, just
    # scaled to a size a test can build in milliseconds.
    bomb = b"\x00" * 200_000
    _write_zip(
        out,
        {
            CONTENT_TYPES: _MIN_CONTENT_TYPES,
            PRESENTATION: _MIN_PRESENTATION,
            "ppt/media/image1.bin": bomb,
        },
    )
    with pytest.raises(DeckDNAError) as exc:
        OpcPackage.open(out)
    assert exc.value.code == "package_corrupt"
    assert "byte limit" in exc.value.message


def test_open_rejects_extreme_compression_ratio_before_reading_members(tmp_path, monkeypatch):
    monkeypatch.setattr(package_mod, "_MAX_COMPRESSION_RATIO", 10.0)
    monkeypatch.setattr(package_mod, "_COMPRESSION_RATIO_MIN_BYTES", 1_000)
    out = tmp_path / "ratio-bomb.pptx"
    repetitive = b"A" * 50_000
    _write_zip(
        out,
        {
            CONTENT_TYPES: _MIN_CONTENT_TYPES,
            PRESENTATION: _MIN_PRESENTATION,
            "ppt/media/repetitive.bin": repetitive,
        },
    )
    with pytest.raises(DeckDNAError) as exc:
        OpcPackage.open(out)
    assert exc.value.code == "package_corrupt"
    assert "compression ratio" in exc.value.message


def test_open_still_rejects_corrupt_member_after_size_checks(tmp_path):
    out = tmp_path / "corrupt.pptx"
    _write_zip(
        out,
        {CONTENT_TYPES: _MIN_CONTENT_TYPES, PRESENTATION: _MIN_PRESENTATION},
    )
    # Flip a byte inside a compressed member's data to break its CRC
    # without touching the central directory, so it passes the new
    # metadata-only size checks and is only caught by testzip().
    raw = bytearray(out.read_bytes())
    marker = zipfile.ZipFile(io.BytesIO(bytes(raw))).infolist()
    target = next(i for i in marker if i.filename == PRESENTATION)
    offset = target.header_offset + 34 + len(target.filename.encode())
    raw[offset] ^= 0xFF
    out.write_bytes(bytes(raw))
    with pytest.raises(DeckDNAError) as exc:
        OpcPackage.open(out)
    assert exc.value.code == "package_corrupt"
