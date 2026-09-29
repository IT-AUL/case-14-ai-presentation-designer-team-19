"""diagram_fill: Diagram -> SmartArt-like grpSp (OR-004 baseline).

Не настоящий dgm/SmartArt — группа нативных редактируемых фигур
(roundRect шаги + rightArrow коннекторы) в крупнейшем опустевшем
текстовом теле слайда, по паттерну add_table.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from deckdna.contracts.content_pack import Diagram, SourceRef
from deckdna.ingestion.diagram_block import parse_diagram_block
from deckdna.pptx.composing.diagram_fill import (
    fill_slide_diagrams,
)
from deckdna.pptx.composing.minimal import generate_deck
from deckdna.pptx.opc.package import OpcPackage
from lxml import etree

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
FIXTURES = Path(__file__).parent.parent / "fixtures"
PLAN_FIXTURE = FIXTURES / "content" / "poc_deck_plan.json"
VK_TEMPLATE = Path("tests/fixtures/pptx/vk_tech_template.pptx")


def _mk_diagram(steps=4, did="dg-1"):
    return Diagram(
        id=did,
        steps=[f"Шаг {i}" for i in range(1, steps + 1)],
        source_ref=SourceRef(artifact_id="d.md"),
    )


def _template_with_empty_bodies(path: Path) -> Path:
    """Шаблон: content-like слайд (длинные тексты) — после заливки
    слоты остаются опустевшими телами-хостами."""
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    for i in range(4):
        slide.shapes.add_textbox(
            Inches(0.5), Inches(0.4 + i * 1.4), Inches(9), Inches(1.2)
        ).text_frame.text = (
            "Длинный абзац контентного текста про процесс "
            "и этапы проекта, который делает слайд content-like"
        )
    prs.save(str(path))
    return path


def _plan_with_diagram_unit() -> dict:
    plan = json.loads(PLAN_FIXTURE.read_text())
    plan["slides"] = plan["slides"][:1]
    plan["slides"][0]["content_units"] = [
        {"role": "title", "kind": "title", "text": "Процесс"},
        {"role": "body", "kind": "diagram", "diagram_ref": "dg-1"},
    ]
    return plan


def _grp_sps(slide_xml: bytes):
    root = etree.fromstring(slide_xml)
    return list(root.iter(f"{{{P}}}grpSp"))


def _slide_xmls(pkg) -> list[bytes]:
    return [
        v
        for p, v in sorted(pkg.parts.items())
        if p.startswith("ppt/slides/slide") and p.endswith(".xml")
    ]


def test_parse_diagram_block_convention():
    """Документированная markdown-конвенция парсится в Diagram."""
    block = "```diagram id=dg-7\nШаг 1 -> Шаг 2 -> Шаг 3 -> Шаг 4\n```"
    diagram = parse_diagram_block(block, SourceRef(artifact_id="d.md"))
    assert diagram is not None and diagram.id == "dg-7"
    assert diagram.steps == ["Шаг 1", "Шаг 2", "Шаг 3", "Шаг 4"]
    # без id — дефолт (парсер секций заменит на dg-N)
    d2 = parse_diagram_block(
        "```diagram\nА -> Б\n```", SourceRef(artifact_id="d.md")
    )
    assert d2 is not None and d2.id == "diagram-1"
    # один шаг — не цепочка
    assert (
        parse_diagram_block(
            "```diagram\nТолько шаг\n```", SourceRef(artifact_id="d.md")
        )
        is None
    )
    # не diagram-блок
    assert (
        parse_diagram_block(
            "обычный текст", SourceRef(artifact_id="d.md")
        )
        is None
    )


def test_markdown_diagram_reaches_pack():
    """```diagram fence -> pack.diagrams + diagram_ref-блок в потоке;
    обычный code fence остаётся Kind.code."""
    from deckdna.ingestion.content_parsers import parse_markdown

    pack = parse_markdown(
        "# Процесс\n\n"
        "```diagram\nСбор -> Анализ -> Вывод\n```\n\n"
        "```python\nprint(1)\n```\n"
    )
    assert len(pack.diagrams) == 1
    assert pack.diagrams[0].steps == ["Сбор", "Анализ", "Вывод"]
    kinds = [b.kind.value for s in pack.sections for b in s.blocks]
    assert "diagram_ref" in kinds and "code" in kinds


def test_plan_deck_carries_diagram_ref():
    """diagram_ref-блок доходит до DeckPlan как Kind.diagram unit
    (тем же путём, что chart_ref/table_ref)."""
    from deckdna.contracts.deck_plan import Brief
    from deckdna.ingestion.content_parsers import parse_markdown
    from deckdna.planning.story_director import plan_deck

    md = "# Отчёт\n\nВводный абзац.\n\n```diagram id=dg-9\nА -> Б -> В\n```\n"
    pack = parse_markdown(md)
    plan = plan_deck(
        pack,
        Brief(
            purpose="Отчёт",
            audience="Жюри",
            language="ru",
            target_slide_count=10,
        ),
    )
    units = [
        u for s in plan.slides for u in s.content_units if u.diagram_ref
    ]
    assert units and units[0].diagram_ref == "dg-9"
    assert units[0].kind.value == "diagram"


def test_compose_creates_diagram_group(tmp_path):
    """e2e: slide с diagram unit -> НОВАЯ grpSp в опустевшем теле:
    шаги в roundRect с реальным текстом, rightArrow между ними."""
    tpl = _template_with_empty_bodies(tmp_path / "tpl.pptx")
    report = generate_deck(
        tpl,
        _plan_with_diagram_unit(),
        tmp_path / "out.pptx",
        diagrams=[_mk_diagram()],
    )
    slide_rep = report["slides"][0]
    assert slide_rep["diagrams"]["diagrams_created"] == 1
    assert slide_rep["diagrams"]["steps_written"] == 4
    assert slide_rep["dropped_units"].get("diagram", 0) == 0

    pkg = OpcPackage.open(tmp_path / "out.pptx")
    slide_xml = _slide_xmls(pkg)[0]
    groups = _grp_sps(slide_xml)
    assert len(groups) == 1
    geoms = [g.get("prst") for g in groups[0].iter(f"{{{A}}}prstGeom")]
    assert geoms.count("roundRect") == 4
    assert geoms.count("rightArrow") == 3
    texts = [t.text for t in groups[0].iter(f"{{{A}}}t")]
    assert texts == ["Шаг 1", "Шаг 2", "Шаг 3", "Шаг 4"]

    # Выходной файл валиден для python-pptx
    from pptx import Presentation

    prs = Presentation(str(tmp_path / "out.pptx"))
    assert len(prs.slides) == 1


def test_compose_diagram_shared_hosts(tmp_path):
    """table unit + diagram unit на одном слайде — оба созданы,
    в разных опустевших телах (общий used_hosts)."""
    tpl = _template_with_empty_bodies(tmp_path / "tpl.pptx")
    from deckdna.contracts.content_pack import Table

    plan = json.loads(PLAN_FIXTURE.read_text())
    plan["slides"] = plan["slides"][:1]
    plan["slides"][0]["content_units"] = [
        {"role": "title", "kind": "title", "text": "Процесс"},
        {"role": "body", "kind": "table", "table_ref": "tbl-1"},
        {"role": "body", "kind": "diagram", "diagram_ref": "dg-1"},
    ]
    report = generate_deck(
        tpl,
        plan,
        tmp_path / "out.pptx",
        tables=[
            Table(
                id="tbl-1",
                headers=["Метрика", "Значение"],
                rows=[["Рост", 12]],
                source_ref=SourceRef(artifact_id="d.md"),
            )
        ],
        diagrams=[_mk_diagram()],
    )
    slide_rep = report["slides"][0]
    assert slide_rep["tables"]["tables_created"] == 1
    assert slide_rep["diagrams"]["diagrams_created"] == 1

    pkg = OpcPackage.open(tmp_path / "out.pptx")
    slide_xml = _slide_xmls(pkg)[0]
    assert _grp_sps(slide_xml) and list(
        etree.fromstring(slide_xml).iter(f"{{{A}}}tbl")
    )


def test_diagram_no_host_drops():
    """Сырой slide XML (все тела заняты стоком) — хоста нет ->
    unit честно дропается, XML не меняется."""
    if not VK_TEMPLATE.exists():
        pytest.skip("organizer fixture not present")
    pkg = OpcPackage.open(VK_TEMPLATE)
    slide = next(p for p in pkg.parts if p.startswith("ppt/slides/slide"))
    xml, rep = fill_slide_diagrams(pkg.parts[slide], [_mk_diagram()])
    assert rep.diagrams_created == 0 and rep.units_dropped == 1
    assert xml == pkg.parts[slide]


@pytest.mark.parametrize("steps", [0, 9])
def test_diagram_that_cannot_fit_keeps_every_step_unplaced(tmp_path, steps):
    """A partial flow is misleading, so reject the whole diagram unit."""
    from deckdna.pptx.composing.text_replace import replace_text_runs

    tpl = _template_with_empty_bodies(tmp_path / "diagram-template.pptx")
    xml = OpcPackage.open(tpl).parts["ppt/slides/slide1.xml"]
    cleared, _ = replace_text_runs(xml, [])
    result, report = fill_slide_diagrams(cleared, [_mk_diagram(steps=steps)])
    assert report.diagrams_created == 0
    assert report.steps_written == 0
    assert report.units_dropped == 1
    assert result == cleared


def test_diagram_ref_unresolved_drops(tmp_path):
    """diagram_ref без Diagram в паке -> dropped, не молчаливый пропуск."""
    tpl = _template_with_empty_bodies(tmp_path / "tpl.pptx")
    plan = _plan_with_diagram_unit()
    plan["slides"][0]["content_units"][1]["diagram_ref"] = "dg-missing"
    report = generate_deck(tpl, plan, tmp_path / "out.pptx", diagrams=[])
    assert report["dropped_units"].get("diagram") == 1
