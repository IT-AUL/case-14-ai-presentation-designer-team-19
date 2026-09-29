"""chart_fill: Chart -> c:numCache/c:strCache реального c:chart партера.

Узкий случай: bar/line/pie-чарт с одним+ сериями; кэши и (best-effort)
embedded workbook синхронизируются с данными контента.
"""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from deckdna.contracts.content_pack import Chart, ChartSeries, SourceRef
from deckdna.pptx.composing.chart_fill import (
    ChartFillReport,
    _fill_chart_xml,
    parse_chart_block,
)
from deckdna.pptx.composing.minimal import generate_deck
from deckdna.pptx.opc.package import OpcPackage, resolve_target
from lxml import etree

C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
FIXTURES = Path(__file__).parent.parent / "fixtures"
PLAN_FIXTURE = FIXTURES / "content" / "poc_deck_plan.json"
SUBMISSION = FIXTURES / "pptx" / "lct2026_submission.pptx"


def _mk_chart(
    categories=("2021", "2022", "2023"),
    series=(("Выручка", (4.3, 2.5, 3.5)),),
    cid="ch-1",
):
    return Chart(
        id=cid,
        categories=list(categories),
        series=[ChartSeries(name=n, values=list(v)) for n, v in series],
        source_ref=SourceRef(artifact_id="c.md"),
    )


def _template_with_chart(path: Path) -> Path:
    """Шаблон: content-like слайд (длинные тексты) + нативный barChart
    через python-pptx add_chart (chart part + rels + embedded xlsx)."""
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    for i in range(4):
        slide.shapes.add_textbox(
            Inches(0.5), Inches(0.3 + i * 1.75), Inches(8), Inches(1.7)
        ).text_frame.text = (
            "Длинный абзац контентного текста про показатели "
            "и результаты проекта, который делает слайд content-like"
        )
    cd = CategoryChartData()
    cd.categories = ["A", "B", "C"]
    cd.add_series("Стоковый ряд", (1.0, 2.0, 3.0))
    slide.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED,
        Inches(1), Inches(4.2), Inches(6), Inches(2.5), cd,
    )
    prs.save(str(path))
    return path


def _plan_with_chart_unit() -> dict:
    plan = json.loads(PLAN_FIXTURE.read_text())
    plan["slides"] = plan["slides"][:1]
    plan["slides"][0]["content_units"] = [
        {"role": "title", "kind": "title", "text": "Динамика"},
        {"role": "body", "kind": "chart", "chart_ref": "ch-1"},
    ]
    return plan


def _chart_xmls(pkg) -> list[bytes]:
    return [v for p, v in sorted(pkg.parts.items()) if "charts/chart" in p and p.endswith(".xml")]


def test_fill_chart_xml_rewrites_caches():
    """Прямая заливка: pts numCache/strCache переписаны под данные."""
    if not SUBMISSION.exists():
        pytest.skip("organizer fixture not present")
    pkg = OpcPackage.open(SUBMISSION)
    raw = pkg.parts["ppt/charts/chart2.xml"]  # 1 серия, 4 точки
    chart = _mk_chart(
        categories=("Q1", "Q2"),
        series=(("Выручка", (9.9, 8.8)),),
    )
    rep = ChartFillReport()
    xml = _fill_chart_xml(raw, chart, rep)
    root = etree.fromstring(xml)
    vals = [v.text for v in root.iter(f"{{{C}}}v")]
    assert rep.charts_filled == 1
    assert "9.9" in vals and "8.8" in vals
    assert "Выручка" in vals
    counts = {int(c.get("val")) for c in root.iter(f"{{{C}}}ptCount") if c.get("val") != "1"}
    assert counts == {2}


def test_fill_chart_xml_series_truncated():
    """Данные с 2 сериями в 1-серийный chart -> вторая серия дропается."""
    if not SUBMISSION.exists():
        pytest.skip("organizer fixture not present")
    pkg = OpcPackage.open(SUBMISSION)
    raw = pkg.parts["ppt/charts/chart2.xml"]
    chart = _mk_chart(series=(("S1", (1.0,)), ("S2", (2.0,))))
    rep = ChartFillReport()
    _fill_chart_xml(raw, chart, rep)
    assert rep.series_dropped == 1


