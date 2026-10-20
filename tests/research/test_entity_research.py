"""Числа компании должны происходить из страницы, а не из отраслевого сниппета."""

import asyncio
import time

from deckdna.contracts.content_pack import ContentPack
from deckdna.contracts.deck_plan import Brief
from deckdna.providers.mock import MockProvider
from deckdna.research import gaps, web
from deckdna.research.config import WebResearchConfig, load_research_config


def brief(purpose="DNS - аналитика компании"):
    return Brief(purpose=purpose, audience="руководство", language="ru", target_slide_count=5)


def pack():
    return ContentPack(
        schema_version="1.0",
        id="empty",
        language="ru",
        sections=[],
        tables=[],
        assets=[],
        warnings=[],
    )


HTML = """<html><head><title>Отчёт компании</title>
<meta property="article:published_time" content="2025-04-15T10:00:00Z"></head><body>
<p>Выручка DNS в 2022 году составила 500 млрд рублей.</p>
<p>Выручка ДНС в 2023 году составила 600 млрд рублей.</p>
<p>Выручка DNS в 2024 году составила 700 млрд рублей.</p>
<p>Чистая прибыль DNS в 2024 году составила 20 млрд рублей.</p>
<p>Количество магазинов DNS в 2024 году составило 2500 магазинов.</p>
<p>Численность сотрудников DNS в 2024 году составила 40000 человек.</p>
<p>Выручка Другой Компании в 2024 году составила 999 млрд рублей.</p>
<p>DNS продаёт электронику. Выручка конкурента в 2024 году — 888 млрд рублей.</p>
</body></html>"""


class Search:
    async def search(self, query, limit, language):
        return [
            web.SearchHit(
                url="https://example.org/report",
                title="Отчёт",
                snippet="Выручка DNS в 2024 году составила 9999 млрд рублей.",
            )
        ]


async def fetch(url):
    title, text = web.html_to_text(HTML)
    return web.Page(url=url, title=title, text=text, published_at="2025-04-15")


def run_research(**kwargs):
    return asyncio.run(
        web.research_web(
            pack(),
            brief(),
            None,
            WebResearchConfig(),
            missing_slides=4,
            covered_topics=[],
            deadline=time.monotonic() + 2,
            search=Search(),
            fetch=fetch,
            **kwargs,
        )
    )


def test_entity_queries_keep_company_and_spelling_variants():
    queries = gaps.build_targeted_queries(brief(), max_queries=5)
    assert len(queries) == 5
    assert all("dns" in q.casefold() or "днс" in q.casefold() for q in queries)
    assert any("выручка" in q for q in queries)
    assert any("магазин" in q and "сотрудник" in q for q in queries)
    entity = gaps.extract_research_subject(brief().purpose)
    assert entity.name == "DNS"
    assert "ДНС" in entity.aliases
    assert gaps.extract_research_subject("Яндекс Маркет — итоги года").name == "Яндекс Маркет"
    assert gaps.extract_research_subject("рынок такси в России").kind == "market"


def test_model_queries_cannot_replace_entity_with_generic_market():
    gw = MockProvider(fixtures={web.QUERIES_PROMPT: {"queries": ["рынок электроники объем"]}})
    queries = asyncio.run(
        web.plan_queries(
            brief(), gw, covered_topics=[], missing_slides=4, max_queries=5, pack=pack()
        )
    )
    assert sum("dns" in q.casefold() or "днс" in q.casefold() for q in queries) >= 4


def test_html_numbers_form_sourced_dataset_without_model():
    result = run_research()
    assert result.facts_kept == 6
    facts = result.to_dict()["numeric_facts"]
    assert len(facts) == 6
    assert {f["value"] for f in facts} == {500, 600, 700, 20, 2500, 40000}
    assert all(f["source_url"] == "https://example.org/report" for f in facts)
    assert all(f["publisher"] and f["published_at"] == "2025-04-15" for f in facts)
    assert len(result.datasets) == 1
    dataset = result.datasets[0]
    assert dataset.row_count == 3
    assert dataset.columns[0].values == ["2022", "2023", "2024"]
    assert dataset.columns[1].values == [500, 600, 700]
    assert "https://example.org/report" in dataset.context_text
    assert result.to_dict()["datasets"][0]["id"] == dataset.id


def test_snippet_is_not_page_evidence_for_company_numbers():
    async def blocked(_url):
        return None

    result = asyncio.run(
        web.research_web(
            pack(),
            brief(),
            None,
            WebResearchConfig(),
            missing_slides=4,
            covered_topics=[],
            deadline=time.monotonic() + 2,
            search=Search(),
            fetch=blocked,
        )
    )
    assert result.facts_kept == 0
    assert result.to_dict()["questions"]
    assert result.to_dict()["status"] == "no_new_facts"


