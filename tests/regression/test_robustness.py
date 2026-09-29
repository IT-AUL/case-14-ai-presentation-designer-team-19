"""Robustness/fuzz-регрессия: намеренно повреждённые и пограничные входы.

Инвариант: система либо корректно деградирует (честный drop / blocker /
integrity-issue), либо падает typed-ошибкой с контекстом — не молчит
о потере данных и не выдаёт голый traceback без контекста.

Случаи:
1. dangling relationship на media-парте (парт удалён, rel остался);
2. content pack с пустым blocks (пустой markdown);
3. slideMaster без единого slideLayout;
4. slideMaster -> несуществующий theme;
5. один paragraph на ~50k символов (overflow stress);
6. ZIP валиден, но один slide-part не well-formed.
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import pytest
from deckdna.audit.basic import RULE_PACKAGE, audit_deck
from deckdna.errors import DeckDNAError
from deckdna.generation.pipeline import generate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TEMPLATE = FIXTURES / "pptx" / "synthetic_unseen.pptx"
CONTENT = FIXTURES / "content" / "poc_article.md"

BRIEF = {
    "purpose": "robustness fuzz",
    "audience": "qa",
    "language": "ru",
    "target_slide_count": 10,
}


def _rewrite_zip(src: Path, dst: Path, transform) -> Path:
    """Копия pptx-zip с transform(name, bytes) -> bytes | None по мемберу."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(
        dst, "w", zipfile.ZIP_DEFLATED
    ) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            res = transform(info.filename, data)
            if res is not None:
                zout.writestr(info.filename, res)
    return dst


def _media_part(pptx: Path) -> str:
    with zipfile.ZipFile(pptx) as zf:
        for name in zf.namelist():
            if name.startswith("ppt/media/"):
                return name
    raise AssertionError(f"no media part in {pptx}")


def _gen(template: Path, content: Path, out_dir: Path):
    return generate(template, content, dict(BRIEF), out_dir)


# ------------------------------------------------------------------ case 1


def test_dangling_media_rel_generate_fails_typed(tmp_path):
    """Media-парт удалён, slide rel на него остался — честный package_corrupt."""
    media = _media_part(TEMPLATE)
    corrupt = _rewrite_zip(
        TEMPLATE, tmp_path / "dang.pptx", lambda n, b: None if n == media else b
    )
    with pytest.raises(DeckDNAError) as ei:
        _gen(corrupt, CONTENT, tmp_path / "g")
    assert ei.value.code == "package_corrupt"
    assert media in ei.value.message


def test_dangling_media_rel_audit_flags_integrity(tmp_path):
    """Пакет открывается python-pptx → аудит идёт, но висячий rel честно сигналится."""
    media = _media_part(TEMPLATE)
    corrupt = _rewrite_zip(
        TEMPLATE, tmp_path / "dang.pptx", lambda n, b: None if n == media else b
    )
    issues = audit_deck(corrupt)
    pkg = [i for i in issues if i.rule_code == RULE_PACKAGE]
    assert pkg, "dangling rel must surface as integrity.package issue"
    assert pkg[0].severity == "error"
    assert media in pkg[0].message


# ------------------------------------------------------------------ case 2


def test_empty_blocks_content_generates_with_honest_flags(tmp_path):
    """Пустой markdown → колода собирается, аудит честно ловит пустые слайды."""
    empty = tmp_path / "empty.md"
    empty.write_text("", encoding="utf-8")
    report = _gen(TEMPLATE, empty, tmp_path / "g")
    assert report["slides_out"] == BRIEF["target_slide_count"]
    rules = {i["rule_code"] for i in report["audit_issues"]}
    assert "integrity.empty_slide" in rules


# ------------------------------------------------------------------ case 3


def _drop_layouts(name: str, data: bytes):
    if re.fullmatch(r"ppt/slideLayouts/slideLayout\d+\.xml", name):
        return None
    if re.fullmatch(r"ppt/slideLayouts/_rels/slideLayout\d+\.xml\.rels", name):
        return None
    if name == "ppt/slideMasters/_rels/slideMaster1.xml.rels":
        return re.sub(
            rb'<Relationship[^>]*slideLayout[^>]*/>', b"", data
        )
    return data


def test_master_without_layouts_fails_typed(tmp_path):
    """Master без layouts: slide layout-rel висячий — package_corrupt у обоих."""
    corrupt = _rewrite_zip(TEMPLATE, tmp_path / "nol.pptx", _drop_layouts)
    with pytest.raises(DeckDNAError) as ei:
        _gen(corrupt, CONTENT, tmp_path / "g")
    assert ei.value.code == "package_corrupt"
    issues = audit_deck(corrupt)
    blockers = [i for i in issues if i.rule_code == RULE_PACKAGE]
    assert blockers and blockers[0].severity == "blocker"