def test_parse_chart_block_convention():
    """Документированная markdown-конвенция парсится в Chart."""
    block = (
        "```chart id=ch-7\n"
        "categories: 2021, 2022, 2023\n"
        "Ряд 1: 4.3, 2.5, 3.5\n"
        "```"
    )
    chart = parse_chart_block(block, SourceRef(artifact_id="c.md"))
    assert chart is not None and chart.id == "ch-7"
    assert chart.categories == ["2021", "2022", "2023"]
    assert chart.series[0].values == [4.3, 2.5, 3.5]
    assert parse_chart_block("обычный текст", SourceRef(artifact_id="c.md")) is None


def test_compose_chart_fills_native_chart(tmp_path):
    """e2e: slide с chart unit + exemplar с a:chart -> кэши переписаны,
    python-pptx читает реальные значения серии."""
    tpl = _template_with_chart(tmp_path / "tpl.pptx")
    report = generate_deck(
        tpl,
        _plan_with_chart_unit(),
        tmp_path / "out.pptx",
        charts=[_mk_chart()],
    )
    ch = report["slides"][0]["charts"]
    assert ch["charts_filled"] == 1 and ch["units_dropped"] == 0

    from pptx import Presentation

    prs = Presentation(str(tmp_path / "out.pptx"))
    chart_shape = next(s for s in prs.slides[0].shapes if s.has_chart)
    plot = chart_shape.chart.plots[0]
    assert list(plot.series[0].values) == [4.3, 2.5, 3.5]
    assert list(plot.categories) == ["2021", "2022", "2023"]


def test_compose_chart_workbook_synced(tmp_path):
    """Embedded xlsx синхронизирован: «Edit Data» покажет те же данные."""
    tpl = _template_with_chart(tmp_path / "tpl.pptx")
    generate_deck(
        tpl,
        _plan_with_chart_unit(),
        tmp_path / "out.pptx",
        charts=[_mk_chart()],
    )
    import openpyxl

    with zipfile.ZipFile(tmp_path / "out.pptx") as zf:
        xlsx_name = next(n for n in zf.namelist() if "embeddings" in n and n.endswith(".xlsx"))
        wb = openpyxl.load_workbook(BytesIO(zf.read(xlsx_name)))
    ws = wb.worksheets[0]
    cats = [str(ws.cell(row=r, column=1).value) for r in (2, 3, 4)]
    assert cats == ["2021", "2022", "2023"]
    assert ws.cell(row=1, column=2).value == "Выручка"
    assert ws.cell(row=2, column=2).value == 4.3


def test_compose_chart_no_chart_part_creates_native(tmp_path):
    """Exemplar без a:chart -> create-fallback: новый нативный c:chart
    (add_table/add_picture-аналог), не drop. Глубокие проверки пакета —
    в тестах ниже."""
    if not SUBMISSION.exists():
        pytest.skip("organizer fixture not present")
    tpl = _template_text_only(tmp_path / "tpl.pptx")
    report = generate_deck(
        tpl,
        _plan_with_chart_unit(),
        tmp_path / "out.pptx",
        charts=[_mk_chart()],
    )
    ch = report["slides"][0]["charts"]
    assert ch["charts_found"] == 0 and ch["charts_created"] == 1
    assert ch["units_dropped"] == 0
    assert "chart" not in report["dropped_units"]


def test_compose_chart_unresolved_ref_dropped(tmp_path):
    """chart_ref без Chart в паке -> dropped."""
    tpl = _template_with_chart(tmp_path / "tpl.pptx")
    report = generate_deck(
        tpl,
        _plan_with_chart_unit(),
        tmp_path / "out.pptx",
        charts=[],  # пустой список — ref не разрешается
    )
    assert report["dropped_units"].get("chart") == 1


def test_fill_slide_charts_shared_part_creates_second(tmp_path):
    """Один chart-парт не может нести два разных набора данных: второй
    unit не дропается — получает отдельный созданный c:chart в пустом
    host-боксе (create-fallback), данные не перезаписывают exemplar."""
    tpl = _template_with_chart(tmp_path / "tpl.pptx")
    plan = _plan_with_chart_unit()
    plan["slides"][0]["content_units"].append(
        {"role": "body", "kind": "chart", "chart_ref": "ch-2"}
    )
    report = generate_deck(
        tpl,
        plan,
        tmp_path / "out.pptx",
        charts=[_mk_chart(), _mk_chart(cid="ch-2")],
    )
    ch = report["slides"][0]["charts"]
    assert ch["charts_filled"] == 2 and ch["units_dropped"] == 0
    assert ch["charts_created"] == 1  # один exemplar-fill + один created


# --- wiring: markdown ```chart fence -> pack.charts -> DeckPlan ----


