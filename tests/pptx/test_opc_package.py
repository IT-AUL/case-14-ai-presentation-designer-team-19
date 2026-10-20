"""OpcPackage.open zip-bomb guards: hardening check #20.

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


def test_set_core_properties_creates_part_when_absent(tmp_path):
    """Пакет без docProps/core.xml получает его с rel и content-type."""
    from deckdna.pptx.opc.package import CORE_PROPS_PART, CORE_PROPS_REL
    from lxml import etree

    src = tmp_path / "bare.pptx"
    _write_zip(src, {CONTENT_TYPES: _MIN_CONTENT_TYPES, PRESENTATION: _MIN_PRESENTATION})
    pkg = OpcPackage.open(src)
    pkg.set_core_properties(title="Тема колоды", subject="Предмет", creator="DeckDNA")
    assert CORE_PROPS_PART in pkg.parts
    root = etree.fromstring(pkg.parts[CORE_PROPS_PART])
    texts = {etree.QName(el).localname: el.text for el in root}
    assert texts["title"] == "Тема колоды" and texts["creator"] == "DeckDNA"
    assert any(r.type == CORE_PROPS_REL for r in pkg.rels(""))
    assert not pkg.content_type_coverage({CORE_PROPS_PART})
    assert not pkg.check_rel_integrity()


def test_generate_deck_overrides_template_metadata(tmp_path):
    """BE3: собранная колода не наследует dc:title/dc:creator шаблона —
    свойства пакета описывают тему новой колоды."""
    from deckdna.contracts.deck_plan import (
        Brief,
        DeckPlan,
        DensityBudget,
        Level,
        Provenance,
        Purpose,
        SlidePlan,
    )
    from deckdna.pptx.composing.minimal import generate_deck
    from pptx import Presentation

    tpl = tmp_path / "tpl.pptx"
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = "Чужой заголовок"
    prs.core_properties.title = "Где Хайп?: КТ-2 исследование"
    prs.core_properties.author = "Анастасия Юшкова"
    prs.save(tpl)

    plan = DeckPlan(
        schema_version="deck-plan/1",
        id="dp",
        brief=Brief(
            purpose="Итоги квартала по регионам", audience="команда продукта",
            language="ru", target_slide_count=3,
        ),
        evidence_graph_id="eg",
        provenance=Provenance(
            planner="test", prompt_version="t", schema_version="deck-plan/1"
        ),
        objective="Итоги квартала по регионам",
        audience="команда продукта",
        language="ru",
        slides=[
            SlidePlan(
                id="s1", index=0, purpose=Purpose.title,
                title_intent="Итоги квартала по регионам",
                key_message="Итоги квартала по регионам",
                evidence_ids=["e1"], content_units=[],
                density_budget=DensityBudget(level=Level.medium),
            )
        ],
    )
    out = tmp_path / "out.pptx"
    generate_deck(tpl, plan, out)

    with zipfile.ZipFile(out) as zf:
        core = zf.read("docProps/core.xml").decode()
    assert "Где Хайп" not in core and "Анастасия Юшкова" not in core
    props = Presentation(str(out)).core_properties
    assert props.title == "Итоги квартала по регионам"
    assert props.author == "DeckDNA"
    assert props.subject == "Итоги квартала по регионам"
