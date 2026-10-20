"""Тесты правил планирования истории и подбора архетипов."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from deckdna.planning import story_rules as sr

HERE = Path(__file__).parent


# ── A. морфология ───────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "ratio,expected",
    [
        (2.0, "в 2 раза"),
        (5.0, "в 5 раз"),
        (12.0, "в 12 раз"),
        (1.5, "в 1,5 раза"),
        (2.1, "в 2,1 раза"),
        (12.4, "в 12,4 раза"),
        (22.0, "в 22 раза"),
    ],
)
def test_times_phrase(ratio, expected):
    assert sr.times_phrase(ratio) == expected


@pytest.mark.parametrize(
    "metric,gender",
    [
        ("Выручка", "f"),
        ("Выручка B2B", "f"),
        ("Прибыль", "f"),
        ("Конверсия", "f"),
        ("Стоимость", "f"),
        ("Оклад", "m"),
        ("Средний чек", "m"),
        ("Новые клиенты", "p"),
        ("Продажи", "p"),
        ("Число записей", "n"),
        ("NPS", "m"),
        ("ROI", "m"),
    ],
)
def test_head_gender(metric, gender):
    assert sr.head_gender(metric) == gender


def test_signed_pct_has_one_sign():
    assert sr.fmt_signed_pct(-79) == "\u221279%"
    assert sr.fmt_signed_pct(5) == "+5%"
    assert "+-" not in sr.fmt_signed_pct(-79)


# ── B. формулы ──────────────────────────────────────────────────────────────
def test_delta_kinds():
    d = sr.delta(100, 210)
    assert d.kind == "growth" and round(d.ratio, 2) == 2.1 and round(d.pct) == 110
    assert sr.delta(100, 103).kind == "flat"
    assert sr.delta(100, 40).kind == "decline"
    assert sr.delta(0, 50).kind == "undefined" and sr.delta(0, 50).pct is None
    assert sr.delta(-10, 5).kind == "undefined"


def test_cagr():
    assert round(sr.cagr(100, 121, 3 + 1 + 0) or 0, 2) == round(
        ((121 / 100) ** (1 / 3) - 1) * 100, 2
    )
    assert sr.cagr(100, 121, 3) is None  # < 4 точек
    assert sr.cagr(0, 10, 12) is None


def test_trend_character():
    assert sr.trend_character([1, 2, 3, 4, 5, 6]) == "steady"
    assert sr.trend_character([5, 1, 6, 2, 7, 1]) == "choppy"
    assert sr.trend_character([1, 2]) == "unknown"


def test_share_stats_pareto():
    # 2 из 10 позиций дают ≥ 80% → Pareto
    pairs = [("A", 500), ("B", 330), *[(f"x{i}", 21) for i in range(8)]]
    st = sr.share_stats(pairs)
    assert st.leader == "A" and round(st.leader_share) == 50
    assert st.pareto_k == 2 and st.pareto_n == 10 and st.pareto_holds
    assert round(st.cum[1]) == 83
    assert round(st.gap_pp) == 17 and round(st.lead_ratio, 2) == round(500 / 330, 2)
    assert round(st.top3_share) == 85
    assert 0 < st.hhi < 1 and round(st.effective_n, 1) == round(1 / st.hhi, 1)
    even = sr.share_stats([(str(i), 10) for i in range(10)])
    assert even.pareto_k == 8 and not even.pareto_holds
    assert sr.share_stats([("a", 5), ("b", -1)]) is None


def test_contributions_sum_to_100():
    out = sr.contributions({"A": 100, "B": 100, "C": 50}, {"A": 180, "B": 90, "C": 80})
    assert out[0][0] == "A" and round(out[0][2]) == round(80 / 100 * 100)
    assert round(sum(p for _, _, p in out)) == 100
    assert sr.contributions({"A": 1}, {"A": 1})[0][2] == 0.0


def test_is_real_time_series():
    # people.csv: 12 человек, «Дата найма» неравномерна, ФИО уникальны
    assert not sr.is_real_time_series(
        n_rows=12, n_buckets=9, time_regular=False, has_entity_key=True
    )
    # purchases.csv: 8 755 транзакций за 24 месяца
    assert sr.is_real_time_series(
        n_rows=8755, n_buckets=24, time_regular=False, has_entity_key=False
    )
    # sales_monthly.csv: 24 строки, регулярный месяц
    assert sr.is_real_time_series(n_rows=24, n_buckets=24, time_regular=True, has_entity_key=False)
    assert not sr.is_real_time_series(
        n_rows=2, n_buckets=2, time_regular=True, has_entity_key=False
    )


# ── C. заголовки ────────────────────────────────────────────────────────────
BAD_TITLES = [
    "Чистый шаблон",
    "Справочная таблица: 3 позиции",
    "Справочная таблица (3 позиции): Человек, Роль, Команда, Город, Руководитель",
    "Справочная таблица: даны 2-3 гипотезы",
    "Text11",
    "purchases.csv",
    "Динамика выручки",
    "Ключевые показатели",
    "Данные по регионам",
    "Бонус: снижение на 63% за 9 месяцев",  # ярлык с двоеточием — текущий вывод бэкенда
    "Выручка B2B выросла:",
]


@pytest.mark.parametrize("title", BAD_TITLES)
def test_bad_titles_rejected(title):
    v = sr.validate_action_title(title)
    assert not v.ok, (title, v)


GOOD_TITLES = [
    "Выручка B2B выросла в 2,1 раза за 12 месяцев — главный вклад: ритейл",
    "Тверь формирует 61% суммы по полю «Оклад»",
    "Продажи опережают «Маркетинг» в 2,5 раза",
    "3 из 10 позиций дают 83% выручки",
    "Конверсия выросла в 1,5 раза за 12 месяцев",
]


@pytest.mark.parametrize("title", GOOD_TITLES)
def test_good_titles_accepted(title):
    v = sr.validate_action_title(title)
    assert v.ok, (title, v)


def test_invented_number_rejected():
    v = sr.validate_action_title(
        "Выручка выросла в 3 раза за 12 месяцев", allowed_numbers={"2.1", "12"}
    )
    assert not v.ok and any("не из фактов" in r for r in v.reasons)


def test_numbers_in_normalisation():
    assert sr.numbers_in("с 1\u00a0234,5 до \u221279%") >= {"1234.5", "-79"}


def test_title_trend_templates():
    g = sr.delta(1000, 2100)
    t = sr.title_trend("Выручка B2B", g, 12, "month", driver="ритейл")
    assert t == "Выручка B2B выросла в 2,1 раза за 12 месяцев — главный вклад: ритейл"
    assert sr.validate_action_title(t, {"2.1", "12"}).ok
    assert (
        sr.title_trend("Продажи", sr.delta(100, 160), 6, "quarter")
        == "Продажи выросли в 1,6 раза за 6 кварталов"
    )
    assert (
        sr.title_trend("Бонус", sr.delta(100, 60), 9, "month")
        == "Бонус снизился на 40% за 9 месяцев"
    )
    assert (
        sr.title_trend("Возвраты", sr.delta(100, 40), 9, "month")
        == "Возвраты снизились в 2,5 раза за 9 месяцев"
    )
    assert (
        sr.title_trend("Прибыль", sr.delta(100, 102), 4, "quarter")
        == "Прибыль осталась на уровне 102 за 4 квартала"
    )
    assert sr.title_trend("Выручка", sr.delta(0, 10), 4, "year") is None


def test_title_share_and_gap():
    t = sr.title_share("Регион", "Тверь", 61.0, "суммы по полю «Оклад»")
    assert (
        t == "Регион «Тверь» формирует 61% суммы по полю «Оклад»" and sr.validate_action_title(t).ok
    )
    assert sr.title_share("Каналы", "Сайт", 45, "выручки") == "«Сайт» — 45% выручки"
    assert (
        sr.title_share("Канал", "Сайт", 38.5, "", percent_given=True)
        == "Канал «Сайт» занимает 38% — крупнейшая доля"
    )
    pairs = [("A", 500), ("B", 330), *[(f"x{i}", 21) for i in range(8)]]
    p = sr.title_share("Регион", "A", 50, "выручки", sr.share_stats(pairs))
    assert p == "2 из 10 позиций дают 83% выручки"
    assert sr.validate_action_title(p).ok
    assert sr.validate_action_title(sr.title_gap("Продажи", "Маркетинг", 2.5)).ok


def test_title_value_split():
    t = sr.title_value_split("Статус", "идёт", 2, 3)
    assert t == "У 2 из 3 позиций статус — «идёт»" and sr.validate_action_title(t).ok


def test_leader_is_meaningful():
    even = sr.share_stats([(f"p{i}", 10 + (i % 3)) for i in range(11)])  # ~9% у лидера
    assert not sr.leader_is_meaningful(even)
    skew = sr.share_stats([("A", 61), ("B", 20), ("C", 19)])
    assert sr.leader_is_meaningful(skew)


# ── D. so_what ──────────────────────────────────────────────────────────────
def test_so_what_uses_only_given_numbers_and_names():
    s = sr.so_what("growth", driver="Ритейл", driver_pct=62.4)
    assert s == "Усилить «Ритейл»: на него приходится 62% прироста"
    assert (
        sr.so_what("leader", driver="Москва", share=61)
        == "Снизить зависимость от «Москва» — на него 61% результата"
    )
    assert sr.so_what("concentration", top_k=2, top_share=83, tail_n=8).startswith(
        "Защитить топ-2 (83%"
    )
    assert sr.so_what("growth") is None  # нет слотов — нет выдумки
    assert (
        sr.so_what("correlation", r=0.84)
        == "Проверить причинность связи (r = 0,84) пилотом до пересмотра бюджета"
    )


# ── E. таблицы ──────────────────────────────────────────────────────────────
def test_max_columns():
    assert sr.max_columns(10_500_000) == 4  # 16:9 на всю ширину: cap=4 (не 5)
    assert sr.max_columns(7_000_000) == 3
    assert sr.max_columns(5_400_000) == 3
    assert sr.max_columns(3_700_000) == 2
    assert sr.max_columns(1_700_000) == 0


def _cols11():
    mk = sr.Col
    return [
        mk("Человек", "text", 12, 0, avg_len=12),
        mk("Должность", "category", 4, 0, avg_len=9),
        mk("Отдел", "category", 4, 0, avg_len=8),
        mk("Город", "category", 3, 0, avg_len=6),
        mk("Руководитель", "category", 10, 0, avg_len=12),
        mk("Грейд", "category", 3, 0, avg_len=2),
        mk("Принят", "period", 12, 0),
        mk("Оклад", "money", 12, 0, cv=0.4),
        mk("Бонус", "money", 8, 0, cv=0.8),
        mk("Статус", "category", 2, 0, avg_len=8),
        mk("Комментарий", "text", 3, 0, avg_len=44),
    ]


def test_select_columns_from_11():
    plan, hidden = sr.select_table_columns(_cols11(), 12, 10_500_000)
    names = [p.name for p in plan]
    assert len(plan) == 4
    assert names[0] == "Человек"  # сущность первой
    assert "Оклад" in names  # главная метрика
    assert "Статус" in names
    assert "Комментарий" not in names and "Комментарий" in hidden
    assert len(hidden) == 11 - 4


def test_select_columns_with_delta():
    mk = sr.Col
    cols = [
        mk("Регион", "text", 8, 0, avg_len=8),
        mk("План", "money", 8, 0, cv=0.3),
        mk("Факт", "money", 8, 0, cv=0.3),
        mk("Статус", "category", 3, 0, avg_len=6),
        mk("Менеджер", "category", 8, 0, avg_len=10),
    ]
    plan, hidden = sr.select_table_columns(cols, 8, 10_500_000, delta_pair=("План", "Факт"))
    roles = [p.role for p in plan]
    assert roles[:3] == ["entity", "metric", "delta"] and plan[2].derived
    assert len(plan) == 4 and "Менеджер" in hidden


def test_narrow_host_gives_fewer_columns_or_none():
    plan, _ = sr.select_table_columns(_cols11(), 12, 5_400_000)
    assert len(plan) == 3
    plan, hidden = sr.select_table_columns(_cols11(), 12, 3_000_000)
    assert plan == [] and len(hidden) == 11


def test_word_break_detection_matches_the_bug():
    # 11 колонок по 450 272 EMU (как в реальном прогоне) → слова рвутся
    assert sr.cell_breaks_word("Руководитель", 450_272)
    assert sr.cell_breaks_word("Человек", 450_272)
    # 1 800 000 EMU при 11pt держит слово до ~18–19 знаков
    assert not sr.cell_breaks_word("Руководитель", sr.MIN_COL_EMU)
    assert not sr.cell_breaks_word("Переговоры", sr.MIN_COL_EMU)
    assert not sr.cell_breaks_word("Межведомственная", sr.MIN_COL_EMU)  # 16 знаков
    assert sr.cell_breaks_word("Электроэнергетический", sr.MIN_COL_EMU)  # 21 знак → сократить


def test_fit_table_flags_old_layout_and_accepts_new():
    cells = [["Иванов Пётр", "Менеджер", "идёт"]] * 4
    bad = sr.fit_table(cells, 4_953_000, 3_000_000, 11)
    assert not bad["ok"]
    good = sr.fit_table(cells, 10_500_000, 3_000_000, 3)
    assert good["ok"] and good["font_pt"] >= 11 and good["col_w"] >= sr.MIN_COL_EMU


def test_top_rows_with_other():
    rows = [[f"r{i}", i] for i in range(1, 13)]
    out = sr.top_rows_with_other(rows, 1)
    assert len(out) == sr.MAX_TABLE_ROWS
    assert out[0][0] == "r12" and out[-1][0] == "Прочие (6)" and out[-1][1] == sum(range(1, 7))
    assert sr.top_rows_with_other(rows[:5], 1) == rows[:5]


def test_footnote():
    assert sr.table_footnote(4, 11, 6, 12) == (
        "Показаны 4 из 11 колонок, 6 из 12 строк. Полная таблица — в приложении и HTML-экспорте"
    )


# ── F. архетипы ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "kw,expected",
    [
        (dict(shape="kpi", n_items=1), "stat_callout"),
        (dict(shape="kpi", n_items=4), "card_grid"),
        (dict(shape="series", n_items=12, n_points=12), "ibcs_chart"),
        (dict(shape="reference", n_items=3, n_cols=11), "card_grid"),
        (dict(shape="reference", n_items=40, n_cols=11, has_numeric=True), "compact_table"),
        (
            dict(
                shape="reference", n_items=40, n_cols=11, has_numeric=True, host_width_emu=3_000_000
            ),
            "card_grid",
        ),
        (dict(shape="text", n_items=4, sequential=True), "process_flow"),
        (dict(shape="categorical", n_items=2, has_pair=True, n_points=2), "comparison_split"),
    ],
)
def test_choose_archetype(kw, expected):
    assert sr.choose_archetype(**kw) == expected


# ── JSON-схемы из отчёта ────────────────────────────────────────────────────
def test_schemas_validate_examples():
    schema = json.loads((HERE / "action_title.schema.json").read_text(encoding="utf-8"))
    ok = {
        "title": "Выручка B2B выросла в 2,1 раза за 12 месяцев",
        "lead_in": "С 1,0 до 2,1 млн ₽: основной вклад дал ритейл",
        "claim_type": "growth",
        "numbers": ["2,1", "12"],
        "evidence_ids": ["pack-1:ds-1-trend-i0"],
        "so_what": "Усилить «Ритейл»: на него приходится 62% прироста",
    }
    jsonschema.validate(ok, schema)
    for bad in (
        {**ok, "title": "Чистый шаблон"},
        {**ok, "title": "Справочная таблица: 3 позиции"},
        {**ok, "title": "Динамика выручки"},
        {**ok, "evidence_ids": []},
        {**ok, "claim_type": "topic"},
    ):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, schema)


# ── G. Сторилайн SCQA, грамматика архетипов, разнообразие и ось вариантов ────


def _scqa_pack() -> object:
    from deckdna.contracts.content_pack import Block, ContentPack, Section, SourceRef

    ref = SourceRef(artifact_id="scqa.md")
    sections = [
        Section(
            id="sec-rec",
            heading="Рекомендации и план действий",
            level=2,
            blocks=[
                Block(
                    kind="list",
                    items=[
                        "Запустить пилот в двух филиалах до конца квартала",
                        "Перевести 60% типовых заявок на автоматический конвейер",
                    ],
                    source_ref=ref,
                )
            ],
        ),
        Section(
            id="sec-ev-weak",
            heading="Качественные наблюдения",
            level=2,
            blocks=[
                Block(
                    kind="list",
                    items=[
                        "Команды отмечают удобство единого окна согласования",
                        "Снижается число повторных возвратов на доработку",
                    ],
                    source_ref=ref,
                )
            ],
        ),
        Section(
            id="sec-comp",
            heading="Проблема и узкие места",
            level=2,
            blocks=[
                Block(
                    kind="list",
                    items=[
                        "Ручная сверка таблиц занимает 14 часов в неделю",
                        "Доля ошибок при переносе данных достигает 18%",
                    ],
                    source_ref=ref,
                )
            ],
        ),
        Section(
            id="sec-ans",
            heading="Ключевой вывод и решение",
            level=2,
            blocks=[
                Block(
                    kind="list",
                    items=[
                        "Переход на сквозной конвейер сокращает цикл в 3 раза",
                        "Окупаемость внедрения достигается за 4 месяца",
                    ],
                    source_ref=ref,
                )
            ],
        ),
        Section(
            id="sec-ev-strong",
            heading="Финансовый эффект пилота",
            level=2,
            blocks=[
                Block(
                    kind="list",
                    items=[
                        "Выручка на одного менеджера выросла на 42% (с 1,2 до 1,7 млн ₽)",
                        "Себестоимость обработки заказа снизилась на 35% за 6 месяцев",
                        "SLA выполнения заявок вырос с 78% до 96%",
                    ],
                    source_ref=ref,
                )
            ],
        ),
        Section(
            id="sec-sit",
            heading="Ситуация на рынке и контекст",
            level=2,
            blocks=[
                Block(
                    kind="list",
                    items=[
                        "Объём корпоративного сегмента вырос до 120 млрд ₽ в 2025 году",
                        "Клиенты ожидают ответа по сложным заявкам в течение 2 часов",
                    ],
                    source_ref=ref,
                )
            ],
        ),
    ]
    return ContentPack(
        schema_version="1.0",
        id="pack-scqa",
        language="ru",
        title_hint="Автоматизация конвейера продаж",
        sections=sections,
        tables=[],
        assets=[],
        warnings=[],
    )


def test_scqa_storyline_orders_situation_complication_answer_evidence_recommendation():
    from deckdna.contracts.deck_plan import Brief, Purpose
    from deckdna.planning.story_director import plan_deck

    pack = _scqa_pack()
    brief = Brief(
        purpose="Обосновать масштабирование конвейера",
        audience="инвестиционный комитет",
        language="ru",
        target_slide_count=9,
    )
    plan = plan_deck(pack, brief)
    assert plan.slides[0].purpose == Purpose.title
    assert plan.slides[1].purpose == Purpose.agenda  # >= 8 слайдов -> есть повестка
    assert plan.slides[-1].purpose == Purpose.cta  # финальный слайд — рекомендация, не «Спасибо»
    assert "Спасибо" not in plan.slides[-1].title_intent

    content_slides = [
        s
        for s in plan.slides
        if s.purpose not in (Purpose.title, Purpose.agenda, Purpose.section_divider, Purpose.cta)
    ]
    sec_order = [
        next(eid.split(":")[1] for eid in s.evidence_ids if ":" in eid) for s in content_slides
    ]
    assert sec_order == [
        "sec-sit",
        "sec-comp",
        "sec-ans",
        "sec-ev-strong",
        "sec-ev-weak",
        "sec-rec",
    ]


def test_semantic_archetypes_cover_grammar():
    from deckdna.contracts.deck_plan import (
        ChartSpec,
        ChartType,
        ContentUnit,
        DensityBudget,
        Kind,
        Level,
        Purpose,
        SlidePlan,
    )

    def _mk_slide(purpose, title, units):
        return SlidePlan(
            id="s1",
            index=1,
            purpose=purpose,
            title_intent=title,
            key_message=title,
            evidence_ids=["p:s1"],
            content_units=[
                ContentUnit(role="title", kind=Kind.title, text=title, evidence_ids=["p:s1"]),
                *units,
            ],
            density_budget=DensityBudget(level=Level.medium, max_items=4),
        )

    def u_num(t: str) -> ContentUnit:
        return ContentUnit(role="bullet", kind=Kind.bullet, text=t, evidence_ids=["p:s1"])

    assert (
        sr.select_semantic_archetype(
            _mk_slide(
                Purpose.overview,
                "Три ключевые метрики",
                [u_num("Выручка +34%"), u_num("Маржа 28%"), u_num("NPS 74 п.")],
            )
        )
        == "kpi_cards"
    )
    assert (
        sr.select_semantic_archetype(
            _mk_slide(Purpose.overview, "Главный рекорд", [u_num("Рост выручки составил 145%")])
        )
        == "big_number"
    )
    assert (
        sr.select_semantic_archetype(
            _mk_slide(
                Purpose.data,
                "Динамика выручки",
                [
                    ContentUnit(
                        role="chart",
                        kind=Kind.chart,
                        chart_ref="c1",
                        evidence_ids=["p:s1"],
                        chart_spec=ChartSpec(chart_type=ChartType.line),
                    ),
                    u_num("Рост в 2,1 раза за год"),
                ],
            )
        )
        == "chart_takeaway"
    )
    assert (
        sr.select_semantic_archetype(
            _mk_slide(
                Purpose.comparison,
                "Сравнение двух вариантов",
                [u_num("Вариант А: быстрый запуск"), u_num("Вариант Б: глубокая кастомизация")],
            )
        )
        == "comparison"
    )
    assert (
        sr.select_semantic_archetype(
            _mk_slide(
                Purpose.overview,
                "Топ сегментов",
                [ContentUnit(role="table", kind=Kind.table, table_ref="t1", evidence_ids=["p:s1"])],
            )
        )
        == "table_topn"
    )
    assert (
        sr.select_semantic_archetype(
            _mk_slide(
                Purpose.timeline,
                "Дорожная карта внедрения",
                [u_num("Этап 1: аудит"), u_num("Этап 2: пилот"), u_num("Этап 3: тираж")],
            )
        )
        == "timeline"
    )
    assert (
        sr.select_semantic_archetype(
            _mk_slide(
                Purpose.process,
                "Процесс обработки",
                [
                    ContentUnit(
                        role="bullet", kind=Kind.bullet, text="Сбор входа", evidence_ids=["p:s1"]
                    ),
                    ContentUnit(
                        role="bullet", kind=Kind.bullet, text="Валидация", evidence_ids=["p:s1"]
                    ),
                    ContentUnit(
                        role="bullet", kind=Kind.bullet, text="Публикация", evidence_ids=["p:s1"]
                    ),
                ],
            )
        )
        == "process"
    )
    assert (
        sr.select_semantic_archetype(
            _mk_slide(
                Purpose.quote,
                "Отзыв заказчика",
                [
                    ContentUnit(
                        role="quote",
                        kind=Kind.quote,
                        text="Система сократила ручной труд.",
                        evidence_ids=["p:s1"],
                    )
                ],
            )
        )
        == "quote"
    )
    assert (
        sr.select_semantic_archetype(
            _mk_slide(
                Purpose.overview,
                "Главный вывод",
                [
                    ContentUnit(
                        role="bullet",
                        kind=Kind.bullet,
                        text="Единый контур устраняет потери.",
                        evidence_ids=["p:s1"],
                    )
                ],
            )
        )
        == "callout"
    )


def test_diversity_constraints_no_adjacent_duplicates_bullets_cap_and_4_archetypes():
    from deckdna.contracts.content_pack import Block, ContentPack, Section, SourceRef
    from deckdna.contracts.deck_plan import Brief, Purpose
    from deckdna.planning.story_director import plan_deck

    ref = SourceRef(artifact_id="uniform.md")
    # 7 однотипных текстовых секций по 3 пункта без чисел
    pack = ContentPack(
        schema_version="1.0",
        id="pack-uniform",
        language="ru",
        title_hint="Однородный вход",
        sections=[
            Section(
                id=f"s{i}",
                heading=f"Направление работы {chr(1040 + i)}",
                level=2,
                blocks=[
                    Block(
                        kind="list",
                        items=[
                            "Команда согласует единый регламент взаимодействия подразделений",
                            "Формируется прозрачный контур контроля качества результатов",
                            "Стандартизируются шаблоны обмена данными между смежными группами",
                        ],
                        source_ref=ref,
                    )
                ],
            )
            for i in range(7)
        ],
        tables=[],
        assets=[],
        warnings=[],
    )
    plan = plan_deck(
        pack,
        Brief(
            purpose="План трансформации",
            audience="руководство",
            language="ru",
            target_slide_count=10,
        ),
    )
    assert len(plan.slides) >= 8
    structural = {"title", "agenda", "section_divider", "thank_you", "qa"}
    content_slides = [s for s in plan.slides if s.purpose.value not in structural]
    archs = [sr.slide_archetype(s) for s in content_slides]

    # 1) Ни один архетип не повторяется два раза подряд
    for a, b in zip(archs, archs[1:], strict=False):
        assert a != b, f"два одинаковых архетипа подряд: {archs}"

    # 2) Доля bullets <= 1/3 колоды
    bullets_count = sum(1 for a in archs if a == "bullets")
    assert bullets_count <= len(plan.slides) / 3 + 1e-6

    # 3) В колоде >= 8 слайдов минимум 4 разных архетипа
    assert len(set(archs)) >= 4, (
        f"меньше 4 архетипов в колоде из {len(plan.slides)} слайдов: {archs}"
    )

    # 4) Соседние контентные слайды не имеют подряд одинаковый Purpose.overview
    for s1, s2 in zip(content_slides, content_slides[1:], strict=False):
        assert not (s1.purpose == Purpose.overview and s2.purpose == Purpose.overview)


def test_chart_takeaway_allowed_adjacent_only_when_chart_types_differ():
    from pathlib import Path

    from deckdna.contracts.deck_plan import Brief
    from deckdna.ingestion.content_parsers import parse_file
    from deckdna.planning.story_director import plan_deck

    pack = parse_file(Path("examples/stage2/purchases.csv"))
    plan = plan_deck(
        pack,
        Brief(
            purpose="Итоги продаж за два года",
            audience="руководство",
            language="ru",
            target_slide_count=7,
        ),
    )
    structural = {"title", "agenda", "section_divider", "thank_you", "qa"}
    content_slides = [s for s in plan.slides if s.purpose.value not in structural]
    archs = [sr.slide_archetype(s) for s in content_slides]
    for s1, s2, a1, a2 in zip(content_slides, content_slides[1:], archs, archs[1:], strict=False):
        if a1 == a2:
            assert a1 == "chart_takeaway"
            assert sr._slide_chart_type(s1) != sr._slide_chart_type(s2)
    assert plan.slides[-1].purpose.value == "cta"


def test_three_variants_differ_along_density_and_visual_archetype_axis():
    from deckdna.contracts.deck_plan import Brief, Purpose
    from deckdna.contracts.variant_spec import Strategy
    from deckdna.planning.story_director import plan_deck

    pack = _scqa_pack()
    brief = Brief(
        purpose="Обосновать масштабирование конвейера",
        audience="руководство",
        language="ru",
        target_slide_count=8,
    )
    plans = {
        s.value: plan_deck(pack, brief, strategy=s)
        for s in (Strategy.faithful, Strategy.balanced, Strategy.visual)
    }

    # Все три варианта опираются на один и тот же набор фактов (секций пака)
    def _fact_sections(p):
        return {
            eid.split(":")[1]
            for s in p.slides
            if s.purpose not in (Purpose.title, Purpose.agenda, Purpose.section_divider)
            for eid in s.evidence_ids
            if ":" in eid and not eid.endswith(":meta")
        }

    assert _fact_sections(plans["faithful"]) == _fact_sections(plans["balanced"])
    assert _fact_sections(plans["balanced"]) == _fact_sections(plans["visual"])

    # Ось 1: доля визуальных архетипов растёт от faithful к visual
    vis_f = sr.visual_archetype_share(plans["faithful"])
    vis_b = sr.visual_archetype_share(plans["balanced"])
    vis_v = sr.visual_archetype_share(plans["visual"])
    assert vis_f <= vis_b <= vis_v
    assert vis_v > vis_f or vis_v == 1.0

    # Ось 2: текстовая плотность снижается от faithful (dense) к balanced (medium) и visual (sparse)
    dens_f = sr.deck_text_density(plans["faithful"])
    dens_b = sr.deck_text_density(plans["balanced"])
    dens_v = sr.deck_text_density(plans["visual"])
    assert dens_f["total_words"] > dens_b["total_words"] > dens_v["total_words"]
    assert dens_f["total_bullets"] > dens_b["total_bullets"] > dens_v["total_bullets"]

    levels = {
        k: {
            s.density_budget.level.value
            for s in p.slides
            if s.purpose not in (Purpose.title, Purpose.agenda, Purpose.section_divider)
        }
        for k, p in plans.items()
    }
    assert levels["faithful"] == {"dense"}
    assert levels["balanced"] == {"medium"}
    assert levels["visual"] == {"sparse"}


def test_density_cap_5_items_12_words_moves_overflow_to_speaker_notes():
    from deckdna.contracts.content_pack import Block, ContentPack, Section, SourceRef
    from deckdna.contracts.deck_plan import Brief, Kind, Purpose
    from deckdna.contracts.variant_spec import Strategy
    from deckdna.planning.story_director import plan_deck

    ref = SourceRef(artifact_id="dense.md")
    long_items = [
        f"Пункт номер {i} содержит очень длинное подробное описание бизнес процесса "
        f"которое явно превышает двенадцать слов и должно уходить в заметки докладчика"
        for i in range(1, 8)
    ]
    pack = ContentPack(
        schema_version="1.0",
        id="pack-dense",
        language="ru",
        title_hint="Плотный контент",
        sections=[
            Section(
                id="s1",
                heading="Раздел 1",
                level=2,
                blocks=[Block(kind="list", items=long_items, source_ref=ref)],
            ),
            Section(
                id="s2",
                heading="Раздел 2",
                level=2,
                blocks=[Block(kind="list", items=long_items[:4], source_ref=ref)],
            ),
        ],
        tables=[],
        assets=[],
        warnings=[],
    )
    plan = plan_deck(
        pack,
        Brief(
            purpose="Проверка плотности",
            audience="руководство",
            language="ru",
            target_slide_count=4,
        ),
        strategy=Strategy.faithful,
    )
    for slide in plan.slides:
        if slide.purpose in (
            Purpose.title,
            Purpose.agenda,
            Purpose.section_divider,
            Purpose.thank_you,
        ):
            continue
        bullets = [u for u in slide.content_units if u.kind == Kind.bullet]
        assert len(bullets) <= 5
        for b in bullets:
            assert len((b.text or "").split()) <= 12, b.text
    first_content = next(s for s in plan.slides if s.purpose not in (Purpose.title, Purpose.cta))
    assert "Пункт номер 6" in (first_content.speaker_note or "")
    assert "Пункт номер 7" in (first_content.speaker_note or "")
    assert "двенадцать слов и должно уходить в заметки докладчика" in (
        first_content.speaker_note or ""
    )


def test_storyline_and_variant_axis_configs_loaded_from_yaml():
    from deckdna.contracts.variant_spec import Strategy
    from deckdna.planning.config import load_generation_config

    cfg = load_generation_config()
    story = sr.storyline_config(cfg)
    assert story.min_slides_for_agenda == 8
    assert story.closing_recommendation is True
    assert story.max_bullets_per_slide == 5
    assert story.max_words_per_bullet == 12
    assert story.no_adjacent_same_archetype is True
    assert story.allow_adjacent_chart_if_different_type is True
    assert abs(story.max_bullets_archetype_share - 0.3334) < 1e-4
    assert story.min_distinct_archetypes_for_8_plus == 4
    assert story.distinct_adjacent_donor_layouts is True

    # Проверка чтения из dict (например, из content_style.py)
    custom_story = sr.storyline_config(
        {
            "planning": {
                "storyline": {
                    "min_slides_for_agenda": 10,
                    "diversity": {
                        "forbid_adjacent_same_archetype": False,
                        "max_bullets_share": 0.25,
                    },
                }
            }
        }
    )
    assert custom_story.min_slides_for_agenda == 10
    assert custom_story.no_adjacent_same_archetype is False
    assert custom_story.max_bullets_archetype_share == 0.25

    ax_f = sr.variant_axis_config(Strategy.faithful)
    ax_b = sr.variant_axis_config(Strategy.balanced)
    ax_v = sr.variant_axis_config(Strategy.visual)
    assert (ax_f.density_level, ax_b.density_level, ax_v.density_level) == (
        "dense",
        "medium",
        "sparse",
    )
    assert ax_f.max_bullets_per_slide > ax_b.max_bullets_per_slide > ax_v.max_bullets_per_slide
    assert (
        ax_f.visual_archetype_target_share
        < ax_b.visual_archetype_target_share
        < ax_v.visual_archetype_target_share
    )


# ---------------------------------------------------------------------------
# FactLedger story: регрессионные тесты истории поверх FactLedger
# ---------------------------------------------------------------------------


def _sample_fact_ledger():
    from deckdna.analysis.fact_ledger import Fact, FactLedger

    f_trend = Fact(
        id="f_rev_trend",
        dataset_id="sales",
        dataset_version="v1",
        view_id="v_series",
        metric="Выручка",
        aggregation="sum",
        scope={},
        kind="endpoint_change",
        predicate="increase",
        values={"from": 120.0, "to": 180.0, "n_points": 12},
        derived={"delta": 60.0, "pct": 50.0, "ratio": 1.5},
        comparison={"from": "2023", "to": "2025", "baseline": "2023"},
        unit="млн ₽",
        score=0.92,
        source_refs=["sales.csv#A1:D13"],
    )
    f_leader = Fact(
        id="f_seg_leader",
        dataset_id="sales",
        dataset_version="v1",
        view_id="v_cat",
        metric="Выручка",
        aggregation="sum",
        scope={"segment": "B2B"},
        kind="share",
        predicate="dominant_share",
        values={"leader": "B2B", "dimension": "segment", "part": 95.0, "total": 180.0},
        derived={"share_pct": 52.8},
        comparison={"baseline": "total", "same_denominator": True},
        unit="млн ₽",
        score=0.86,
        source_refs=["sales.csv#E1:F5"],
    )
    f_laggard = Fact(
        id="f_seg_laggard",
        dataset_id="sales",
        dataset_version="v1",
        view_id="v_cat",
        metric="Выручка",
        aggregation="sum",
        scope={"segment": "B2B_vs_Gos"},
        kind="comparison",
        predicate="gap",
        values={"leader": "B2B", "runner_up": "Госсектор", "from": 50.0, "to": 95.0},
        derived={"gap_ratio": 1.9, "ratio": 1.9},
        comparison={"baseline": "Госсектор"},
        unit="млн ₽",
        score=0.68,
        source_refs=["sales.csv#E1:F5"],
    )
    f_margin = Fact(
        id="f_margin_drop",
        dataset_id="finance",
        dataset_version="v1",
        view_id="v_margin",
        metric="Маржа",
        aggregation="ratio",
        scope={},
        kind="endpoint_change",
        predicate="decrease",
        values={"from": 24.0, "to": 18.0, "n_points": 12},
        derived={"delta": -6.0, "pct": -25.0},
        comparison={"from": "2023", "to": "2025", "baseline": "2023"},
        unit="%",
        score=0.82,
        source_refs=["finance.csv#A1:C13"],
    )
    return FactLedger.from_facts([f_trend, f_leader, f_laggard, f_margin])


def test_story_ledger_validation_rejects_invalid_fact_id_title_and_causal_edges():
    import asyncio

    from deckdna.analysis.title_templates import render_title
    from deckdna.contracts.deck_plan import Brief
    from deckdna.planning.story_director import plan_story_ledger_llm

    ledger = _sample_fact_ledger()
    brief = Brief(
        purpose="Итоги продаж и маржинальности",
        audience="руководство",
        language="ru",
        target_slide_count=6,
    )

    # 1) Выдуманный fact_id отбрасывается
    bad_fact_plan = {
        "main_fact_id": "f_rev_trend",
        "slides": [
            {
                "role": "answer",
                "fact_ids": ["f_invented_999"],
                "title_realization_id": "increase_pct",
                "transition": "none",
                "layout_intent": "editorial",
            }
        ],
        "omitted_fact_ids": [],
    }
    v1 = sr.validate_story_ledger_plan(bad_fact_plan, ledger)
    assert not v1.ok
    assert any("unknown_fact_id" in r for r in v1.reasons)

    # 2) Недопустимый title_realization_id (не из allowed_title_ids) отбрасывается
    bad_title_plan = {
        "main_fact_id": "f_rev_trend",
        "slides": [
            {
                "role": "answer",
                "fact_ids": ["f_rev_trend"],
                "title_realization_id": "share_leader",
                "transition": "none",
                "layout_intent": "editorial",
            }
        ],
        "omitted_fact_ids": [],
    }
    v2 = sr.validate_story_ledger_plan(bad_title_plan, ledger)
    assert not v2.ok
    assert any("disallowed_title_realization_id" in r for r in v2.reasons)

    # 3) Слайд роли evidence без фактов отбрасывается
    empty_ev_plan = {
        "main_fact_id": "f_rev_trend",
        "slides": [
            {
                "role": "evidence",
                "fact_ids": [],
                "title_realization_id": "increase_pct",
                "transition": "elaboration",
                "layout_intent": "editorial",
            }
        ],
        "omitted_fact_ids": [],
    }
    v3 = sr.validate_story_ledger_plan(empty_ev_plan, ledger)
    assert not v3.ok
    assert any("evidence_slide_missing_fact" in r for r in v3.reasons)

    # 4) Причинный переход ('causal' / 'because') запрещён
    causal_plan = {
        "main_fact_id": "f_rev_trend",
        "slides": [
            {
                "role": "answer",
                "fact_ids": ["f_rev_trend"],
                "title_realization_id": ledger.by_id["f_rev_trend"].allowed_title_ids[0],
                "transition": "causal",
                "layout_intent": "editorial",
            }
        ],
        "omitted_fact_ids": [],
    }
    v4 = sr.validate_story_ledger_plan(causal_plan, ledger)
    assert not v4.ok
    assert any("causal" in r for r in v4.reasons)

    # 5) Невалидный ответ модели в plan_story_ledger_llm не роняет пайплайн, а откатывается на
    # beam search
    class _BrokenGateway:
        async def text_json(self, prompt_id, payload, schema):
            assert "facts" in payload
            assert payload["must_not_assume_direction"] is True
            return sr.StoryLedgerPlan.model_validate(bad_fact_plan)

    fallback_plan = asyncio.run(plan_story_ledger_llm(ledger, brief, _BrokenGateway()))
    assert fallback_plan.main_fact_id in ledger.by_id
    for sl in fallback_plan.slides:
        assert sl.fact_ids
        for fid in sl.fact_ids:
            assert fid in ledger.by_id
        primary = ledger.by_id[sl.fact_ids[0]]
        assert sl.title_realization_id in primary.allowed_title_ids
        rendered = render_title(sl.title_realization_id, primary)
        assert rendered


def test_beam_search_story_plan_objective_and_no_causal_transitions():
    ledger = _sample_fact_ledger()
    plan = sr.beam_search_story_plan(ledger, brief_text="Анализ выручки и маржи", max_slides=6, k=8)

    assert plan.main_fact_id == "f_rev_trend"
    assert len(plan.slides) >= 2
    allowed_NonCausal = {"none", "context", "elaboration", "contrast", "drill_down", "temporal"}
    for sl in plan.slides:
        assert sl.transition in allowed_NonCausal
        assert sl.transition not in sr.FORBIDDEN_CAUSAL_TRANSITIONS
        assert all(fid in ledger.by_id for fid in sl.fact_ids)

    # Проверяем формулу J = ΣS + 0.20·ΣL − 0.30·ΣD − 0.08·|π|
    f1 = ledger.by_id["f_rev_trend"]
    f2 = ledger.by_id["f_seg_leader"]
    tr_name, l_val = sr.classify_fact_transition(f1, f2)
    assert tr_name == "drill_down"
    from deckdna.analysis.fact_ledger import fact_similarity

    d_val = fact_similarity(f2, f1)
    expected_j = round((f1.score + f2.score) + 0.20 * l_val - 0.30 * d_val - 0.08 * 2, 6)
    assert sr.story_sequence_objective([f1, f2]) == expected_j


def test_material_length_two_facts_yield_3_to_4_slides_and_zero_facts_yield_honest_limitation():
    from deckdna.analysis.fact_ledger import Fact, FactLedger
    from deckdna.contracts.content_pack import Block, ContentPack, Section, SourceRef
    from deckdna.contracts.deck_plan import Brief, Purpose
    from deckdna.planning.story_director import plan_deck

    ledger_full = _sample_fact_ledger()
    two_facts = [ledger_full.by_id["f_rev_trend"], ledger_full.by_id["f_seg_leader"]]
    ledger_two = FactLedger.from_facts(two_facts)

    brief_12 = Brief(
        purpose="Анализ продаж",
        audience="руководство",
        language="ru",
        target_slide_count=12,
    )
    # 1) Два содержательных факта -> 3-4 слайда (обложка + 1-2 слайда фактов + финал), без добивки
    deck_two = plan_deck(
        ContentPack(
            schema_version="1.0",
            id="pack-two",
            language="ru",
            title_hint="Короткий отчёт",
            sections=[],
            tables=[],
            assets=[],
            warnings=[],
        ),
        brief_12,
        fact_ledger=ledger_two,
    )
    assert 3 <= len(deck_two.slides) <= 4
    assert not any(s.purpose == Purpose.section_divider for s in deck_two.slides)
    assert not any(s.purpose == Purpose.agenda for s in deck_two.slides)

    # 2) Ни одного содержательного факта -> честный слайд «Что можно установить и каких данных
    # не хватает»
    missing_f = Fact(
        id="f_missing",
        dataset_id="empty_ds",
        dataset_version="v1",
        metric="Выручка",
        aggregation="none",
        scope={},
        kind="missing",
        predicate="missing",
        values={"column": "Выручка", "missing_pct": 85.0},
        derived={"pct": 85.0},
        comparison={"period": "2025"},
        unit="%",
        score=0.5,
        source_refs=["empty.csv#A1:B10"],
    )
    ledger_zero = FactLedger.from_facts([missing_f])
    story_zero = sr.beam_search_story_plan(ledger_zero, brief=brief_12)
    assert len(story_zero.slides) == 1
    assert story_zero.slides[0].role == "limitation"

    deck_zero = sr.compile_story_ledger_to_deck_plan(story_zero, ledger_zero, brief_12)
    assert len(deck_zero.slides) == 3
    lim_slide = deck_zero.slides[1]
    body_joined = " ".join(u.text or "" for u in lim_slide.content_units)
    assert (
        "не хватает" in body_joined.lower()
        or "пропущен" in lim_slide.title_intent.lower()
        or "пропуски" in lim_slide.title_intent.lower()
    )

    # 3) Короткий текстовый материал (2 секции) при запросе 10 слайдов -> 4 слайда без
    # разделителей-заглушек
    short_pack = ContentPack(
        schema_version="1.0",
        id="pack-short-text",
        language="ru",
        title_hint="Короткая записка",
        sections=[
            Section(
                id="s1",
                heading="Факт 1",
                level=2,
                blocks=[
                    Block(
                        kind="paragraph",
                        text="Выручка выросла на 18% год к году.",
                        source_ref=SourceRef(artifact_id="note.md"),
                    )
                ],
            ),
            Section(
                id="s2",
                heading="Факт 2",
                level=2,
                blocks=[
                    Block(
                        kind="paragraph",
                        text="Доля корпоративного сегмента достигла 54%.",
                        source_ref=SourceRef(artifact_id="note.md"),
                    )
                ],
            ),
        ],
        tables=[],
        assets=[],
        warnings=[],
    )
    short_deck = plan_deck(short_pack, brief_12)
    assert 3 <= len(short_deck.slides) <= 4
    assert not any(s.purpose == Purpose.section_divider for s in short_deck.slides)


def test_goal_must_not_assume_direction_uses_honest_contrast_title():
    from deckdna.analysis.fact_ledger import FactLedger
    from deckdna.analysis.title_templates import render_title

    ledger_full = _sample_fact_ledger()
    drop_fact = ledger_full.by_id["f_margin_drop"]
    ledger_drop = FactLedger.from_facts([drop_fact])

    goal = sr.neutralize_goal_direction("Покажите рост маржи за 2023–2025")
    assert goal["must_not_assume_direction"] is True
    assert goal["assumed_direction"] == "increase"
    assert "Оценить динамику" in goal["neutral_question"]

    story = sr.beam_search_story_plan(ledger_drop, brief_text="Покажите рост маржи за 2023–2025")
    assert story.slides[0].title_realization_id == "no_growth_contrast"
    rendered_title = render_title(story.slides[0].title_realization_id, drop_fact)
    assert "не выросла" in rendered_title.lower() and "снижение" in rendered_title.lower()


def test_three_variants_preserve_identical_facts_numbers_chart_type_and_category_order():
    import re
    from pathlib import Path

    from deckdna.contracts.content_pack import Chart, ChartSeries, ContentPack, SourceRef
    from deckdna.contracts.deck_plan import Brief, Kind, Purpose
    from deckdna.contracts.variant_spec import Strategy
    from deckdna.ingestion.content_parsers import parse_file
    from deckdna.planning.story_director import plan_deck

    ledger = _sample_fact_ledger()
    pack = ContentPack(
        schema_version="1.0",
        id="pack-inv",
        language="ru",
        title_hint="Анализ продаж",
        sections=[],
        tables=[],
        charts=[
            Chart(
                id="chart-v_series",
                source_ref=SourceRef(artifact_id="sales.csv", range="v_series"),
                chart_type="line",
                title="Динамика выручки",
                categories=["2023", "2024", "2025"],
                series=[ChartSeries(name="Выручка", values=[120.0, 150.0, 180.0])],
                unit="млн ₽",
            ),
            Chart(
                id="chart-v_cat",
                source_ref=SourceRef(artifact_id="sales.csv", range="v_cat"),
                chart_type="bar",
                title="Структура выручки по сегментам",
                categories=["B2B", "B2C", "Партнёры", "Госсектор"],
                series=[ChartSeries(name="Выручка", values=[95.0, 50.0, 23.0, 12.0])],
                unit="млн ₽",
            ),
        ],
        assets=[],
        warnings=[],
    )
    brief = Brief(
        purpose="Анализ продаж и маржи",
        audience="руководство",
        language="ru",
        target_slide_count=6,
    )
    variants = sr.plan_three_variants_from_ledger(ledger, brief, pack=pack)

    def _extract_invariants(deck_plan):
        fact_seq = [tuple(s.insight_ids or []) for s in deck_plan.slides]
        numbers_seq = [
            re.findall(r"\d+(?:[.,]\d+)?", " ".join(u.text or "" for u in s.content_units))
            for s in deck_plan.slides
        ]
        chart_seq = [
            (
                u.chart_ref,
                u.chart_spec.chart_type.value if u.chart_spec else None,
            )
            for s in deck_plan.slides
            for u in s.content_units
            if u.kind == Kind.chart
        ]
        return fact_seq, numbers_seq, chart_seq

    inv_ed = _extract_invariants(variants["editorial"])
    inv_co = _extract_invariants(variants["comparison"])
    inv_da = _extract_invariants(variants["dashboard"])

    # Одинаковые fact_ids, числа и типы графиков во всех трёх вариантах
    assert inv_ed == inv_co == inv_da
    # Порядок категорий в графиках пака не мутируется
    assert pack.charts[0].categories == ["2023", "2024", "2025"]
    assert pack.charts[1].categories == ["B2B", "B2C", "Партнёры", "Госсектор"]

    # Меняются плотность (dense / medium / sparse) и композиционные роли/интенты
    dens_ed = sr.deck_text_density(variants["editorial"])
    dens_co = sr.deck_text_density(variants["comparison"])
    dens_da = sr.deck_text_density(variants["dashboard"])
    assert dens_ed["total_words"] > dens_co["total_words"] > dens_da["total_words"]

    # Проверяем тот же инвариант на реальном дата-паке (purchases.csv) через plan_deck
    real_pack = parse_file(Path("examples/stage2/purchases.csv"))
    real_plans = {
        s.value: plan_deck(real_pack, brief, strategy=s)
        for s in (Strategy.faithful, Strategy.balanced, Strategy.visual)
    }
    real_facts_f = [
        tuple(s.insight_ids or [])
        for s in real_plans["faithful"].slides
        if s.purpose != Purpose.title
    ]
    real_facts_b = [
        tuple(s.insight_ids or [])
        for s in real_plans["balanced"].slides
        if s.purpose != Purpose.title
    ]
    real_facts_v = [
        tuple(s.insight_ids or [])
        for s in real_plans["visual"].slides
        if s.purpose != Purpose.title
    ]
    assert real_facts_f == real_facts_b == real_facts_v

    real_charts_f = [
        (u.chart_ref, u.chart_spec.chart_type.value if u.chart_spec else None)
        for s in real_plans["faithful"].slides
        for u in s.content_units
        if u.kind == Kind.chart
    ]
    real_charts_b = [
        (u.chart_ref, u.chart_spec.chart_type.value if u.chart_spec else None)
        for s in real_plans["balanced"].slides
        for u in s.content_units
        if u.kind == Kind.chart
    ]
    real_charts_v = [
        (u.chart_ref, u.chart_spec.chart_type.value if u.chart_spec else None)
        for s in real_plans["visual"].slides
        for u in s.content_units
        if u.kind == Kind.chart
    ]
    assert real_charts_f == real_charts_b == real_charts_v


def test_layout_selection_cost_formula_and_no_three_consecutive_identical_families():
    # Проверяем точную формулу cost = fit + 0.8·(family == prev) + 0.3·count_in_last_4
    history = ["cards", "two_column", "cards", "cards"]
    # Для "cards": prev == "cards" (1), count_in_last_4 == 3 -> 0.5 + 0.8*1 + 0.3*3 = 2.2
    assert sr.layout_selection_cost(0.5, "cards", history) == 2.2
    # Для "kpi": prev != "kpi" (0), count_in_last_4 == 0 -> 0.9 + 0 + 0 = 0.9
    assert sr.layout_selection_cost(0.9, "kpi", history) == 0.9

    # Даже если у "cards" идеальный fit=0.0, а у альтернативы "two_column" большой fit=5.0,
    # после двух "cards" подряд третий "cards" подряд запрещён при наличии подходящей альтернативы
    chosen_after_two = sr.select_layout_family(
        [("cards", 0.0), ("two_column", 5.0)],
        history=["cards", "cards"],
    )
    assert chosen_after_two == "two_column"

    # На серии из 8 слайдов с одинаковыми кандидатами нет серий >= 3 одинаковых семейств
    seq = sr.select_layout_family_sequence(
        [[("cards", 0.1), ("kpi", 0.4), ("two_column", 0.5)] for _ in range(8)]
    )
    assert len(seq) == 8
    for i in range(len(seq) - 2):
        assert not (seq[i] == seq[i + 1] == seq[i + 2]), f"серия из 3 одинаковых: {seq}"
