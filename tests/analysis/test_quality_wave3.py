"""Регрессии волны качества LEAD-BE (раунд 2):

- у мультирядного ряда факты считаются по каждой серии — заголовок берёт
  сильнейшую, а не первую;
- производные представления не вытесняют основные при тесном бюджете,
  но занимают свободные места;
- слайды одного датасета идут подряд, блок KPI — первым после выводов;
- «Ключевые выводы» — не больше одного вывода на показатель;
- инфинитив после двоеточия не попадает в заголовок обложки;
- хвост «Остальные» больше лидера не становится столбцом — уходит в сноску;
- мост отклонений распознаётся и получает водопад;
- сломанная тема (фон == текст) не даёт невидимых подписей.
"""

from __future__ import annotations

from pathlib import Path

from deckdna.analysis import analyze_dataset
from deckdna.analysis.shape import shape_for_type
from deckdna.contracts.deck_plan import Brief, Purpose
from deckdna.ingestion.content_parsers import parse_file
from deckdna.ingestion.merge import merge_packs
from deckdna.ingestion.tabular import read_csv
from deckdna.planning.data_story import cover_title_text, plan_data_deck

ROOT = Path(__file__).resolve().parents[2]
STAGE2 = ROOT / "examples" / "stage2"


def _csv(text: str, name: str = "x.csv"):
    return read_csv(text.encode(), name)


def _brief(purpose: str = "Итоги", target: int = 7) -> Brief:
    return Brief(purpose=purpose, audience="руководство", language="ru", target_slide_count=target)


def test_each_series_gets_its_span_fact():
    ds = _csv(
        "Квартал;Ушедшие клиенты;Новые клиенты\n"
        + "".join(f"2025-0{q};{400 + q};{400 + 100 * q}\n" for q in range(1, 5))
    )
    analysis = analyze_dataset(ds)
    series_view = next(v for v in analysis.views if v.view.shape == "series")
    facts = [i.fact for i in series_view.insights]
    assert any("Новые клиенты" in f for f in facts)
    assert any("Ушедшие клиенты" in f for f in facts)
    # заголовок — сильнейший факт: рост новых, а не плоский отток
    assert "Новые клиенты" in (series_view.headline or "")


def test_derived_view_yields_when_budget_is_tight():
    pack = merge_packs(
        [
            parse_file(STAGE2 / "regions.xlsx"),
            parse_file(STAGE2 / "sales_monthly.csv"),
            parse_file(STAGE2 / "kpi.json"),
        ]
    )
    plan = plan_data_deck(pack, _brief(target=6))
    data = [s for s in plan.slides if s.purpose == Purpose.data]
    view_ids = [(s.chart or {}).get("view_id", "") for s in data]
    assert len(data) <= 4
    # при дефиците мест производный срез «доля от итога» уступает основным
    assert not any(v.endswith("-share") for v in view_ids)


def test_derived_view_kept_when_budget_allows():
    pack = parse_file(STAGE2 / "regions.xlsx")
    plan = plan_data_deck(pack, _brief(target=8))
    data = [s for s in plan.slides if s.purpose == Purpose.data]
    assert len(data) >= 2


def test_kpi_leads_and_dataset_slides_are_grouped():
    pack = merge_packs(
        [
            parse_file(STAGE2 / "regions.xlsx"),
            parse_file(STAGE2 / "sales_monthly.csv"),
            parse_file(STAGE2 / "kpi.json"),
        ]
    )
    plan = plan_data_deck(pack, _brief(target=9))
    data = [s for s in plan.slides if s.purpose == Purpose.data]
    assert data and (data[0].chart or {}).get("type") == "kpi"
    # слайды одного датасета — подряд: извлекаем id датасета из evidence
    order = []
    for s in data:
        ev = next((e for e in s.evidence_ids if ":ds-" in e), "")
        order.append(ev.rsplit(":", 1)[-1])
    seen = []
    for ds_id in order:
        if not seen or seen[-1] != ds_id:
            seen.append(ds_id)
    assert len(seen) == len(set(seen)), f"датасеты перемешаны: {order}"