def test_query_model_timeout_is_inside_overall_deadline():
    class Slow:
        async def text_json(self, *args):
            await asyncio.sleep(0.2)

    started = time.monotonic()
    result = asyncio.run(
        web.research_web(
            pack(),
            brief(),
            Slow(),
            WebResearchConfig(),
            missing_slides=4,
            covered_topics=[],
            deadline=started + 0.05,
            search=Search(),
            fetch=fetch,
        )
    )
    assert time.monotonic() - started < 0.15
    assert result.degraded == "budget_exhausted"


def test_default_search_has_no_paid_key_and_budget_is_capped():
    from deckdna.settings import Settings

    cfg = load_research_config(search_url=Settings.model_fields["research_search_url"].default)
    assert cfg.web.search_url == "ddg://"
    assert cfg.budget_s <= 30


def test_market_subject_does_not_duplicate_market_prefix():
    queries = gaps.build_targeted_queries(brief("рынок такси в России"), max_queries=5)
    assert all("рынок рынок" not in q.casefold() for q in queries)


def test_generic_product_description_is_not_a_named_company():
    subject = gaps.extract_research_subject("Цифровой дизайнер презентаций для компаний")
    assert subject.kind == "market"


def test_shares_from_three_players_form_one_dataset():
    from deckdna.research.numbers_chart import datasets_from_numbers, extract_grounded_numbers

    subject = gaps.extract_research_subject("рынок такси в России")
    text = (
        "В 2024 году доля Альфа на рынке такси составила 50%.\n"
        "В 2024 году доля Бета на рынке такси составила 30%.\n"
        "В 2024 году доля Гамма на рынке такси составила 20%."
    )
    facts = extract_grounded_numbers(text, subject, "https://example.org/shares", "Издатель")
    datasets = datasets_from_numbers(facts)
    assert len(datasets) == 1
    assert datasets[0].columns[0].values == ["Альфа", "Бета", "Гамма"]
    assert datasets[0].columns[1].values == [50, 30, 20]


def test_numbers_do_not_mix_metric_period_or_conflicting_sources():
    from deckdna.research.numbers_chart import datasets_from_numbers, extract_grounded_numbers

    text = (
        "Выручка DNS в 2022 году составила 500 млрд рублей.\n"
        "Выручка DNS в 2023 году составила 600 млрд рублей.\n"
        "Выручка DNS в 2024 году составила 700 млрд рублей.\n"
        "Выручка DNS в первом квартале 2025 года составила 300 млрд рублей.\n"
        "Прогноз выручки DNS на 2026 год — 1000 млрд рублей.\n"
        "Прибыль DNS в 2021 году составила 50 млрд рублей."
    )
    subject = gaps.extract_research_subject("DNS - аналитика компании")
    facts = extract_grounded_numbers(text, subject, "https://example.org/report", "Издатель")
    assert len(facts) == 4
    assert datasets_from_numbers(facts)[0].columns[1].values == [500, 600, 700]
    conflicting = extract_grounded_numbers(
        "Выручка DNS в 2024 году составила 701 млрд рублей.",
        subject,
        "https://example.net/report",
        "Другой издатель",
    )
    assert datasets_from_numbers(facts + conflicting) == []


def test_same_unit_different_metrics_never_make_a_time_series():
    from deckdna.contracts.content_pack import SourceRef
    from deckdna.research.numbers_chart import chart_or_table

    chart, table = chart_or_table(
        [
            "Выручка в 2022 году — 500 млрд рублей",
            "Прибыль в 2023 году — 20 млрд рублей",
            "Расходы в 2024 году — 400 млрд рублей",
        ],
        "mixed",
        SourceRef(artifact_id="web:1"),
    )
    assert chart is None
    assert table is not None


def test_ddg_uses_lite_when_html_returns_challenge():
    import httpx
    from deckdna.research.providers import DuckDuckGoSearch

    def respond(request):
        if request.url.host == "html.duckduckgo.com":
            return httpx.Response(202, text="challenge")
        return httpx.Response(
            200,
            text="""<table>
<tr><td><a class="result-link" href="https://example.org/report">Company results</a></td></tr>
<tr><td class="result-snippet">Revenue was 700 billion rubles in 2024.</td></tr>
</table>""",
        )

    hits = asyncio.run(
        DuckDuckGoSearch(transport=httpx.MockTransport(respond)).search("company revenue", 4, "en")
    )
    assert [h.url for h in hits] == ["https://example.org/report"]
    assert "700" in hits[0].snippet