def test_markdown_chart_fence_extracts_chart():
    """```chart fence -> pack.charts + chart_ref-блок в потоке
    (раньше уходил в Kind.code и charts оставался пустым)."""
    from deckdna.ingestion.content_parsers import parse_markdown

    md = (
        "# Отчёт\n\n"
        "Вводный абзац.\n\n"
        "```chart\n"
        "categories: 2021, 2022, 2023\n"
        "Выручка: 4.3, 2.5, 3.5\n"
        "```\n"
    )
    pack = parse_markdown(md)
    assert len(pack.charts) == 1
    assert pack.charts[0].categories == ["2021", "2022", "2023"]
    assert pack.charts[0].series[0].values == [4.3, 2.5, 3.5]
    kinds = [b.kind.value for s in pack.sections for b in s.blocks]
    assert "chart_ref" in kinds and "code" not in kinds
    ref_block = next(
        b for s in pack.sections for b in s.blocks if b.kind.value == "chart_ref"
    )
    assert ref_block.text == pack.charts[0].id


def test_markdown_plain_fence_stays_code():
    """Обычный fence без языка chart — по-прежнему Kind.code."""
    from deckdna.ingestion.content_parsers import parse_markdown

    pack = parse_markdown("# T\n\n```python\nprint(1)\n```\n")
    kinds = [b.kind.value for s in pack.sections for b in s.blocks]
    assert "code" in kinds and "chart_ref" not in kinds
    assert not pack.charts


def test_plan_deck_carries_chart_ref():
    """chart_ref-блок из пака доходит до DeckPlan как Kind.chart unit
    с заполненным chart_ref (тем же путём, что table_ref)."""
    from deckdna.ingestion.content_parsers import parse_markdown
    from deckdna.planning.story_director import plan_deck

    md = (
        "# Отчёт\n\n"
        "Вводный абзац.\n\n"
        "```chart id=ch-9\n"
        "categories: Q1, Q2\n"
        "Выручка: 1.0, 2.0\n"
        "```\n"
    )
    pack = parse_markdown(md)
    from deckdna.contracts.deck_plan import Brief

    plan = plan_deck(
        pack,
        Brief(
            purpose="Отчёт",
            audience="Жюри",
            language="ru",
            target_slide_count=10,
        ),
    )
    chart_units = [
        u for s in plan.slides for u in s.content_units if u.chart_ref
    ]
    assert chart_units and chart_units[0].chart_ref == "ch-9"
    assert chart_units[0].kind.value == "chart"


def test_generate_end_to_end_chart_data(tmp_path):
    """Сквозной прогон generate(): markdown ```chart -> выходной pptx
    содержит реальные данные в numCache (не сток)."""
    tpl = _template_with_chart(tmp_path / "tpl.pptx")
    content = tmp_path / "c.md"
    content.write_text(
        "# Показатели\n\n"
        "Итоги года.\n\n"
        "```chart id=ch-1\n"
        "categories: 2021, 2022, 2023\n"
        "Выручка: 4.3, 2.5, 3.5\n"
        "```\n"
    )
    from deckdna.generation.pipeline import Brief, generate

    generate(
        tpl,
        content,
        Brief(
            purpose="Отчёт",
            audience="Жюри",
            language="ru",
            target_slide_count=10,
        ),
        tmp_path / "out",
    )
    deck = tmp_path / "out" / "deck.pptx"
    assert deck.exists()

    pkg = OpcPackage.open(deck)
    found = False
    for xml in _chart_xmls(pkg):
        root = etree.fromstring(xml)
        vals = {v.text for v in root.iter(f"{{{C}}}v")}
        if {"4.3", "2.5", "3.5"} <= vals:
            found = True
    assert found, "chart cache не содержит данных из markdown"


# --- exemplar: capability-aware selection (needs) --------------------


def _template_chart_off_pool(path: Path) -> Path:
    """Шаблон: 2 content-like слайда на layout 6 (dominant pool) +
    1 chart-слайд на layout 5 — вне пула, старый выбор его не видел."""
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches

    prs = Presentation()
    for _ in range(2):
        s = prs.slides.add_slide(prs.slide_layouts[6])
        for i in range(4):
            s.shapes.add_textbox(
                Inches(0.5), Inches(0.3 + i * 0.9), Inches(8), Inches(0.7)
            ).text_frame.text = (
                "Длинный абзац контентного текста про показатели "
                "и результаты проекта, который делает слайд content-like"
            )
    cs = prs.slides.add_slide(prs.slide_layouts[5])
    cd = CategoryChartData()
    cd.categories = ["A", "B", "C"]
    cd.add_series("Стоковый ряд", (1.0, 2.0, 3.0))
    cs.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED,
        Inches(1), Inches(1), Inches(6), Inches(4), cd,
    )
    prs.save(str(path))
    return path