def test_key_findings_do_not_repeat_one_metric():
    pack = parse_file(STAGE2 / "sales_monthly.csv")
    plan = plan_data_deck(pack, _brief(target=8))
    key = next((s for s in plan.slides if s.title_intent == "Ключевые выводы"), None)
    assert key is not None
    subjects = [
        (u.text or "").split(":", 1)[0].strip().casefold()
        for u in key.content_units
        if u.role == "bullet"
    ]
    assert len(subjects) == len(set(subjects)), subjects


def test_cover_title_drops_tail_instruction():
    assert cover_title_text("Отчёт по продажам: показать динамику") == "Отчёт по продажам"
    assert cover_title_text("Итоги — сделать выводы") == "Итоги"
    assert cover_title_text("Отчёт: результаты Q3") == "Отчёт: результаты Q3"


def test_oversized_others_moves_to_the_footnote():
    from deckdna.contracts.content_pack import Chart, ChartSeries, SourceRef

    chart = Chart(
        id="c",
        categories=[f"К{i}" for i in range(15)],
        series=[ChartSeries(name="Выручка", values=[200, 150] + [80] * 13)],
        source_ref=SourceRef(artifact_id="x"),
        chart_type="bar",
    )
    shaped = shape_for_type(chart, "bar")
    assert "Остальные" not in shaped.categories
    assert "всего" in (shaped.source_note or "")
    # хвост не крупнее самой мелкой показанной категории — агрегат
    # остаётся строкой-столбцом («Остальные (3)» = 3 ≤ 3)
    small_tail = chart.model_copy(
        update={
            "categories": [f"К{i}" for i in range(13)],
            "series": [
                ChartSeries(name="Выручка", values=[100, 50, 40, 30, 20, 10, 5, 4, 3, 3, 1, 1, 1])
            ],
        }
    )
    shaped2 = shape_for_type(small_tail, "bar")
    assert shaped2.categories[-1] == "Остальные (3)"


def test_bridge_view_chooses_waterfall():
    ds = _csv("Фактор;Значение\nБыло;100\nЦена;30\nОбъём;15\nИздержки;-25\nСтало;120\n")
    analysis = analyze_dataset(ds)
    cat = next(v for v in analysis.views if v.view.shape == "categorical")
    assert cat.view.bridge
    assert cat.rec.chosen.chart_type == "waterfall"
    # категории в исходном порядке, итоговая строка сохранена
    assert cat.view.categories == ["Было", "Цена", "Объём", "Издержки", "Стало"]
    facts = [i.fact for i in cat.insights]
    assert any("со 100" in f or "с 100" in f for f in facts)
    assert any("драйвер" in (i.headline or "") or "фактор" in i.fact for i in cat.insights)


def test_non_bridge_never_gets_waterfall():
    ds = _csv("Регион;Выручка\nМосква;40\nКазань;12\nПермь;8\n")
    analysis = analyze_dataset(ds)
    cat = next(v for v in analysis.views if v.view.shape == "categorical")
    assert not cat.view.bridge
    assert all(r.chart_type != "waterfall" for r in cat.rec.ranked)


def test_waterfall_xml_is_stacked_with_hidden_base():
    from deckdna.contracts.content_pack import Chart, ChartSeries, SourceRef
    from deckdna.pptx.composing.chart_build import build_typed_chart_xml, typed_workbook

    chart = Chart(
        id="c",
        categories=["Было", "Цена", "Объём", "Стало"],
        series=[ChartSeries(name="Выручка", values=[100.0, 30.0, -10.0, 120.0])],
        source_ref=SourceRef(artifact_id="x"),
        chart_type="waterfall",
    )
    xml = build_typed_chart_xml(chart, None).decode()
    assert '<c:grouping val="stacked"/>' in xml
    assert '<c:overlap val="100"/>' in xml
    assert "<a:noFill/>" in xml  # невидимая база
    assert '<c:legendEntry><c:idx val="0"/><c:delete val="1"/></c:legendEntry>' in xml
    # декомпозиция: база фактора «Объём» = min(100+30, 100+30-10) = 120
    import re as _re

    nums = _re.findall(r"<c:pt idx=\"2\"><c:v>([\d.]+)</c:v>", xml)
    assert "120" in nums and "10" in nums
    book = typed_workbook(chart)
    assert len(book) > 1000  # книга с 4 синтетическими рядами