def test_approximate_numbers_keep_their_qualifier_in_display_text():
    from deckdna.research.numbers_chart import extract_grounded_numbers

    facts = extract_grounded_numbers(
        "Выручка DNS в 2024 году составила более 700 млрд рублей.",
        gaps.extract_research_subject(brief().purpose),
        "https://example.org/",
        "Издатель",
    )
    assert facts[0].qualifier == "approximate"
    assert "более" in facts[0].text


def test_different_metric_numbers_in_one_sentence_do_not_cross_attach():
    from deckdna.research.numbers_chart import extract_grounded_numbers

    text = "Выручка DNS в 2024 году составила 700 млрд рублей, а чистая прибыль — 20 млрд рублей."
    facts = extract_grounded_numbers(
        text, gaps.extract_research_subject(brief().purpose), "https://example.org/", "Издатель"
    )
    assert {(f.metric, f.value) for f in facts} == {("Выручка", 700), ("Чистая прибыль", 20)}


def test_company_mention_does_not_attribute_another_named_player_metric():
    from deckdna.research.numbers_chart import extract_grounded_numbers

    text = "DNS и Альфа работают в России, выручка Альфа в 2024 году — 888 млрд рублей."
    facts = extract_grounded_numbers(
        text, gaps.extract_research_subject(brief().purpose), "https://example.org/", "Издатель"
    )
    assert facts == []


def test_count_growth_percent_is_not_a_store_count():
    from deckdna.research.numbers_chart import extract_grounded_numbers

    facts = extract_grounded_numbers(
        "Число магазинов DNS в 2024 году выросло на 12%.",
        gaps.extract_research_subject(brief().purpose),
        "https://example.org/",
        "Издатель",
    )
    assert facts == []


def test_company_does_not_inherit_market_size_with_same_acronym():
    from deckdna.research.numbers_chart import extract_grounded_numbers

    facts = extract_grounded_numbers(
        "Объем рынка DNS в 2024 году составил 4 млрд долларов.",
        gaps.extract_research_subject(brief().purpose),
        "https://example.org/",
        "Издатель",
    )
    assert facts == []


def test_multiword_entity_matches_punctuation_variants():
    subject = gaps.extract_research_subject("Яндекс Маркет — итоги года")
    assert subject.matches("Выручка Яндекс.Маркет составила 10 млрд рублей.")
    assert subject.matches("Выручка «Яндекс-Маркет» выросла.")


def test_ddg_403_also_tries_lite():
    import httpx
    from deckdna.research.providers import DuckDuckGoSearch

    def respond(request):
        if request.url.host == "html.duckduckgo.com":
            return httpx.Response(403)
        return httpx.Response(
            200, text='<a class="result-link" href="https://example.org/">Report</a>'
        )

    hits = asyncio.run(
        DuckDuckGoSearch(transport=httpx.MockTransport(respond)).search("company revenue", 4, "en")
    )
    assert [h.url for h in hits] == ["https://example.org/"]


def test_product_operating_metrics_with_inflected_name_and_spelled_scale():
    from deckdna.research.numbers_chart import extract_grounded_numbers

    text = "Количество активных покупателей на Яндекс Маркете составило 18,2 миллиона человек."
    facts = extract_grounded_numbers(
        text,
        gaps.extract_research_subject("Яндекс Маркет — итоги года"),
        "https://example.org/",
        "Издатель",
    )
    assert [(f.metric, f.value, f.unit) for f in facts] == [
        ("Активные покупатели", 18.2, "млн чел.")
    ]


def test_generic_short_description_is_not_a_named_company():
    assert gaps.extract_research_subject("Цифровой дизайнер презентаций").kind == "market"


def test_financial_comparison_keeps_current_year_and_metric_scope():
    from deckdna.research.numbers_chart import extract_grounded_numbers

    text = (
        "Чистая прибыль компании «Альфа» в 2024 году снизилась на 21,7% "
        "по сравнению с 2023 годом до 32,992 млрд рублей."
    )
    facts = extract_grounded_numbers(
        text,
        gaps.extract_research_subject("Альфа — анализ компании"),
        "https://example.org/",
        "Издатель",
    )
    assert [(f.metric, f.value, f.period) for f in facts] == [("Чистая прибыль", 32.992, "2024")]


