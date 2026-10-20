"""Регрессионные тесты веб-ресёрча и атрибуции источников:
1. Выявление пробелов в скудном брифе (TAM/SAM/SOM, конкуренты, юнит-экономика)
   и генерация снайперских запросов без слов «презентация для инвесторов».
2. Провайдеры быстрого поиска: DuckDuckGo (бесключевой HTML), Tavily, Serper
   и каскадный FallbackSearchProvider.
3. Snippet-First Grounding при блокировке/редиректе целевых страниц, очистка
   от cookie-мусора и сохранение статистических абзацев за порогом 6000 символов.
4. Автоматическое формирование сносок «Источник: Data Insight, РБК Исследования
   рынков 2025» и мягкая деградация в отраслевые бенчмарки при сбое сети.
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx
from deckdna.contracts.content_pack import (
    Block,
    ContentPack,
    ExternalSource,
    Kind,
    Origin,
    Section,
    SourceRef,
)
from deckdna.contracts.deck_plan import Brief
from deckdna.planning import plan_deck
from deckdna.planning.config import DeckBounds, GenerationConfig
from deckdna.providers.mock import MockProvider
from deckdna.research import fill_content
from deckdna.research.citations import (
    MODEL_BENCHMARK_FOOTNOTE_RU,
    MODEL_BENCHMARK_TITLE_RU,
    format_source_footnote,
    resolve_publisher_name,
    select_page_excerpt,
)
from deckdna.research.config import ResearchConfig, WebResearchConfig
from deckdna.research.expand import EXPAND_PROMPT
from deckdna.research.gaps import (
    GapDimension,
    analyze_brief_gaps,
    build_targeted_queries,
    extract_core_subject,
)
from deckdna.research.providers import (
    DuckDuckGoSearch,
    FallbackSearchProvider,
    SerperSearch,
    TavilySearch,
    build_search_provider,
)
from deckdna.research.slide import research_slide
from deckdna.research.web import (
    EXTRACT_PROMPT,
    QUERIES_PROMPT,
    SearchHit,
    html_to_text,
    plan_queries,
    research_web,
)

CFG = GenerationConfig(deck=DeckBounds(min_slides=3, max_slides=40))


def _sparse_food_pack() -> ContentPack:
    return ContentPack(
        schema_version="1.0",
        id="pack-food",
        language="ru",
        sections=[
            Section(
                id="s0",
                heading="Идея",
                level=2,
                blocks=[
                    Block(
                        kind=Kind.paragraph,
                        text="Презентация для инвесторов про сервис доставки готовой еды.",
                        source_ref=SourceRef(artifact_id="brief.md"),
                    )
                ],
            )
        ],
        tables=[],
        assets=[],
        warnings=[],
    )


def _food_brief(target: int = 8) -> Brief:
    return Brief(
        purpose="Презентация для инвесторов про сервис доставки готовой еды",
        audience="инвесторы",
        language="ru",
        target_slide_count=target,
    )


class TestGapAnalysisAndQueryFormulation:
    def test_extracts_core_subject_and_detects_three_business_gaps(self):
        brief = _food_brief()
        pack = _sparse_food_pack()
        assert extract_core_subject(brief.purpose, "ru") == "сервис доставки готовой еды"

        gaps = analyze_brief_gaps(pack, brief, max_queries=3)
        dims = [g.dimension for g in gaps.missing_dimensions]
        assert dims == [
            GapDimension.market_trends,
            GapDimension.competitive_landscape,
            GapDimension.unit_economics,
        ]
        assert gaps.targeted_queries[0] == (
            "рынок доставки готовой еды РФ 2025 2026 объем аналитика"
        )
        assert all("презентаци" not in q.casefold() for q in gaps.targeted_queries)

    def test_plan_queries_replaces_meta_presentation_fallback_with_targeted_queries(self):
        brief = _food_brief()
        pack = _sparse_food_pack()

        def failing_queries(_payload):
            raise RuntimeError("llm timeout")

        gw = MockProvider(fixtures={QUERIES_PROMPT: failing_queries})
        queries = asyncio.run(
            plan_queries(
                brief,
                gw,
                covered_topics=["Идея"],
                missing_slides=5,
                max_queries=3,
                pack=pack,
            )
        )
        assert len(queries) == 3
        assert queries[0] == "рынок доставки готовой еды РФ 2025 2026 объем аналитика"
        assert all("презентация" not in q.casefold() for q in queries)

    def test_covered_dimension_is_not_duplicated_first(self):
        pack = ContentPack(
            schema_version="1.0",
            id="pack-partial",
            language="ru",
            sections=[
                Section(
                    id="s0",
                    heading="Рынок",
                    level=2,
                    blocks=[
                        Block(
                            kind=Kind.paragraph,
                            text="Объем рынка доставки готовой еды составил 650 млрд ₽, CAGR 24%.",
                            source_ref=SourceRef(artifact_id="brief.md"),
                        )
                    ],
                )
            ],
            tables=[],
            assets=[],
            warnings=[],
        )
        queries = build_targeted_queries(_food_brief(), pack, max_queries=2)
        # Рынок уже есть в паке — первыми идут конкуренты и юнит-экономика
        assert "лидеры доля конкуренты" in queries[0]
        assert "средний чек LTV CAC" in queries[1]


class TestFastSearchProviders:
    def test_duckduckgo_html_provider_unwraps_uddg_and_skips_ads(self):
        ddg_html = (
            "<html><body>"
            '<div class="result result--ad">'
            '<a class="result__a" href="https://duckduckgo.com/y.js?ad_provider=bing">Реклама</a>'
            '<a class="result__snippet">Рекламный блок</a>'
            "</div>"
            '<div class="result">'
            '<a class="result__a" '
            'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdatainsight.ru%2Ffoodtech-2025&amp;rut=123">'
            "Рынок e-grocery и доставки готовой еды 2025 — Data Insight"
            "</a>"
            '<a class="result__snippet">'
            "По данным Data Insight, объем рынка доставки готовой еды в РФ в 2025 году "
            "достиг 650 млрд рублей (+24% год к году)."
            "</a>"
            "</div>"
            "</body></html>"
        )
        transport = httpx.MockTransport(lambda req: httpx.Response(200, text=ddg_html))
        provider = DuckDuckGoSearch(transport=transport)
        hits = asyncio.run(provider.search("рынок доставки готовой еды", limit=3, language="ru"))
        assert len(hits) == 1
        assert hits[0].url == "https://datainsight.ru/foodtech-2025"
        assert "650 млрд рублей" in hits[0].snippet

    def test_tavily_and_serper_providers_return_structured_hits(self):
        def tavily_handler(req: httpx.Request) -> httpx.Response:
            body = json.loads(req.content.decode())
            assert body["query"] == "рынок доставки еды"
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://marketing.rbc.ru/research/food-2025",
                            "title": "Анализ рынка доставки еды 2025 — РБК Исследования рынков",
                            "content": (
                                "Средний чек в доставке готовой еды вырос на 18% до 1 420 рублей."
                            ),
                        }
                    ]
                },
            )

        def serper_handler(req: httpx.Request) -> httpx.Response:
            assert req.headers["X-API-KEY"] == "serper-key"
            return httpx.Response(
                200,
                json={
                    "answerBox": {
                        "title": "Объем рынка FoodTech",
                        "link": "https://infoline.spb.ru/foodtech-2025",
                        "snippet": "Рынок FoodTech в России превысил 1,2 трлн рублей в 2025 году.",
                    },
                    "organic": [
                        {
                            "title": "Лидеры доставки — TAdviser",
                            "link": "https://tadviser.ru/food-delivery",
                            "snippet": "Топ-3 игрока контролируют 72% рынка экспресс-доставки.",
                        }
                    ],
                },
            )

        tavily = TavilySearch(api_key="tvly-key", transport=httpx.MockTransport(tavily_handler))
        t_hits = asyncio.run(tavily.search("рынок доставки еды", limit=3, language="ru"))
        assert len(t_hits) == 1
        assert "1 420 рублей" in t_hits[0].snippet

        serper = SerperSearch(api_key="serper-key", transport=httpx.MockTransport(serper_handler))
        s_hits = asyncio.run(serper.search("рынок доставки еды", limit=3, language="ru"))
        assert len(s_hits) == 2
        assert s_hits[0].url == "https://infoline.spb.ru/foodtech-2025"

    def test_fallback_provider_and_url_factory(self):
        class BrokenProvider:
            async def search(self, query: str, limit: int, language: str) -> list[SearchHit]:
                raise httpx.ConnectError("primary down")

        class BackupProvider:
            async def search(self, query: str, limit: int, language: str) -> list[SearchHit]:
                return [
                    SearchHit(
                        url="https://datainsight.ru/ok",
                        title="Data Insight",
                        snippet="Рынок вырос на 24% в 2025 году до 650 млрд рублей.",
                    )
                ]

        chain = FallbackSearchProvider([BrokenProvider(), BackupProvider()])
        hits = asyncio.run(chain.search("рынок", limit=2, language="ru"))
        assert len(hits) == 1 and hits[0].url == "https://datainsight.ru/ok"

        assert isinstance(build_search_provider("ddg://default"), DuckDuckGoSearch)
        assert isinstance(build_search_provider("tavily://key123"), TavilySearch)
        assert isinstance(build_search_provider("serper://key123"), SerperSearch)


class TestSnippetFirstGroundingAndCitations:
    def test_snippet_fallback_saves_facts_when_direct_page_fetch_is_blocked(self):
        """Регрессия: если сайт закрыт Cloudflare/редиректом (fetch_page -> None),
        богатый сниппет поисковой выдачи сохраняет качественные верифицированные факты."""

        class SnippetSearch:
            async def search(self, query: str, limit: int, language: str) -> list[SearchHit]:
                return [
                    SearchHit(
                        url="https://datainsight.ru/food-delivery-2025",
                        title="Рынок доставки готовой еды 2025 — Data Insight",
                        snippet=(
                            "Рынок доставки готовой еды в России растет за счет готовых рационов "
                            "и развития дарккитчен. Частота заказов повышает удержание клиентов."
                        ),
                    ),
                    SearchHit(
                        url="https://marketing.rbc.ru/foodtech-2025",
                        title="Аналитика FoodTech 2025 — РБК Исследования рынков",
                        snippet=(
                            "Лидеры сегмента контролируют рынок доставки готовой еды в РФ, "
                            "развивая собственные подписочные экосистемы и экспресс-логистику."
                        ),
                    ),
                ]

        async def blocked_fetch(_url: str):
            return None  # Имитация 301 редиректа или антибот-защиты при прямом GET

        def extract_from_snippet(payload: dict) -> dict:
            text = payload["page"]["text"]
            if "дарккитчен" in text:
                return {
                    "facts": [
                        {
                            "text": (
                                "Рынок доставки готовой еды в РФ растет за счет готовых рационов"
                            ),
                            "quote": (
                                "Рынок доставки готовой еды в России растет за счет готовых "
                                "рационов и развития дарккитчен"
                            ),
                            "topic": "Рынок и тренды",
                        },
                        {
                            "text": "Частота заказов в доставке готовой еды повышает удержание",
                            "quote": "Частота заказов повышает удержание клиентов",
                            "topic": "Бизнес-метрики сегмента",
                        },
                    ]
                }
            return {
                "facts": [
                    {
                        "text": "Лидеры сегмента развивают подписочные экосистемы доставки",
                        "quote": (
                            "Лидеры сегмента контролируют рынок доставки готовой еды в РФ, "
                            "развивая собственные подписочные экосистемы и экспресс-логистику"
                        ),
                        "topic": "Конкурентный ландшафт",
                    }
                ]
            }

        gw = MockProvider(
            fixtures={
                QUERIES_PROMPT: {
                    "queries": ["рынок доставки готовой еды РФ 2025 2026 объем аналитика"]
                },
                EXTRACT_PROMPT: extract_from_snippet,
            }
        )
        cfg = WebResearchConfig(search_url="http://searxng:8080")
        res = asyncio.run(
            research_web(
                _sparse_food_pack(),
                _food_brief(),
                gw,
                cfg,
                missing_slides=3,
                covered_topics=["Идея"],
                deadline=time.monotonic() + 10,
                search=SnippetSearch(),
                fetch=blocked_fetch,
                min_facts_per_topic=1,
            )
        )
        assert res.degraded is None
        assert res.facts_kept == 3
        publishers = [s.publisher for s in res.sources]
        assert publishers == ["Data Insight", "РБК Исследования рынков"]
        assert res.footnote == "Источник: Data Insight, РБК Исследования рынков 2025"

    def test_boilerplate_cleaning_and_stat_priority_excerpt(self):
        noisy_html = """
        <html><head><title>Исследование рынка — РБК Исследования рынков</title></head>
        <body>
          <p>Мы используем файлы cookie для улучшения работы сайта и анализа трафика.</p>
          <p>Подпишитесь на наш Telegram-канал, чтобы не пропустить главные новости бизнеса!</p>
          <p>Объем рынка облачных сервисов в России вырос на 34% до 165 млрд рублей в 2025 году.</p>
          <p>Читайте также: топ-10 трендов цифровизации в ритейле и логистике.</p>
        </body></html>
        """
        title, text = html_to_text(noisy_html)
        assert resolve_publisher_name("https://www.rbc.ru/article/1", title) == (
            "РБК Исследования рынков"
        )
        assert "cookie" not in text.casefold()
        assert "telegram" not in text.casefold()
        assert "165 млрд рублей" in text

        # Длинный текст: статистический абзац в конце после 7000 символов воды
        filler = "\n".join(
            f"Общий вводный абзац номер {i} с описанием истории отрасли без цифр и метрик."
            for i in range(120)
        )
        stat_para = "Ключевой факт: рынок вырос на 28% и достиг 490 млрд ₽ в 2025 году."
        long_text = f"{filler}\n{stat_para}"
        assert len(long_text) > 7000
        excerpt = select_page_excerpt(long_text, max_chars=2500)
        assert len(excerpt) <= 2500
        assert stat_para in excerpt

    def test_slide_numbers_research_attaches_citation_footnote_to_chart(self):
        from deckdna.research.web import Page

        plan = plan_deck(_sparse_food_pack(), _food_brief(4), config=CFG)
        slide = plan.slides[1]
        page_text = (
            "В 2023 году рынок составил 410 млрд рублей. "
            "В 2024 году рынок составил 520 млрд рублей. "
            "В 2025 году рынок составил 650 млрд рублей."
        )

        class StatSearch:
            async def search(self, query: str, limit: int, language: str) -> list[SearchHit]:
                return [
                    SearchHit(
                        url="https://datainsight.ru/food-2025",
                        title="Отчет Data Insight 2025",
                        snippet=page_text,
                    )
                ]

        async def fetch_page_stub(url: str):
            return Page(url=url, title="Отчет Data Insight 2025", text=page_text)

        gw = MockProvider(
            fixtures={
                QUERIES_PROMPT: {"queries": ["рынок доставки еды 2025"]},
                EXTRACT_PROMPT: {
                    "facts": [
                        {
                            "text": "В 2023 году рынок составил 410 млрд рублей",
                            "quote": "В 2023 году рынок составил 410 млрд рублей",
                            "topic": "Динамика",
                        },
                        {
                            "text": "В 2024 году рынок составил 520 млрд рублей",
                            "quote": "В 2024 году рынок составил 520 млрд рублей",
                            "topic": "Динамика",
                        },
                        {
                            "text": "В 2025 году рынок составил 650 млрд рублей",
                            "quote": "В 2025 году рынок составил 650 млрд рублей",
                            "topic": "Динамика",
                        },
                    ]
                },
            }
        )
        rc = ResearchConfig(budget_s=10, web=WebResearchConfig(search_url="http://searxng"))
        found = asyncio.run(
            research_slide(
                plan,
                slide,
                _sparse_food_pack(),
                plan.brief,
                gw,
                rc,
                focus="numbers",
                search=StatSearch(),
                fetch=fetch_page_stub,
            )
        )
        assert found.chart_id is not None
        chart = next(c for c in found.pack.charts if c.id == found.chart_id)
        assert chart.source_note == "Источник: Data Insight 2025"


class TestGracefulDegradation:
    def test_offline_degradation_marks_open_industry_benchmark_estimation(self):
        class OfflineSearch:
            async def search(self, query: str, limit: int, language: str) -> list[SearchHit]:
                raise httpx.ConnectError("network unreachable")

        def model_expand(payload: dict) -> dict:
            dims = [d["dimension"] for d in payload.get("missing_dimensions", [])]
            assert "market_trends" in dims
            return {
                "sections": [
                    {
                        "heading": "Рынок и драйверы спроса",
                        "bullets": [
                            (
                                "Спрос смещается в сторону готовых рационов и "
                                "экспресс-доставки до 30 минут"
                            ),
                            (
                                "Консолидация вокруг крупных экосистем повышает "
                                "порог входа для локальных кухонь"
                            ),
                        ],
                    },
                    {
                        "heading": "Факторы юнит-экономики",
                        "bullets": [
                            (
                                "Удержание клиентов и частота заказов определяют "
                                "окупаемость привлечения (LTV/CAC)"
                            ),
                            (
                                "Плотность заказов на дарккитчен снижает "
                                "логистические издержки на последнюю милю"
                            ),
                        ],
                    },
                ]
            }

        gw = MockProvider(
            fixtures={
                QUERIES_PROMPT: {"queries": ["рынок доставки еды РФ 2025"]},
                EXTRACT_PROMPT: {"facts": []},
                EXPAND_PROMPT: model_expand,
            }
        )
        rc = ResearchConfig(budget_s=15, web=WebResearchConfig(search_url="http://searxng:8080"))
        out = asyncio.run(
            fill_content(
                _sparse_food_pack(),
                _food_brief(6),
                gw,
                config=CFG,
                research_config=rc,
                search=OfflineSearch(),
            )
        )
        assert out.web is not None and out.web.degraded == "no_search_results"
        assert out.resolved == "expand" and out.model_sections == 2
        model_sources = [s for s in out.pack.external_sources if s.origin == Origin.model]
        assert len(model_sources) == 1
        assert model_sources[0].title == MODEL_BENCHMARK_TITLE_RU
        assert format_source_footnote(model_sources, language="ru") == MODEL_BENCHMARK_FOOTNOTE_RU
        assert (
            format_source_footnote(
                [
                    ExternalSource(
                        id="web:1",
                        origin=Origin.web,
                        url="https://datainsight.ru/a",
                        title="Data Insight 2025",
                        publisher="Data Insight",
                    ),
                    ExternalSource(
                        id="web:2",
                        origin=Origin.web,
                        url="https://marketing.rbc.ru/b",
                        title="РБК Исследования рынков 2025",
                        publisher="РБК Исследования рынков",
                    ),
                ],
                language="ru",
            )
            == "Источник: Data Insight, РБК Исследования рынков 2025"
        )