def test_exemplar_needs_prefer_chart_bearing_slide(tmp_path):
    """plan-slide с chart unit -> exemplar с реальным c:chart партом,
    даже если он вне dominant-layout пула (ранее дропалось)."""
    from deckdna.pptx.cloning.exemplar import select_exemplar_slides

    tpl = _template_chart_off_pool(tmp_path / "tpl.pptx")
    pkg = OpcPackage.open(tpl)
    plain = select_exemplar_slides(pkg, 1)
    assert plain[0].slide_part != "ppt/slides/slide3.xml"
    need = select_exemplar_slides(pkg, 1, needs=[{"chart"}])
    assert need[0].slide_part == "ppt/slides/slide3.xml"


def test_exemplar_needs_cycle_distinct_chart_slides():
    """Несколько chart unit -> разные chart-слайды, если шаблон их
    предлагает (lct2026_submission: slides 21-23)."""
    if not SUBMISSION.exists():
        pytest.skip("organizer fixture not present")
    from deckdna.pptx.cloning.exemplar import select_exemplar_slides

    pkg = OpcPackage.open(SUBMISSION)
    picks = select_exemplar_slides(pkg, 3, needs=[{"chart"}] * 3)
    parts = {c.slide_part for c in picks}
    assert parts == {
        "ppt/slides/slide21.xml",
        "ppt/slides/slide22.xml",
        "ppt/slides/slide23.xml",
    }


def test_generate_deck_chart_filled_via_capability_match(tmp_path):
    """e2e: chart unit на выходном слайде реально содержит данные,
    потому что exemplar-выбор притащил chart-несущий слайд."""
    tpl = _template_chart_off_pool(tmp_path / "tpl.pptx")
    report = generate_deck(
        tpl,
        _plan_with_chart_unit(),
        tmp_path / "out.pptx",
        charts=[_mk_chart()],
    )
    slide_rep = report["slides"][0]
    assert slide_rep["exemplar"]["slide_part"] == "ppt/slides/slide3.xml"
    assert slide_rep["charts"]["charts_filled"] == 1
    assert report["dropped_units"].get("chart", 0) == 0

    pkg = OpcPackage.open(tmp_path / "out.pptx")
    vals = set()
    for xml in _chart_xmls(pkg):
        vals |= {v.text for v in etree.fromstring(xml).iter(f"{{{C}}}v")}
    assert {"4.3", "2.5", "3.5"} <= vals


def test_exemplar_needs_prefer_tbl_slide(tmp_path):
    """table unit -> exemplar с нативной a:tbl вне dominant пула."""
    from deckdna.pptx.cloning.exemplar import select_exemplar_slides
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    for _ in range(2):
        s = prs.slides.add_slide(prs.slide_layouts[6])
        for i in range(4):
            s.shapes.add_textbox(
                Inches(0.5), Inches(0.3 + i * 0.9), Inches(8), Inches(0.7)
            ).text_frame.text = (
                "Длинный абзац контентного текста про показатели "
                "и результаты проекта, который делает слайд content-like"
            )
    ts = prs.slides.add_slide(prs.slide_layouts[5])
    ts.shapes.add_table(3, 2, Inches(0.5), Inches(3.0), Inches(8), Inches(2))
    tpl = tmp_path / "tpl.pptx"
    prs.save(str(tpl))

    pkg = OpcPackage.open(tpl)
    picks = select_exemplar_slides(pkg, 1, needs=[{"table"}])
    assert picks[0].slide_part == "ppt/slides/slide3.xml"


def test_exemplar_needs_unsatisfied_falls_back(tmp_path):
    """Ни один слайд шаблона не несёт chart -> обычный пик, unit
    честно дропается ниже по пайплайну (не баг)."""
    from deckdna.pptx.cloning.exemplar import select_exemplar_slides

    tpl = tmp_path / "tpl.pptx"
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[6])
    for i in range(4):
        s.shapes.add_textbox(
            Inches(0.5), Inches(0.3 + i * 0.9), Inches(8), Inches(0.7)
        ).text_frame.text = (
            "Длинный абзац контентного текста про показатели "
            "и результаты проекта, который делает слайд content-like"
        )
    prs.save(str(tpl))
    pkg = OpcPackage.open(tpl)
    picks = select_exemplar_slides(pkg, 1, needs=[{"chart"}])
    assert picks[0].slide_part == "ppt/slides/slide1.xml"