def test_neighboring_company_context_and_publication_relative_year():
    from deckdna.research.numbers_chart import extract_grounded_numbers

    text = (
        "ООО «Альфа» опубликовало годовой финансовый отчёт.\n"
        "Выручка ООО в прошлом году выросла на 4,6% до 760,20 млрд рублей."
    )
    facts = extract_grounded_numbers(
        text,
        gaps.extract_research_subject("Альфа — анализ компании"),
        "https://example.org/",
        "Издатель",
        "2025-03-31",
    )
    assert len(facts) == 1
    assert facts[0].period == "2024"
    assert "760,20" in facts[0].text
    assert "Альфа" in facts[0].quote
    assert web.verify_fact(
        web.ExtractedFact(text=facts[0].text, quote=facts[0].quote, topic="Финансы"), text
    )


def test_russian_accounting_abbreviation_is_not_another_owner():
    from deckdna.research.numbers_chart import extract_grounded_numbers

    facts = extract_grounded_numbers(
        "Выручка DNS по РСБУ в 2024 году достигла 760,2 млрд рублей.",
        gaps.extract_research_subject(brief().purpose),
        "https://example.org/",
        "Издатель",
    )
    assert len(facts) == 1


def test_slide_numbers_attach_dataset_and_real_chart_without_model():
    from deckdna.contracts.content_pack import Block, Kind, Section, SourceRef
    from deckdna.planning import plan_deck
    from deckdna.planning.config import DeckBounds, GenerationConfig
    from deckdna.research.config import ResearchConfig
    from deckdna.research.slide import research_slide

    content = pack().model_copy(
        update={
            "sections": [
                Section(
                    id="intro",
                    heading="О компании",
                    level=2,
                    blocks=[
                        Block(
                            kind=Kind.list,
                            items=[
                                "Анализ компании для руководства",
                                "Финансовые результаты за три года",
                            ],
                            source_ref=SourceRef(artifact_id="brief.md"),
                        )
                    ],
                )
            ]
        }
    )
    plan = plan_deck(content, brief(), config=GenerationConfig(deck=DeckBounds(min_slides=3)))
    result = asyncio.run(
        research_slide(
            plan,
            plan.slides[1],
            content,
            brief(),
            None,
            ResearchConfig(),
            focus="numbers",
            search=Search(),
            fetch=fetch,
        )
    )
    assert len(result.pack.datasets) == 1
    assert result.chart_id
    chart = next(c for c in result.pack.charts if c.id == result.chart_id)
    assert chart.categories == ["2022", "2023", "2024"]
    assert chart.series[0].values == [500, 600, 700]


def test_expenses_are_not_attached_to_preceding_profit():
    from deckdna.research.numbers_chart import extract_grounded_numbers

    text = (
        "Прибыль от продаж DNS в 2024 году составила 58,4 млрд рублей при росте "
        "коммерческих расходов до 81,2 млрд рублей и управленческих расходов до 9,25 млрд рублей."
    )
    facts = extract_grounded_numbers(
        text, gaps.extract_research_subject(brief().purpose), "https://example.org/", "Издатель"
    )
    assert {(f.metric, f.value) for f in facts} == {
        ("Прибыль от продаж", 58.4),
        ("Коммерческие расходы", 81.2),
        ("Управленческие расходы", 9.25),
    }


def test_neighbor_context_stops_at_another_company_before_metric():
    from deckdna.research.numbers_chart import extract_grounded_numbers

    facts = extract_grounded_numbers(
        "DNS продаёт электронику.\nАльфа увеличила выручку в 2024 году до 888 млрд рублей.",
        gaps.extract_research_subject(brief().purpose),
        "https://example.org/",
        "Издатель",
    )
    assert facts == []


def test_comparison_year_before_metric_is_not_the_observation_year():
    from deckdna.research.numbers_chart import extract_grounded_numbers

    facts = extract_grounded_numbers(
        "По сравнению с 2023 годом выручка DNS в 2024 году выросла до 700 млрд рублей.",
        gaps.extract_research_subject(brief().purpose),
        "https://example.org/",
        "Издатель",
    )
    assert [(f.value, f.period) for f in facts] == [(700, "2024")]


def test_absolute_growth_is_not_a_level_and_percent_is_not_headcount():
    from deckdna.research.numbers_chart import extract_grounded_numbers

    facts = extract_grounded_numbers(
        "Выручка DNS в 2024 году увеличилась на 20 млрд рублей до 700 млрд рублей.",
        gaps.extract_research_subject(brief().purpose),
        "https://example.org/",
        "Издатель",
    )
    assert [f.value for f in facts] == [700]
    facts = extract_grounded_numbers(
        "Количество активных покупателей на Яндекс Маркете выросло на 20%.",
        gaps.extract_research_subject("Яндекс Маркет — итоги года"),
        "https://example.org/",
        "Издатель",
    )
    assert facts == []