def test_ink_falls_back_when_theme_matches_background():
    from deckdna.pptx.composing.chart_colors import chart_colors

    A_ = "http://schemas.openxmlformats.org/drawingml/2006/main"
    P_ = "http://schemas.openxmlformats.org/presentationml/2006/main"
    R_ = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    theme = (
        f'<a:theme xmlns:a="{A_}"><a:themeElements><a:clrScheme name="x">'
        '<a:dk1><a:srgbClr val="0B1F3A"/></a:dk1><a:lt1><a:srgbClr val="0B1F3A"/></a:lt1>'
        '<a:dk2><a:srgbClr val="12264A"/></a:dk2><a:lt2><a:srgbClr val="0E2A50"/></a:lt2>'
        + "".join(f'<a:accent{i}><a:srgbClr val="3366{i}{i}"/></a:accent{i}>' for i in range(1, 7))
        + '<a:hlink><a:srgbClr val="0000FF"/></a:hlink>'
        '<a:folHlink><a:srgbClr val="800080"/></a:folHlink></a:clrScheme>'
        '<a:fontScheme name="x"><a:majorFont><a:latin typeface=""/></a:majorFont>'
        '<a:minorFont><a:latin typeface=""/></a:minorFont></a:fontScheme>'
        '<a:fmtScheme name="x"/></a:themeElements></a:theme>'
    ).encode()
    master = (
        f'<p:sldMaster xmlns:p="{P_}" xmlns:a="{A_}">'
        '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" '
        'accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" '
        'accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
        f'<p:cSld><p:bg><p:bgPr><a:solidFill><a:schemeClr val="bg1"/></a:solidFill>'
        "<a:effectLst/></p:bgPr></p:bg><p:spTree>"
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        "<p:grpSpPr/></p:spTree></p:cSld></p:sldMaster>"
    ).encode()
    parts = {
        "ppt/slides/slide1.xml": (
            f'<p:sld xmlns:p="{P_}" xmlns:a="{A_}"><p:cSld><p:spTree>'
            '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            "<p:grpSpPr/></p:spTree></p:cSld></p:sld>"
        ).encode(),
        "ppt/slides/_rels/slide1.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{R_}/slideLayout" '
            'Target="../slideLayouts/slideLayout1.xml"/>'
            "</Relationships>"
        ).encode(),
        "ppt/slideLayouts/slideLayout1.xml": (
            f'<p:sldLayout xmlns:p="{P_}" xmlns:a="{A_}"><p:cSld><p:spTree>'
            '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            "<p:grpSpPr/></p:spTree></p:cSld></p:sldLayout>"
        ).encode(),
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{R_}/slideMaster" '
            'Target="../slideMasters/slideMaster1.xml"/>'
            "</Relationships>"
        ).encode(),
        "ppt/slideMasters/slideMaster1.xml": master,
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{R_}/theme" Target="../theme/theme1.xml"/>'
            "</Relationships>"
        ).encode(),
        "ppt/theme/theme1.xml": theme,
    }
    colors = chart_colors(parts, "ppt/slides/slide1.xml")
    assert colors.ink == "FFFFFF"


def test_so_what_present_even_when_meaning_exists():
    pack = parse_file(STAGE2 / "sales_monthly.csv")
    plan = plan_data_deck(pack, _brief("Итоги продаж"))
    for slide in plan.slides:
        if slide.purpose != Purpose.data:
            continue
        texts = [u.text for u in slide.content_units if u.role == "bullet"]
        assert any("Стоит" in t or "нужн" in t.casefold() for t in texts), slide.title_intent
