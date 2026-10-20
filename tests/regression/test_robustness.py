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


def test_dangling_media_rel_generate_fails_typed_or_avoids_it(tmp_path):
    """Media-парт удалён, slide rel на него остался.

    Инвариант (ADR-031, пересмотр): битый файл на выходе недопустим. Если
    слайд с висячей связью попал в колоду — честный ``package_corrupt`` с
    именем парта; если образцом стал другой слайд (выбор образцов с тех пор
    поменялся и этот слайд не берёт), колода собирается и в ней нет ни одной
    висячей связи. Раньше тест требовал только первый исход."""
    from deckdna.pptx.opc.package import OpcPackage

    media = _media_part(TEMPLATE)
    corrupt = _rewrite_zip(
        TEMPLATE, tmp_path / "dang.pptx", lambda n, b: None if n == media else b
    )
    try:
        report = _gen(corrupt, CONTENT, tmp_path / "g")
    except DeckDNAError as exc:
        assert exc.code == "package_corrupt"
        assert media in exc.message
        return
    out = OpcPackage.open(report["artifacts"]["pptx"])
    dangling = [
        (part, target)
        for part in out.parts
        if part.startswith("ppt/slides/slide") and part.endswith(".xml")
        for _rel, target in out.internal_dependencies(part)
        if target not in out.parts
    ]
    assert not dangling, dangling


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


def test_empty_content_without_a_model_fails_typed(tmp_path):
    """ADR-030: пустой markdown без модели — собирать не из чего. Раньше
    колода собиралась из пустых слайдов-заглушек; теперь typed
    invalid_input на стадии планирования с понятной причиной."""
    empty = tmp_path / "empty.md"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(DeckDNAError) as ei:
        _gen(TEMPLATE, empty, tmp_path / "g")
    assert ei.value.code == "invalid_input"
    assert "no content" in ei.value.message


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


def test_huge_paragraph_never_loses_text_silently(tmp_path):
    """План с units > eligible slots: потеря честно видна в отчёте.

    Один 50k paragraph больше не переполняет: bounded sentence-split
    (story_director) складывает хвост в единичные юниты — 18 units на
    10 слайдов вмещаются, весь текст доходит (см.
    test_huge_paragraph_bounded_units_no_loss). Честное переполнение
    достижимо только когда план несёт больше текстовых юнитов, чем на
    слайдах eligible body-слотов — например 200 отдельных абзацев.
    """
    huge = tmp_path / "huge.md"
    huge.write_text(
        "# Заголовок\n\n"
        + "\n\n".join(
            f"Пункт {i}: достаточно длинный текст пункта номер {i}, "
            "чтобы стать отдельным юнитом."
            for i in range(200)
        ),
        encoding="utf-8",
    )
    report = _gen(TEMPLATE, huge, tmp_path / "g")
    # ADR-031: плотная проза и клонирование карточек теперь размещают все
    # 200 пунктов. Инвариант прежний — потеря не бывает молчаливой: каждый
    # пункт либо на слайде, либо учтён в text_unplaced с предупреждением
    import re

    from pptx import Presentation

    text = " ".join(
        shape.text_frame.text
        for slide in Presentation(report["artifacts"]["pptx"]).slides
        for shape in slide.shapes
        if shape.has_text_frame
    )
    placed = {int(n) for n in re.findall(r"Пункт (\d+):", text)}
    dropped = report["compose_report"]["dropped_units"]
    if len(placed) < 200:
        assert dropped.get("text_unplaced", 0) > 0
        assert report["compose_report"]["warnings"], "mass text loss must produce a warning"
    else:
        assert not dropped.get("text_unplaced")


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
