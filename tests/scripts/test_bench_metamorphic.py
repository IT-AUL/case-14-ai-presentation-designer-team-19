"""Метаморфные инварианты конвейера данных → колода (QA-GATES, §3 задачи).

Без живого API: прямой вызов ``analysis``/``planning``/``generate``.
Продуктовый код здесь не правится: если инвариант падает из-за бага
продукта, тест помечается ``xfail`` с причиной — это зафиксированный
дефект, а не ошибка теста.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest
from deckdna.analysis.pipeline import analyze_pack
from deckdna.contracts.deck_plan import Brief
from deckdna.generation.pipeline import generate
from deckdna.ingestion.content_parsers import parse_file
from deckdna.planning.data_story import plan_data_deck
from deckdna.pptx.composing.chart_build import chart_type_of
from pptx import Presentation

ROOT = Path(__file__).resolve().parents[2]
STAGE2 = ROOT / "examples" / "stage2"
FIXTURES = ROOT / "tests" / "fixtures" / "pptx"
BRIEF = {
    "purpose": "Итоги продаж за два года",
    "audience": "руководство",
    "language": "ru",
    "target_slide_count": 7,
}


def _num(text: str) -> Decimal | None:
    """Числовая часть записи числа из факта («100 ₽» → 100, «4,1» → 4.1)."""
    m = re.match(r"[-+]?\d[\d  \u202f]*(?:[.,]\d+)?", text.strip())
    if not m:
        return None
    return Decimal(re.sub(r"[  \u202f]", "", m.group()).replace(",", "."))


def _facts(analysis):
    """(представление, тип факта, текст) — порядок представлений сохранён."""
    return [(va.view.shape, i.fact_type, i.fact) for va in analysis.views for i in va.insights]


_UNIT_SCALE = {"млрд": Decimal(10) ** 9, "млн": Decimal(10) ** 6, "тыс.": Decimal(1000)}


def _scaled_num(text: str) -> Decimal | None:
    """Число с учётом масштабного суффикса («100 тыс. ₽» → 100 000)."""
    value = _num(text)
    if value is None:
        return None
    for tag, mult in _UNIT_SCALE.items():
        if tag in text:
            return value * mult
    return value


def _write(path: Path, rows: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def decks(tmp_path_factory):
    """Одна и та же таблица → колоды на двух структурно разных шаблонах;
    второй шаблон дополнительно несёт два полностью пустых слайда."""
    tmp = tmp_path_factory.mktemp("metamorphic-decks")
    sparse = tmp / "sparse_with_empty.pptx"
    prs = Presentation(FIXTURES / "synthetic_unseen_sparse.pptx")
    blank = min(prs.slide_layouts, key=lambda layout: len(layout.placeholders))
    for _ in range(2):
        prs.slides.add_slide(blank)
    prs.save(sparse)
    out = {}
    for name, template in (
        ("vk_tech", FIXTURES / "vk_tech_template.pptx"),
        ("unseen_43", FIXTURES / "synthetic_unseen_43.pptx"),
        ("sparse_with_empty", sparse),
    ):
        out[name] = generate(template, STAGE2 / "purchases.csv", dict(BRIEF), tmp / name)
    return out


def test_chart_type_does_not_depend_on_template(decks):
    """Один датасет → одни и те же выбранные типы графиков в плане на любом
    шаблоне, а на шаблонах, способных нести графики, — и в рендере
    (ADR-033 §10, QA-GATES №1)."""
    signatures = []
    for name, report in decks.items():
        planned = [
            u["chart_spec"]["chart_type"]
            for s in report["deck_plan"]["slides"]
            for u in s["content_units"]
            if u["kind"] == "chart"
        ]
        signatures.append(planned)
        if name == "sparse_with_empty":
            continue  # рендер sparse теряет графики — отдельный xfail-тест
        rendered = [
            chart_type_of(shape.chart.part.blob)
            for slide in Presentation(report["artifacts"]["pptx"]).slides
            for shape in slide.shapes
            if getattr(shape, "has_chart", False)
        ]
        assert rendered == planned, name
    assert signatures[0] == signatures[1] == signatures[2], signatures


def test_sparse_template_renders_planned_native_charts(decks):
    """На sparse-шаблоне запланированные графики попадают на слайды."""
    report = decks["sparse_with_empty"]
    planned = [
        u["chart_spec"]["chart_type"]
        for s in report["deck_plan"]["slides"]
        for u in s["content_units"]
        if u["kind"] == "chart"
    ]
    rendered = [
        chart_type_of(shape.chart.part.blob)
        for slide in Presentation(report["artifacts"]["pptx"]).slides
        for shape in slide.shapes
        if getattr(shape, "has_chart", False)
    ]
    assert rendered == planned


def test_currency_scale_1000_preserves_relative_growth(tmp_path):
    """Выручка ×1000: кратности и проценты фактов не меняются, абсолютные
    значения масштабируются ровно в 1000 раз (QA-GATES №2)."""
    rows = ["Месяц;Выручка, ₽"] + [
        f"2025-0{i};{v}" for i, v in enumerate((100, 150, 200, 260, 330, 410), 1)
    ]
    base_csv = _write(tmp_path / "a" / "sales.csv", rows)
    scaled_csv = _write(
        tmp_path / "b" / "sales.csv",
        [rows[0]] + [f"{r.split(';')[0]};{int(r.split(';')[1]) * 1000}" for r in rows[1:]],
    )
    base = analyze_pack(parse_file(base_csv, artifact_id="a"), "динамика выручки")[0]
    scaled = analyze_pack(parse_file(scaled_csv, artifact_id="b"), "динамика выручки")[0]
    assert [v.view.shape for v in base.views] == [v.view.shape for v in scaled.views]
    assert [i.fact_type for va in base.views for i in va.insights] == [
        i.fact_type for va in scaled.views for i in va.insights
    ]
    scaled_abs = invariant_rel = 0
    for va_base, va_scaled in zip(base.views, scaled.views, strict=True):
        for ins_base, ins_scaled in zip(va_base.insights, va_scaled.insights, strict=True):
            for n_base, n_scaled in zip(ins_base.numbers, ins_scaled.numbers, strict=True):
                d_base, d_scaled = _scaled_num(n_base), _scaled_num(n_scaled)
                assert d_base is not None and d_scaled is not None
                if d_scaled == d_base:
                    invariant_rel += 1  # кратность/процент — инварианты масштаба
                elif d_scaled == d_base * 1000:
                    scaled_abs += 1  # абсолютное значение — масштабируется
                else:
                    pytest.fail(f"{n_base!r} → {n_scaled!r}: ни инвариант, ни ×1000")
    assert invariant_rel and scaled_abs


def test_row_permutation_preserves_order_independent_facts(tmp_path):
    """Перестановка строк не меняет факты (доли, лидеры, итоги, ряды —
    сортировка по периоду внутри анализа) (QA-GATES №3)."""
    rows = [
        "Канал;Доля",
        "Сайт;38.5",
        "Маркетплейс;24.1",
        "Розница;19.7",
        "Соцсети;10.2",
        "Прочее;7.5",
    ]
    first = _write(tmp_path / "a" / "channels.csv", rows)
    shuffled = _write(tmp_path / "b" / "channels.csv", [rows[0], *reversed(rows[1:])])
    series = _write(
        tmp_path / "a" / "months.csv",
        ["Месяц;Продажи"] + [f"2025-0{i};{i * 10}" for i in range(1, 7)],
    )
    series_perm = _write(
        tmp_path / "b" / "months.csv",
        ["Месяц;Продажи"] + [f"2025-0{i};{i * 10}" for i in (3, 1, 6, 2, 5, 4)],
    )
    for plain, permuted in ((first, shuffled), (series, series_perm)):
        a = analyze_pack(parse_file(plain, artifact_id="p"), "обзор данных")[0]
        b_ = analyze_pack(parse_file(permuted, artifact_id="q"), "обзор данных")[0]
        assert sorted(map(str, _facts(a))) == sorted(map(str, _facts(b_)))


def test_objective_changes_task_and_chart_not_computed_values(tmp_path):
    """Смена цели меняет задачу/выбор графика/порядок слайдов, но не
    вычисленные факты (QA-GATES №4)."""
    csv = _write(
        tmp_path / "dyn.csv",
        ["Месяц;Канал;Продажи"]
        + [
            f"{m};{c};{v}"
            for m, c, v in (
                ("2025-01", "Сайт", 10),
                ("2025-01", "Маркет", 8),
                ("2025-02", "Сайт", 12),
                ("2025-02", "Маркет", 9),
                ("2025-03", "Сайт", 15),
                ("2025-03", "Маркет", 9),
                ("2025-04", "Сайт", 18),
                ("2025-04", "Маркет", 11),
                ("2025-05", "Сайт", 20),
                ("2025-05", "Маркет", 10),
                ("2025-06", "Сайт", 25),
                ("2025-06", "Маркет", 12),
            )
        ],
    )
    briefs = ("Показать динамику продаж", "Показать доли каналов в структуре продаж")
    analyses = [
        analyze_pack(parse_file(csv, artifact_id=f"obj-{i}"), text)[0]
        for i, text in enumerate(briefs)
    ]
    plans = [
        plan_data_deck(
            parse_file(csv, artifact_id=f"plan-{i}"),
            Brief(
                purpose=text,
                audience="руководство",
                language="ru",
                target_slide_count=6,
                content_fill="shrink",
            ),
        )
        for i, text in enumerate(briefs)
    ]

    # задача/выбор изменились
    def signature(a):
        return [(v.view.shape, v.task.task, v.rec.chosen.chart_type) for v in a.views]

    assert signature(analyses[0]) != signature(analyses[1])
    plan_signature = [
        (s.purpose.value, (s.chart or {}).get("type"))
        for p in plans
        for s in p.slides
        if s.purpose.value == "data"
    ]
    assert plan_signature[0] != plan_signature[1]
    # вычисленные значения — те же самые факты на каждом представлении;
    # ключ — структурная роль представления (id содержит хеш датасета)
    facts_by_view = [
        {(va.view.shape, va.view.title): sorted(i.fact for i in va.insights) for va in a.views}
        for a in analyses
    ]
    assert facts_by_view[0] == facts_by_view[1]


def test_empty_template_slides_do_not_add_empty_output_slides(decks):
    """Пустые слайды-образцы в шаблоне не порождают пустых слайдов
    в готовой колоде (QA-GATES №5)."""
    report = decks["sparse_with_empty"]
    prs = Presentation(report["artifacts"]["pptx"])
    empty = []
    for i, slide in enumerate(prs.slides, 1):
        has_text = any(
            getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip()
            for shape in slide.shapes
        )
        has_visual = any(
            getattr(shape, "has_chart", False)
            or getattr(shape, "has_table", False)
            or shape.shape_type == 13  # picture
            for shape in slide.shapes
        )
        if not (has_text or has_visual):
            empty.append(i)
    assert empty == [], f"пустые слайды в выходе: {empty}"
    assert len(prs.slides) == len(report["deck_plan"]["slides"])