def test_shared_chart_part_unshared_per_output_slide(tmp_path):
    """2 chart unit'а на шаблоне с ОДНИМ chart-слайдом: оба выходных
    слайда клонируют один exemplar -> shared chart-парт дублируется,
    каждый слайд несёт свой набор данных + свой workbook (до фикса
    второй unit честно дропался: shared part = один набор данных)."""
    tpl = _template_with_chart(tmp_path / "tpl.pptx")
    plan = json.loads(PLAN_FIXTURE.read_text())
    plan["slides"] = plan["slides"][:2]
    for i, cid in enumerate(("ch-1", "ch-2")):
        plan["slides"][i]["content_units"] = [
            {"role": "title", "kind": "title", "text": f"Динамика {i}"},
            {"role": "body", "kind": "chart", "chart_ref": cid},
        ]
    charts = [
        _mk_chart(cid="ch-1", series=(("Выручка", (4.3, 2.5, 3.5)),)),
        _mk_chart(
            cid="ch-2",
            categories=("Q1", "Q2", "Q3"),
            series=(("EBITDA", (9.9, 8.8, 7.7)),),
        ),
    ]
    report = generate_deck(
        tpl, plan, tmp_path / "out.pptx", charts=charts
    )
    slides = report["slides"]
    assert slides[0]["charts"]["charts_filled"] == 1
    assert slides[1]["charts"]["charts_filled"] == 1
    assert report["dropped_units"].get("chart", 0) == 0

    pkg = OpcPackage.open(tmp_path / "out.pptx")
    chart_parts = sorted(
        n for n in pkg.parts
        if "charts/chart" in n and n.endswith(".xml")
    )
    assert len(chart_parts) == 2, f"ожидали 2 chart-парта: {chart_parts}"

    # разные rels-таргеты у двух выходных слайдов
    targets = set()
    for i in (1, 2):
        for rel in pkg.rels(f"ppt/slides/slide{i}.xml"):
            if rel.type_name == "chart":
                targets.add(resolve_target(f"ppt/slides/slide{i}.xml", rel.target))
    assert len(targets) == 2

    # каждый chart-парт несёт свои данные
    vals = {}
    for part in chart_parts:
        root = etree.fromstring(pkg.parts[part])
        vals[part] = {v.text for v in root.iter(f"{{{C}}}v")}
    part_vals = list(vals.values())
    assert any({"4.3", "2.5", "3.5"} <= v for v in part_vals)
    assert any({"9.9", "8.8", "7.7"} <= v for v in part_vals)

    # у дупа — свой workbook (не общий с первым)
    wb_targets = set()
    for part in chart_parts:
        for rel in pkg.rels(part):
            if rel.type_name == "package" or rel.target.endswith(".xlsx"):
                wb_targets.add(resolve_target(part, rel.target))
    assert len(wb_targets) == 2 and all(w in pkg.parts for w in wb_targets)

    # пакет валиден — python-pptx открывает оба chart-парта
    from pptx import Presentation

    assert len(Presentation(str(tmp_path / "out.pptx")).slides) == 2


def test_shared_chart_part_no_dup_without_collision(tmp_path):
    """Один chart-юнит без коллизии exemplar'ов — дублирования нет,
    единственный парт штатно заполняется."""
    tpl = _template_with_chart(tmp_path / "tpl.pptx")
    plan = _plan_with_chart_unit()
    report = generate_deck(
        tpl, plan, tmp_path / "out.pptx", charts=[_mk_chart()]
    )
    assert report["slides"][0]["charts"]["charts_filled"] == 1
    pkg = OpcPackage.open(tmp_path / "out.pptx")
    dups = [n for n in pkg.parts if "_dup" in n]
    assert dups == []


# --- From-scratch native chart (OR-004: template без c:chart) ---


def _template_text_only(path: Path) -> Path:
    """Шаблон без единого chart-парта: content-like слайд, только
    текстовые боксы — «unseen» случай OR-004 для chart-юнитов."""
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    for i in range(4):
        slide.shapes.add_textbox(
            Inches(0.5), Inches(0.3 + i * 1.75), Inches(8), Inches(1.7)
        ).text_frame.text = (
            "Длинный абзац контентного текста про показатели "
            "и результаты проекта, который делает слайд content-like"
        )
    prs.save(str(path))
    return path