def test_market_snippet_cannot_supply_numeric_evidence():
    gw = MockProvider(
        fixtures={
            web.QUERIES_PROMPT: {"queries": ["рынок такси России"]},
            web.EXTRACT_PROMPT: {
                "facts": [
                    {
                        "text": "Объем рынка такси в России в 2024 году составил 999 млрд рублей",
                        "quote": "Объем рынка такси в России в 2024 году составил 999 млрд рублей",
                        "topic": "Рынок",
                    }
                ]
            },
        }
    )

    class SnippetSearch:
        async def search(self, *args):
            return [
                web.SearchHit(
                    url="https://example.org/report",
                    title="Исследование",
                    snippet="Объем рынка такси в России в 2024 году составил 999 млрд рублей",
                )
            ]

    async def blocked(_):
        return None

    result = asyncio.run(
        web.research_web(
            pack(),
            brief("рынок такси в России"),
            gw,
            WebResearchConfig(),
            missing_slides=3,
            covered_topics=[],
            deadline=time.monotonic() + 1,
            search=SnippetSearch(),
            fetch=blocked,
        )
    )
    assert result.facts_kept == 0
    assert result.pages_fetched == 0
    assert result.to_dict()["questions"]


def test_colon_headed_year_list_forms_verified_time_series():
    from deckdna.research.numbers_chart import datasets_from_numbers, extract_grounded_numbers

    raw_html = """<html><body>
    <p>DNS — российская компания, владелец розничной сети.</p>
    <p>Выручка компании составила, млрд руб.:</p>
    <ul>
      <li>2022 год — 500;</li>
      <li>2023 год — 600;</li>
      <li>2024 год — 760.</li>
    </ul>
    </body></html>"""
    _, text = web.html_to_text(raw_html)
    subject = gaps.extract_research_subject("DNS - аналитика компании")
    facts = extract_grounded_numbers(text, subject, "https://example.org/wiki", "Википедия")
    assert [(f.period, f.value, f.unit) for f in facts] == [
        ("2022", 500.0, "млрд ₽"),
        ("2023", 600.0, "млрд ₽"),
        ("2024", 760.0, "млрд ₽"),
    ]
    for f in facts:
        assert web.verify_fact(
            web.ExtractedFact(text=f.text, quote=f.quote, topic="Показатели"),
            text,
            max_quote_chars=800,
        )
    datasets = datasets_from_numbers(facts)
    assert len(datasets) == 1
    assert datasets[0].columns[0].values == ["2022", "2023", "2024"]
    assert datasets[0].columns[1].values == [500.0, 600.0, 760.0]


def test_post_number_count_nouns_bind_to_their_own_metric():
    from deckdna.research.numbers_chart import extract_grounded_numbers

    text = (
        "DNS развивает федеральную розничную сеть в регионах.\n"
        "В 2010 году сеть насчитывала 100 магазинов в 28 городах России, "
        "в которых работали 1,5 тыс. сотрудников."
    )
    subject = gaps.extract_research_subject("DNS - аналитика компании")
    facts = extract_grounded_numbers(text, subject, "https://example.org/wiki", "Википедия")
    assert {(f.metric, f.value, f.unit) for f in facts} == {
        ("Магазины", 100.0, "магазинов"),
        ("Сотрудники", 1.5, "тыс."),
    }


def test_parenthesized_market_shares_build_share_dataset():
    from deckdna.research.numbers_chart import datasets_from_numbers, extract_grounded_numbers

    text = (
        "Чаще всего в такси встречаются автомобили марок "
        "Lada (16,6%), Kia (13,5%), Hyundai (12,3%) и Volkswagen (8%)."
    )
    subject = gaps.extract_research_subject("рынок такси в России")
    facts = extract_grounded_numbers(text, subject, "https://example.org/taxi", "Авто")
    datasets = datasets_from_numbers(facts)
    assert len(datasets) == 1
    assert datasets[0].columns[0].values == ["Lada", "Kia", "Hyundai", "Volkswagen"]
    assert datasets[0].columns[1].values == [16.6, 13.5, 12.3, 8.0]


def test_foreign_entity_with_growth_only_resets_neighbor_context():
    from deckdna.research.numbers_chart import extract_grounded_numbers

    text = (
        "Яндекс Маркет обновил логистическую инфраструктуру.\n"
        "Тем временем у компании «Авито» выручка выросла на 15%.\n"
        "Чистая прибыль в 2024 году составила 28,8 млрд рублей."
    )
    subject = gaps.extract_research_subject("Яндекс Маркет — итоги года")
    facts = extract_grounded_numbers(text, subject, "https://example.org/news", "Издатель")
    assert facts == []
