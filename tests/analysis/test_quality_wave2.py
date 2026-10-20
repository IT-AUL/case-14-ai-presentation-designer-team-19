"""Регрессии волны качества LEAD-BE: честные выводы, задачи, графики.

- равные/почти равные доли не дают ложного «лидера»/«аутсайдера»;
- «в задаче сказано про …» — в правильном падеже;
- «Топ-3 категории …» вместо «Три крупнейших «Регион»»;
- круговая отклоняется при равномерных долях и «прочих» > 25%;
- производная «доля от итога» не строится для равномерного разреза и
  для счёта записей по категориям (entity-каталоги);
- метрики разных порядков не делят одну ось без единиц;
- KPI-слайд получает заголовок-вывод, а не ярлык «Ключевые показатели»;
- обложка не начинается с императива «показать …»;
- у слайда с данными есть «so what»-пункт и человеческие заметки.
"""

from __future__ import annotations

from pathlib import Path

from deckdna.analysis import analyze_dataset
from deckdna.analysis.profile import profile_dataset
from deckdna.analysis.task import score_tasks
from deckdna.analysis.views import build_views
from deckdna.contracts.deck_plan import Brief, Purpose
from deckdna.ingestion.content_parsers import parse_file
from deckdna.ingestion.tabular import read_csv
from deckdna.planning.data_story import cover_title_text, plan_data_deck

ROOT = Path(__file__).resolve().parents[2]
STAGE2 = ROOT / "examples" / "stage2"


def _csv(text: str, name: str = "x.csv"):
    return read_csv(text.encode(), name)


def _brief(purpose: str = "Итоги", target: int = 7) -> Brief:
    return Brief(purpose=purpose, audience="руководство",
                 language="ru", target_slide_count=target)


def test_equal_percent_shares_have_no_leader_or_tail():
    ds = _csv("Канал;Доля\n" + "".join(f"К{i};{100 / 6:.2f}%\n" for i in range(6)))
    analysis = analyze_dataset(ds, "какие доли")
    cat = next(v for v in analysis.views if v.view.shape == "categorical")
    types = [i.fact_type for i in cat.insights]
    assert "leader" not in types and "tail" not in types
    assert cat.insights[0].fact_type == "even"
    assert "Равномерное распределение" in cat.insights[0].fact


def test_equal_absolute_shares_have_no_leader():
    ds = _csv("Регион;Выручка\n" + "".join(f"Р{i};100\n" for i in range(5)))
    analysis = analyze_dataset(ds)
    cat = next(v for v in analysis.views if v.view.shape == "categorical")
    assert [i.fact_type for i in cat.insights] == ["even"]
    assert "поровну" in cat.insights[0].fact


def test_near_equal_shares_do_not_claim_leader():
    ds = _csv("Канал;Доля\nA;33%\nB;33%\nC;34%\n")
    analysis = analyze_dataset(ds)
    cat = next(v for v in analysis.views if v.view.shape == "categorical")
    assert all(i.fact_type != "leader" for i in cat.insights)


def test_intent_reason_uses_correct_case():
    ds = _csv("Месяц;Выручка\n2025-01;10\n2025-02;11\n2025-03;12\n")
    prof = profile_dataset(ds)
    [view] = [v for v in build_views(ds, prof) if v.shape == "series"]
    task = score_tasks(view, "покажи динамику продаж")
    assert any("про динамику" in r for r in task.reasons)
    assert not any("про динамика" in r for r in task.reasons)


def test_top3_wording_is_grammatical():
    ds = _csv(
        "Город;Выручка\n"
        + "".join(f"Город{i};{v}\n" for i, v in enumerate((40, 30, 20, 5, 3, 2)))
    )
    analysis = analyze_dataset(ds)
    cat = next(v for v in analysis.views if v.view.shape == "categorical")
    fact = next(i for i in cat.insights if i.fact_type == "concentration")
    assert "Топ-3" in fact.fact
    assert "Три крупнейших" not in fact.fact


def test_uniform_distribution_rejects_pie():
    ds = _csv("Канал;Продажи\n" + "".join(f"К{i};25\n" for i in range(4)))
    analysis = analyze_dataset(ds)
    cat = next(v for v in analysis.views if v.view.shape == "categorical")
    pie = next((r for r in cat.rec.rejected if r.chart_type == "pie"), None)
    assert pie is not None and "равн" in pie.reasons[0]