def test_compose_chart_on_chartless_slide_creates_native(tmp_path):
    """Unseen-шаблон без c:chart + chart-юнит -> НОВЫЙ нативный чарт:
    chart part + embedded workbook + rels + Override + graphicFrame
    в опустевшем теле; данные читаются python-pptx и openpyxl."""
    tpl = _template_text_only(tmp_path / "tpl.pptx")
    chart = _mk_chart(
        categories=("2021", "2022", "2023", "2024"),
        series=(("Выручка", (4.3, 2.5, 3.5, 4.5)), ("Расход", (2.4, 4.4, 1.5, 2.8))),
    )
    report = generate_deck(
        tpl,
        _plan_with_chart_unit(),
        tmp_path / "out.pptx",
        charts=[chart],
    )
    ch = report["slides"][0]["charts"]
    assert ch["charts_created"] == 1 and ch["charts_filled"] == 1
    assert ch["units_dropped"] == 0
    assert ch["created_types"] == ["barChart"]  # typed limitation
    assert "chart" not in report["dropped_units"]

    out = tmp_path / "out.pptx"
    pkg = OpcPackage.open(out)

    # chart-парт + его rels + workbook реально в пакете
    chart_parts = [
        n for n in pkg.parts if n.startswith("ppt/charts/chart") and n.endswith(".xml")
    ]
    assert len(chart_parts) == 1
    chart_part = chart_parts[0]
    wb_targets = {
        resolve_target(chart_part, r.target)
        for r in pkg.rels(chart_part)
        if r.type_name == "package" or r.target.endswith(".xlsx")
    }
    assert wb_targets and all(w in pkg.parts for w in wb_targets)

    # слайд связан с chart-партом rel + graphicFrame в разрешённой зоне
    slide_part = next(
        n for n in pkg.parts if n.startswith("ppt/slides/slide") and n.endswith(".xml")
    )
    slide_chart_rels = [
        r for r in pkg.rels(slide_part)
        if r.type_name == "chart" and resolve_target(slide_part, r.target) == chart_part
    ]
    assert len(slide_chart_rels) == 1
    slide_root = etree.fromstring(pkg.parts[slide_part])
    frame = next(
        f for f in slide_root.iter(f"{{{P}}}graphicFrame")
        if f.find(f".//{{{C}}}chart") is not None
    )
    chart_el = frame.find(f".//{{{C}}}chart")
    assert chart_el.get(
        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    ) == slide_chart_rels[0].id

    # content types: Override и на chart, и на workbook
    ct_root = etree.fromstring(pkg.parts["[Content_Types].xml"])
    overrides = {
        el.get("PartName").lstrip("/"): el.get("ContentType")
        for el in ct_root
        if etree.QName(el).localname == "Override"
    }
    assert overrides[chart_part].endswith("chart+xml")
    assert all(overrides.get(w, "").endswith("spreadsheetml.sheet") for w in wb_targets)

    # native editable: python-pptx читает чарт, кэши несут реальные данные
    from pptx import Presentation

    chart_shape = next(
        s for s in Presentation(str(out)).slides[0].shapes if s.has_chart
    )
    plot = chart_shape.chart.plots[0]
    assert list(plot.categories) == ["2021", "2022", "2023", "2024"]
    assert list(plot.series[0].values) == [4.3, 2.5, 3.5, 4.5]
    assert plot.series[1].name == "Расход"

    # «Edit Data»: workbook консистентен кэшам
    import openpyxl

    wb = openpyxl.load_workbook(BytesIO(pkg.parts[sorted(wb_targets)[0]]))
    ws = wb.active
    assert ws.cell(row=2, column=1).value == "2021"  # строка, не serial-date
    assert ws.cell(row=1, column=2).value == "Выручка"
    assert ws.cell(row=5, column=3).value == 2.8


def test_create_chart_second_unit_uses_second_host(tmp_path):
    """Два chart-юнита на chartless-слайде: оба создаются, в разных
    host-боксах (общий used_hosts с table/diagram/image fallback'ами)."""
    tpl = _template_text_only(tmp_path / "tpl.pptx")
    plan = _plan_with_chart_unit()
    plan["slides"][0]["content_units"].append(
        {"role": "body", "kind": "chart", "chart_ref": "ch-2"}
    )
    report = generate_deck(
        tpl,
        plan,
        tmp_path / "out.pptx",
        charts=[_mk_chart(), _mk_chart(cid="ch-2", series=(("S2", (9.9, 8.8, 7.7)),))],
    )
    ch = report["slides"][0]["charts"]
    assert ch["charts_created"] == 2 and ch["units_dropped"] == 0

    pkg = OpcPackage.open(tmp_path / "out.pptx")
    slide_part = next(
        n for n in pkg.parts if n.startswith("ppt/slides/slide") and n.endswith(".xml")
    )
    root = etree.fromstring(pkg.parts[slide_part])
    frames = [
        f for f in root.iter(f"{{{P}}}graphicFrame")
        if f.find(f".//{{{C}}}chart") is not None
    ]
    boxes = {
        tuple(
            int(xf.get(k))
            for xf in f.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}off")
            for k in ("x", "y")
        )
        for f in frames
    }
    assert len(frames) == 2 and len(boxes) == 2