# ------------------------------------------------------------------ case 4


def test_master_missing_theme(tmp_path):
    """theme-парт удалён, rel из master остался: generate падает, аудит сигналит."""
    corrupt = _rewrite_zip(
        TEMPLATE,
        tmp_path / "notheme.pptx",
        lambda n, b: None if n.startswith("ppt/theme/") else b,
    )
    with pytest.raises(DeckDNAError) as ei:
        _gen(corrupt, CONTENT, tmp_path / "g")
    assert ei.value.code == "package_corrupt"
    assert "theme" in ei.value.message
    issues = audit_deck(corrupt)
    pkg = [i for i in issues if i.rule_code == RULE_PACKAGE]
    assert pkg and pkg[0].severity == "error"
    assert "theme" in pkg[0].message


# ------------------------------------------------------------------ case 5


def test_huge_paragraph_reports_unplaced_text(tmp_path):
    """План с units > eligible slots: потеря честно видна в отчёте.

    Один 50k paragraph больше не переполняет: bounded sentence-split
    (story_director) складывает хвост в единичные юниты — 18 units на
    10 слайдов вмещаются, весь текст доходит (см.
    test_huge_paragraph_bounded_units_no_loss). На полном pipeline
    compose-level drop больше недостижим: dense_prose/adaptive_cards
    поглощают сколь угодно много юнитов (проверено до 800), а при
    ~2000 честно падает sources-slide (composition_failed) — то есть
    потери либо нет, либо есть typed-ошибка, но не молчаливая потеря.
    Реальная ветка text_unplaced жива на уровне композера: слайд плана
    с 6 текстовыми юнитами на эталоне-картосетке vk_tech (card-grid
    donor блокирует adaptive_cards-rescue; eligible bodies = 0)
    честно дропает все юниты и пишет warning.
    """
    from deckdna.contracts.variant_spec import Strategy
    from deckdna.pptx.cloning.exemplar import (
        ExemplarChoice,
        slide_layout_map,
    )
    from deckdna.pptx.composing.minimal import generate_deck
    from deckdna.pptx.opc.package import OpcPackage

    tpl = FIXTURES / "pptx" / "vk_tech_template.pptx"
    if not tpl.exists():
        pytest.skip("organizer fixture not present")
    pkg = OpcPackage.open(tpl)
    grid_part = "ppt/slides/slide8.xml"  # card-grid donor: 5 card slots, 1 body
    choice = ExemplarChoice(
        slide_part=grid_part,
        layout_part=slide_layout_map(pkg)[grid_part] or "",
        shape_count=5,
        layout_slide_count=1,
        long_runs=1,
        content_slots=1,
    )
    plan = json.loads(
        (FIXTURES / "content" / "poc_deck_plan.json").read_text(encoding="utf-8")
    )
    plan["slides"] = plan["slides"][:1]
    plan["slides"][0]["content_units"] = [
        {"role": "title", "kind": "title", "text": "Слайд с избытком юнитов"},
    ] + [
        {
            "role": "body",
            "kind": "bullet",
            "text": f"Юнит {i} — текст, которому нет слота на слайде",
        }
        for i in range(6)
    ]
    report = generate_deck(
        tpl,
        plan,
        tmp_path / "over.pptx",
        exemplar_choices=[choice],
        strategy=Strategy.faithful,
    )
    dropped = report["dropped_units"]
    assert dropped.get("text_unplaced", 0) > 0
    assert report["warnings"], "mass text loss must produce a warning"


# ------------------------------------------------------------------ case 6


def test_malformed_slide_part_named_in_error(tmp_path):
    """Well-formed ZIP + битый slideN.xml: package_corrupt с именем парта."""
    corrupt = _rewrite_zip(
        TEMPLATE,
        tmp_path / "badxml.pptx",
        lambda n, b: b[: len(b) // 2] if n == "ppt/slides/slide2.xml" else b,
    )
    with pytest.raises(DeckDNAError) as ei:
        _gen(corrupt, CONTENT, tmp_path / "g")
    assert ei.value.code == "package_corrupt"
    assert "ppt/slides/slide2.xml" in ei.value.message
    issues = audit_deck(corrupt)
    blockers = [i for i in issues if i.rule_code == RULE_PACKAGE]
    assert blockers and blockers[0].severity == "blocker"
    assert "slide2.xml" in blockers[0].message