def test_pie_rejected_when_others_would_exceed_quarter():
    # 9 категорий: топ-5 ≈ 63%, хвост из 4 ≈ 37% > 25%
    vals = [20, 15, 12, 9, 7, 6, 6, 6, 6]
    ds = _csv("Сегмент;Доля\n" + "".join(f"S{i};{v}\n" for i, v in enumerate(vals)))
    analysis = analyze_dataset(ds, "структура по сегментам")
    cat = next(v for v in analysis.views if v.view.shape == "categorical")
    ranked = {r.chart_type for r in cat.rec.ranked}
    assert "pie" not in ranked and "doughnut" not in ranked


def test_no_derived_share_for_uniform_or_counts():
    # равномерный разрез
    ds = _csv("Регион;Выручка\n" + "".join(f"Р{i};100\n" for i in range(4)))
    analysis = analyze_dataset(ds)
    assert not any(v.view.derived for v in analysis.views)
    # entity-каталог: счёт записей, «доля» повторяла бы тот же график
    text = "Артикул;Название;Категория;Цена;Остаток\n" + "".join(
        f"SKU-{i};Товар {i};Кат{i % 3};{100 + i};{i}\n" for i in range(220)
    )
    ds = _csv(text)
    analysis = analyze_dataset(ds)
    assert not any(v.view.derived for v in analysis.views)


def test_incomparable_magnitudes_do_not_share_an_axis():
    ds = _csv(
        "Месяц;Выручка;Заказы\n"
        + "".join(f"2025-{m:02d};{2_000_000 + m * 1000};{300 + m}\n" for m in range(1, 13))
    )
    analysis = analyze_dataset(ds)
    trends = [v for v in analysis.views if v.view.shape == "series"]
    assert len(trends) >= 2, "метрики разных порядков должны давать разные графики"
    assert all(len(v.view.series) == 1 for v in trends)


def test_kpi_slide_title_is_a_conclusion(tmp_path):
    f = tmp_path / "kpi.csv"
    f.write_text("Показатель;Значение\nВыручка, млн ₽;4,8\nЗаказы, тыс.;1,2\n",
                 encoding="utf-8")
    plan = plan_data_deck(parse_file(f), _brief())
    kpi = next(s for s in plan.slides if (s.chart or {}).get("type") == "kpi")
    assert kpi.title_intent != "Ключевые показатели"
    assert "4,8" in kpi.title_intent or "Выручка" in kpi.title_intent


def test_cover_title_strips_instruction_verbs():
    assert cover_title_text("Покажите мне динамику продаж") == "Динамика продаж"
    assert cover_title_text("показать структуру канала") == "Структура канала"
    assert cover_title_text("Итоги продаж за два года") == "Итоги продаж за два года"
    assert cover_title_text("make report") == "Report"
    assert cover_title_text("") is None


def test_data_slide_has_so_what_and_human_notes():
    pack = parse_file(STAGE2 / "sales_monthly.csv")
    plan = plan_data_deck(pack, _brief("Итоги продаж"))
    data = [s for s in plan.slides if s.purpose == Purpose.data]
    assert data
    for slide in data:
        texts = [u.text for u in slide.content_units if u.text]
        assert not any("Данные:" in t for t in texts)
        note = slide.speaker_note or ""
        assert "Данные:" not in note and "Альтернативы" not in note
        assert note.strip(), "заметка докладчика должна быть человеческим текстом"
    first = data[0]
    bullets = [u.text for u in first.content_units if u.role == "bullet"]
    assert any(
        "Стоит" in b or "вывод" in b.casefold() or "драйвер" in b.casefold() for b in bullets
    ), "у слайда с данными должен быть пункт «so what»"


def test_cover_title_has_no_instruction_verb():
    pack = parse_file(STAGE2 / "sales_monthly.csv")
    plan = plan_data_deck(pack, _brief("Покажите динамику продаж"))
    title = next(s for s in plan.slides if s.purpose == Purpose.title)
    assert not title.title_intent.casefold().startswith(("покажите", "показать"))
    assert "Динамика" in title.title_intent