def test_create_chart_no_host_drops(tmp_path):
    """Сырой slide XML — все тела заняты текстом, host нет -> честный
    drop, не создание (типизированная граница fallback'а)."""
    tpl = _template_text_only(tmp_path / "tpl.pptx")
    with zipfile.ZipFile(tpl) as zf:
        parts = {n: zf.read(n) for n in zf.namelist()}
    slide = "ppt/slides/slide1.xml"
    from deckdna.pptx.composing.chart_fill import fill_slide_charts
    from deckdna.pptx.opc.package import parse_rels, rels_name_for

    rels = parse_rels(parts[rels_name_for(slide)])
    report = fill_slide_charts(
        parts, slide, [_mk_chart()], rels, set(), used_hosts=set()
    )
    assert report.units_dropped == 1 and report.charts_created == 0


def test_create_chart_package_valid_for_audit(tmp_path):
    """Созданный чарт не роняет OOXML-проверки аудита: dangling rels /
    content-type покрытие чисты; единственный флаг — честный
    chart.metadata (в Chart-контракте нет подписей осей — не выдумываем)."""
    tpl = _template_text_only(tmp_path / "tpl.pptx")
    generate_deck(
        tpl,
        _plan_with_chart_unit(),
        tmp_path / "out.pptx",
        charts=[_mk_chart()],
    )
    from deckdna.audit.basic import audit_deck

    issues = audit_deck(tmp_path / "out.pptx")
    rules = {i.rule_code for i in issues}
    assert "package.dangling_rel" not in rules
    assert "package.invalid_xml" not in rules
    # единственный честный флаг — подписи/единицы осей (легенда есть)
    chart_flags = [i for i in issues if i.rule_code == "chart.metadata"]
    assert len(chart_flags) == 1 and "подписей и единиц осей" in chart_flags[0].message


def test_create_chart_empty_categories_valid_ooxml(tmp_path):
    """Категории пусты -> c:cat опускается (валидный OOXML, индексы
    1..n — не выдуманные метки), чарт создаётся и читается."""
    tpl = _template_text_only(tmp_path / "tpl.pptx")
    report = generate_deck(
        tpl,
        _plan_with_chart_unit(),
        tmp_path / "out.pptx",
        charts=[_mk_chart(categories=(), series=(("S", (1.0, 2.0)),))],
    )
    ch = report["slides"][0]["charts"]
    assert ch["charts_created"] == 1 and ch["units_dropped"] == 0
    pkg = OpcPackage.open(tmp_path / "out.pptx")
    chart_part = next(n for n in pkg.parts if n.endswith("chart1.xml"))
    root = etree.fromstring(pkg.parts[chart_part])
    assert root.find(f".//{{{C}}}cat") is None
    assert "$A$1" not in pkg.parts[chart_part].decode()  # нет битого диапазона


def test_create_chart_empty_values_series_skipped_and_dropped(tmp_path):
    """Серия без значений пропускается (series_dropped); все серии
    пустые -> юнит честно дропается, чарт не создаётся."""
    tpl = _template_text_only(tmp_path / "tpl.pptx")
    # все значения пустые -> drop
    rep = generate_deck(
        tpl,
        _plan_with_chart_unit(),
        tmp_path / "out1.pptx",
        charts=[_mk_chart(series=(("S1", ()), ("S2", ())))],
    )
    ch = rep["slides"][0]["charts"]
    assert ch["charts_created"] == 0 and ch["units_dropped"] == 1

    # частично пустые -> создаётся с пропущенной серией
    rep = generate_deck(
        tpl,
        _plan_with_chart_unit(),
        tmp_path / "out2.pptx",
        charts=[_mk_chart(series=(("S1", ()), ("S2", (1.0, 2.0, 3.0))))],
    )
    ch = rep["slides"][0]["charts"]
    assert ch["charts_created"] == 1 and ch["series_dropped"] == 1
    from pptx import Presentation

    shape = next(
        s for s in Presentation(str(tmp_path / "out2.pptx")).slides[0].shapes
        if s.has_chart
    )
    assert [s.name for s in shape.chart.plots[0].series] == ["S2"]


def test_create_chart_length_mismatch_emits_actual_points(tmp_path):
    """Длина категорий/значений не совпадает -> кэши несут фактические
    точки (ptCount=len), дополнение данных не выдумывается, пакет валиден."""
    tpl = _template_text_only(tmp_path / "tpl.pptx")
    report = generate_deck(
        tpl,
        _plan_with_chart_unit(),
        tmp_path / "out.pptx",
        charts=[
            _mk_chart(categories=("A", "B"), series=(("S", (1.0, 2.0, 3.0, 4.0)),))
        ],
    )
    assert report["slides"][0]["charts"]["charts_created"] == 1
    pkg = OpcPackage.open(tmp_path / "out.pptx")
    chart_part = next(n for n in pkg.parts if n.endswith("chart1.xml"))
    root = etree.fromstring(pkg.parts[chart_part])
    val_pts = root.findall(f".//{{{C}}}val//{{{C}}}numCache/{{{C}}}pt")
    cat_pts = root.findall(f".//{{{C}}}cat//{{{C}}}strCache/{{{C}}}pt")
    assert len(val_pts) == 4 and len(cat_pts) == 2
    from pptx import Presentation

    shape = next(
        s for s in Presentation(str(tmp_path / "out.pptx")).slides[0].shapes
        if s.has_chart
    )
    plot = shape.chart.plots[0]
    assert list(plot.categories) == ["A", "B"]
    assert list(plot.series[0].values) == [1.0, 2.0, 3.0, 4.0]


def test_create_chart_points_written_and_min_host(tmp_path):
    """points_written честно считает кэш-точки созданного чарта; бокс
    ниже минимума не принимается за чарт-хост (честный drop)."""
    tpl = _template_text_only(tmp_path / "tpl.pptx")
    chart = _mk_chart(
        categories=("A", "B", "C"), series=(("S", (1.0, 2.0, 3.0)),)
    )
    report = generate_deck(
        tpl, _plan_with_chart_unit(), tmp_path / "out.pptx", charts=[chart]
    )
    ch = report["slides"][0]["charts"]
    assert ch["charts_created"] == 1
    assert ch["points_written"] == 3 + 3  # категории + значения

    # сырой вызов: единственный пустой бокс ниже минимума -> drop
    import zipfile as _zf

    with _zf.ZipFile(tpl) as zf:
        parts = {n: zf.read(n) for n in zf.namelist()}
    slide = "ppt/slides/slide1.xml"
    root = etree.fromstring(parts[slide])
    A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
    for sp in root.iter(f"{{{P}}}sp"):
        tx = sp.find(f".//{{{A_NS}}}t")
        xfrm = sp.find(f".//{{{A_NS}}}xfrm")
        if tx is not None and xfrm is not None and tx.text and len(tx.text) > 40:
            tx.text = ""  # опустевшее тело
            ext = xfrm[1]
            ext.set("cx", "500000")
            ext.set("cy", "500000")  # ~0.55" — ниже минимума чарта
            break
    parts[slide] = etree.tostring(
        root, xml_declaration=True, standalone=True
    )
    from deckdna.pptx.composing.chart_fill import fill_slide_charts
    from deckdna.pptx.opc.package import parse_rels, rels_name_for

    rels = parse_rels(parts[rels_name_for(slide)])
    rep2 = fill_slide_charts(
        parts, slide, [chart], rels, set(), used_hosts=set()
    )
    assert rep2.charts_created == 0 and rep2.units_dropped == 1


def test_created_chart_categories_render_as_strings_not_dates(tmp_path):
    """Render-assert: LibreOffice-PDF содержит '2021' и НЕ serial-date
    '1905' — категории list[str] не уходят в числовые ячейки/numCache."""
    import shutil

    if shutil.which("soffice") is None or shutil.which("pdftotext") is None:
        pytest.skip("soffice/pdftotext not present")
    tpl = _template_text_only(tmp_path / "tpl.pptx")
    out = tmp_path / "out.pptx"
    generate_deck(
        tpl,
        _plan_with_chart_unit(),
        out,
        charts=[
            _mk_chart(
                categories=("2021", "2022", "2023", "2024"),
                series=(("S", (1.0, 2.0, 3.0, 4.0)),),
            )
        ],
    )
    import subprocess

    from deckdna.pptx.exporting.render import render_pdf

    render_pdf(out, tmp_path / "out.pdf")
    text = subprocess.run(  # noqa: S603 — fixed argv, no shell
        [shutil.which("pdftotext"), str(tmp_path / "out.pdf"), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "2021" in text
    assert "1905" not in text
