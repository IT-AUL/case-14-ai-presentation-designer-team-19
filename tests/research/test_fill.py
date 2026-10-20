"""ADR-030: стадия добора контента — режимы, добор из знаний модели,
веб-ресёрч с проверкой цитат. Без сети: поиск и загрузка страниц
подменены, модель — MockProvider с fixture'ами."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from deckdna.contracts.content_pack import Block, ContentPack, Kind, Section, SourceRef
from deckdna.contracts.deck_plan import Brief, ContentFill
from deckdna.planning import plan_deck, validate_deck_plan
from deckdna.planning.config import DeckBounds, GenerationConfig
from deckdna.planning.content_fill import annotate_unit_origins
from deckdna.planning.diagram_detect import PROMPT_NAME as DIAGRAM_PROMPT
from deckdna.planning.evidence import build_evidence_graph
from deckdna.providers.mock import MockProvider
from deckdna.research import fill_content
from deckdna.research.config import ModelFillConfig, ResearchConfig, WebResearchConfig
from deckdna.research.expand import EXPAND_PROMPT
from deckdna.research.web import (
    EXTRACT_PROMPT,
    QUERIES_PROMPT,
    ExtractedFact,
    Page,
    SearchHit,
    fetch_page,
    html_to_text,
    verify_fact,
)

CFG = GenerationConfig(deck=DeckBounds(min_slides=3, max_slides=40))
RC = ResearchConfig(budget_s=30)
RC_WEB = ResearchConfig(budget_s=30, web=WebResearchConfig(search_url="http://searxng:8080"))


def _brief(target: int = 8, fill: ContentFill | None = None) -> Brief:
    return Brief(
        purpose="Цифровой дизайнер презентаций для компаний",
        audience="руководство",
        language="ru",
        target_slide_count=target,
        content_fill=fill,
    )


def _one_liner() -> ContentPack:
    return ContentPack(
        schema_version="1.0",
        id="pack-one",
        language="ru",
        sections=[
            Section(
                id="s0",
                heading="Идея",
                level=2,
                blocks=[
                    Block(
                        kind=Kind.paragraph,
                        text="Сервис собирает презентацию по шаблону компании за минуты.",
                        source_ref=SourceRef(artifact_id="brief.md"),
                    )
                ],
            )
        ],
        tables=[],
        assets=[],
        warnings=[],
    )


def _bullet(topic: str, k: int) -> str:
    return f"{topic}: содержательный пункт номер {k} с пояснением для руководства компании"


def _expand_fixture(payload: dict) -> dict:
    return {
        "sections": [
            {
                "heading": f"Раздел модели {i}",
                "bullets": [_bullet(f"Тема {i}", k) for k in range(4)],
            }
            for i in range(payload["sections_needed"])
        ]
    }


def _run(coro):
    return asyncio.run(coro)


class TestModes:
    @pytest.mark.parametrize("target, resolved", [(3, "none"), (4, "condense")])
    def test_sufficient_content_does_not_call_models_or_search(self, target, resolved):
        pack = _one_liner()
        if target == 4:
            pack = pack.model_copy(
                update={
                    "sections": [
                        pack.sections[0].model_copy(update={"id": f"s{i}"}) for i in range(8)
                    ]
                }
            )
        gw = MockProvider(
            fixtures={DIAGRAM_PROMPT: {"diagrams": []}, EXPAND_PROMPT: _expand_fixture}
        )
        search = FakeSearch()
        out = _run(
            fill_content(
                pack,
                _brief(target),
                gw,
                config=CFG,
                research_config=RC_WEB,
                search=search,
                fetch=_fake_fetch,
            )
        )
        assert out.resolved == resolved and not out.changed
        assert not gw.calls
        assert not search.queries
        assert out.pack is pack

    def test_sufficient_content_keeps_local_process_detection(self):
        pack = _one_liner()
        pack.sections[0].heading = "Процесс"
        pack.sections[0].blocks[0] = Block(
            kind=Kind.paragraph,
            text="Сбор → Проверка → Публикация",
            source_ref=SourceRef(artifact_id="brief.md"),
        )
        gw = MockProvider(fixtures={DIAGRAM_PROMPT: {"diagrams": []}})
        out = _run(fill_content(pack, _brief(3), gw, config=CFG, research_config=RC))
        assert out.diagrams_detected == 1
        assert out.pack.diagrams[0].steps == ["Сбор", "Проверка", "Публикация"]
        assert not gw.calls

    def test_enough_content_is_left_alone(self):
        pack = _one_liner()
        out = _run(fill_content(pack, _brief(3), None, config=CFG, research_config=RC))
        assert out.resolved == "none" and out.pack is pack

    def test_shrink_cuts_the_deck_to_capacity(self):
        out = _run(
            fill_content(
                _one_liner(), _brief(10, ContentFill.shrink), None, config=CFG, research_config=RC
            )
        )
        assert out.resolved == "shrink"
        assert out.brief.target_slide_count == out.before.capacity == 3

    def test_expand_without_a_model_leaves_the_count_to_the_planner(self):
        """Добрать нечем: цель не урезается по оценке — планировщик сам
        использует весь контент и сокращает колоду без заглушек."""
        out = _run(
            fill_content(_one_liner(), _brief(10), MockProvider(), config=CFG, research_config=RC)
        )
        assert out.resolved == "no_model"
        assert out.brief.target_slide_count == 10
        plan = plan_deck(out.pack, out.brief, config=CFG)
        assert len(plan.slides) == plan.brief.target_slide_count == 3

    def test_ask_returns_a_decision_only_when_someone_can_answer(self):
        gw = MockProvider(fixtures={EXPAND_PROMPT: _expand_fixture})
        asked = _run(
            fill_content(
                _one_liner(),
                _brief(8, ContentFill.ask),
                gw,
                config=CFG,
                research_config=RC,
                ask_allowed=True,
            )
        )
        assert asked.needs_decision and asked.resolved == "ask"
        assert not gw.calls
        headless = _run(
            fill_content(
                _one_liner(), _brief(8, ContentFill.ask), gw, config=CFG, research_config=RC
            )
        )
        assert not headless.needs_decision and headless.resolved == "expand"


class TestModelExpand:
    def test_expand_reaches_the_requested_count_with_marked_facts(self):
        gw = MockProvider(fixtures={EXPAND_PROMPT: _expand_fixture})
        out = _run(fill_content(_one_liner(), _brief(8), gw, config=CFG, research_config=RC))
        assert out.resolved == "expand"
        assert out.brief.target_slide_count == 8
        assert out.after.capacity >= 8
        assert [s.origin.value for s in out.pack.external_sources] == ["model"]
        report = out.to_dict()
        assert report["facts_by_origin"]["user"] == 1
        assert report["facts_by_origin"]["model"] > 0

        plan = annotate_unit_origins(plan_deck(out.pack, out.brief, config=CFG), out.pack)
        validate_deck_plan(plan, CFG)
        assert len(plan.slides) == 8
        assert not any(
            s.title_intent.startswith("Раздел ") and not s.title_intent.startswith("Раздел модели")
            for s in plan.slides
        )
        # фраза пользователя не потерялась (здесь она стала заголовком-
        # выводом слайда), добранные пункты помечены как знания модели
        texts = [u.text for s in plan.slides for u in s.content_units]
        assert "Сервис собирает презентацию по шаблону компании за минуты." in " ".join(
            t or "" for t in texts
        ) or any(s.title_intent.startswith("Сервис собирает") for s in plan.slides)
        origins = {u.origin.value for s in plan.slides for u in s.content_units if u.origin}
        assert origins == {"model"}

    def test_model_output_is_filtered(self):
        def noisy(payload):
            return {
                "sections": [
                    {"heading": "Идея", "bullets": [_bullet("дубль", 1), _bullet("дубль", 2)]},
                    {
                        "heading": "Ссылки",
                        "bullets": [
                            "Подробнее на https://example.com/report про рынок презентаций",
                            _bullet("ссылки", 1),
                        ],
                    },
                    {
                        "heading": "Годный",
                        "bullets": [
                            _bullet("годный", 1),
                            _bullet("годный", 2),
                            "коротко",
                            "Сервис собирает презентацию по шаблону компании за минуты.",
                        ],
                    },
                ]
            }

        gw = MockProvider(fixtures={EXPAND_PROMPT: noisy})
        rc = ResearchConfig(budget_s=30, model=ModelFillConfig(bullets_per_section=4))
        out = _run(fill_content(_one_liner(), _brief(5), gw, config=CFG, research_config=rc))
        added = [s for s in out.pack.sections if s.id.startswith("sec-model")]
        # раздел с уже существующим заголовком и раздел, где после
        # фильтрации ссылок остался один пункт, отброшены
        assert [s.heading for s in added] == ["Годный"]
        assert added[0].blocks[0].items == [_bullet("годный", 1), _bullet("годный", 2)]

    def test_provider_failure_does_not_fail_the_run(self):
        def boom(payload):
            raise RuntimeError("provider down")

        gw = MockProvider(fixtures={EXPAND_PROMPT: boom})
        out = _run(fill_content(_one_liner(), _brief(8), gw, config=CFG, research_config=RC))
        assert out.resolved == "expand_failed"
        assert out.brief.target_slide_count == 8  # дальше решает планировщик


PAGE_TEXT = (
    "Рынок программ для презентаций в 2025 году оценивался в 9,3 млрд долларов. "
    "Компании тратят до 30% рабочего времени аналитиков на подготовку слайдов. "
    "Корпоративные шаблоны обеспечивают единый стиль документов компании. "
) * 3


class FakeSearch:
    def __init__(self):
        self.queries: list[str] = []

    async def search(self, query: str, limit: int, language: str) -> list[SearchHit]:
        self.queries.append(query)
        return [
            SearchHit(url="https://news.example.org/market", title="Рынок"),
            SearchHit(url="https://news.example.org/market#dup", title="Дубль"),
        ]


async def _fake_fetch(url: str) -> Page | None:
    return Page(url=url, title="Обзор рынка презентаций", text=PAGE_TEXT)


def _extract_fixture(payload):
    assert "Рынок программ" in payload["page"]["text"]
    q_size = "Рынок программ для презентаций в 2025 году оценивался в 9,3 млрд долларов."
    q_time = "Компании тратят до 30% рабочего времени аналитиков на подготовку слайдов."
    return {
        "facts": [
            {
                "text": "Рынок программ для презентаций в 2025 году — 9,3 млрд долларов",
                "quote": q_size,
                "topic": "Рынок",
            },
            {
                "text": "Подготовка слайдов отнимает до 30% времени аналитиков",
                "quote": q_time,
                "topic": "Рынок",
            },
            # цитаты нет на странице — выдумка
            {
                "text": "Рынок вырастет вдвое к 2030 году",
                "quote": "Рынок вырастет вдвое к 2030 году по оценкам аналитиков.",
                "topic": "Рынок",
            },
            # число факта не совпадает с цитатой
            {
                "text": "Подготовка слайдов отнимает до 50% времени",
                "quote": q_time,
                "topic": "Время",
            },
        ]
    }


class TestWebResearch:
    def _gateway(self):
        return MockProvider(
            fixtures={
                QUERIES_PROMPT: {"queries": ["рынок презентаций", "рынок презентаций", "шаблоны"]},
                EXTRACT_PROMPT: _extract_fixture,
                EXPAND_PROMPT: _expand_fixture,
            }
        )

    def test_only_quote_backed_facts_reach_the_pack(self):
        search = FakeSearch()
        out = _run(
            fill_content(
                _one_liner(),
                _brief(8),
                self._gateway(),
                config=CFG,
                research_config=RC_WEB,
                search=search,
                fetch=_fake_fetch,
            )
        )
        web = out.web
        assert search.queries == ["рынок презентаций", "шаблоны"]  # дубль запроса убран
        assert web.pages_fetched == 1  # дубль URL с якорем не скачивается
        assert web.facts_kept == 2 and web.facts_rejected == 2
        facts = [
            i
            for s in out.pack.sections
            if s.id.startswith("sec-web")
            for b in s.blocks
            for i in b.items
        ]
        assert facts == [
            "Рынок программ для презентаций в 2025 году — 9,3 млрд долларов",
            "Подготовка слайдов отнимает до 30% времени аналитиков",
        ]
        sources = [s for s in out.pack.sections if s.heading == "Источники"]
        assert len(sources) == 1
        entry = sources[0].blocks[0].items[0]
        assert "https://news.example.org/market" in entry and "дата обращения" in entry

        graph = build_evidence_graph(out.pack)
        web_nodes = [n for n in graph.nodes if n.origin and n.origin.value == "web"]
        assert web_nodes and all(n.confidence == 0.8 for n in web_nodes)
        report = out.to_dict()
        assert report["web"]["sources"][0]["url"] == "https://news.example.org/market"
        # веба не хватило до 8 слайдов — добрала модель
        assert out.model_sections > 0 and out.resolved == "expand"

    def test_search_outage_falls_back_to_model(self):
        class Down:
            async def search(self, query, limit, language):
                raise httpx.ConnectError("no route")

        out = _run(
            fill_content(
                _one_liner(),
                _brief(6),
                self._gateway(),
                config=CFG,
                research_config=RC_WEB,
                search=Down(),
                fetch=_fake_fetch,
            )
        )
        assert out.web.degraded == "no_search_results"
        assert out.resolved == "expand" and out.model_sections > 0

    def test_web_is_skipped_without_a_search_url(self):
        out = _run(
            fill_content(_one_liner(), _brief(6), self._gateway(), config=CFG, research_config=RC)
        )
        assert out.web is None and out.model_sections > 0


class TestVerifyFact:
    def test_quote_must_be_verbatim(self):
        page = "Выручка выросла на 12% в 2024 году, сообщила компания."
        ok = ExtractedFact(
            text="Выручка +12% в 2024", quote="выросла на 12% в 2024 году", topic="t"
        )
        para = ExtractedFact(text="Выручка +12%", quote="увеличилась на 12% в 2024 году", topic="t")
        assert verify_fact(ok, page)
        assert not verify_fact(para, page)

    def test_typography_differences_are_tolerated(self):
        page = "Проект «Альфа» — лидер рынка."
        fact = ExtractedFact(text="Альфа лидирует", quote='Проект "Альфа" - лидер рынка', topic="t")
        assert verify_fact(fact, page)

    def test_numbers_must_come_from_the_quote(self):
        page = "Доля рынка составила 1 200 клиентов и 15%."
        quote = "Доля рынка составила 1 200 клиентов"
        good = ExtractedFact(text="1200 клиентов", quote=quote, topic="t")
        bad = ExtractedFact(text="1500 клиентов", quote=quote, topic="t")
        assert verify_fact(good, page)
        assert not verify_fact(bad, page)


class TestPages:
    def test_html_to_text_drops_navigation_and_scripts(self):
        html = """<html><head><title>Обзор</title>
        <script>var x = 'инструкция модели';</script></head>
        <body><nav><li>Главная страница сайта и меню навигации</li></nav>
        <p>Содержательный абзац статьи о рынке презентаций в компаниях.</p>
        <footer><p>Все права защищены, подвал сайта с контактами</p></footer></body></html>"""
        title, text = html_to_text(html)
        assert title == "Обзор"
        assert text == "Содержательный абзац статьи о рынке презентаций в компаниях."

    def test_private_hosts_are_never_fetched(self):
        calls = []

        def handler(request):
            calls.append(request.url)
            return httpx.Response(200, text="x")

        page = _run(
            fetch_page(
                "http://127.0.0.1/admin",
                WebResearchConfig(),
                transport=httpx.MockTransport(handler),
            )
        )
        assert page is None and not calls

    def test_redirects_and_oversized_pages_are_rejected(self, monkeypatch):
        import deckdna.research.web as web

        async def public(host):
            return True

        monkeypatch.setattr(web, "_host_is_public", public)
        body = "<p>" + "Длинный содержательный текст страницы о презентациях. " * 20 + "</p>"

        def handler(request):
            if request.url.path == "/moved":
                return httpx.Response(302, headers={"location": "http://169.254.169.254/"})
            if request.url.path == "/huge":
                return httpx.Response(
                    200, headers={"content-type": "text/html"}, text="<p>" + "x" * 6000 + "</p>"
                )
            return httpx.Response(
                200, headers={"content-type": "text/html; charset=utf-8"}, text=body
            )

        cfg = WebResearchConfig(max_page_bytes=4000)
        transport = httpx.MockTransport(handler)
        assert _run(fetch_page("https://site.example/moved", cfg, transport=transport)) is None
        assert _run(fetch_page("https://site.example/huge", cfg, transport=transport)) is None
        page = _run(fetch_page("https://site.example/ok", cfg, transport=transport))
        assert page is not None and "презентациях" in page.text


def test_research_respects_the_time_budget():
    async def slow_fetch(url):
        await asyncio.sleep(5)
        return None

    gw = MockProvider(
        fixtures={
            QUERIES_PROMPT: {"queries": ["рынок"]},
            EXTRACT_PROMPT: {"facts": []},
        }
    )
    rc = ResearchConfig(
        budget_s=0.5,
        web=WebResearchConfig(search_url="http://s"),
        model=ModelFillConfig(enabled=False),
    )
    started = time.monotonic()
    out = _run(
        fill_content(
            _one_liner(),
            _brief(6),
            gw,
            config=CFG,
            research_config=rc,
            search=FakeSearch(),
            fetch=slow_fetch,
        )
    )
    assert time.monotonic() - started < 3
    assert out.web.degraded == "no_pages"
    assert out.resolved == "expand_failed"
    plan = plan_deck(out.pack, out.brief, config=CFG)
    assert len(plan.slides) == 3  # честно короче, без заглушек


class TestBeHonestNoFabricatedData:
    def test_model_expand_filters_ungrounded_numbers_and_demo_categories(self):
        def noisy_numbers(payload):
            return {
                "sections": [
                    {
                        "heading": "Предположение: Операционная модель ритейлера",
                        "bullets": [
                            "Выручка компании выросла на 24,5% и достигла 850 млрд рублей.",
                            "Направление A лидирует по выручке (100 усл. ед.), а D — по динамике.",
                            "Федеральная сеть объединяет магазины и распределительные центры.",
                            "Логистика опирается на прямые контракты с производителями техники.",
                            "Предположение: омниканальные продажи сочетают заказ и выдачу.",
                        ],
                    }
                ]
            }

        gw = MockProvider(fixtures={EXPAND_PROMPT: noisy_numbers})
        out = _run(fill_content(_one_liner(), _brief(6), gw, config=CFG, research_config=RC))
        model_secs = [s for s in out.pack.sections if s.id.startswith("sec-model")]
        assert len(model_secs) == 1
        assert model_secs[0].heading == "Операционная модель ритейлера"
        items = model_secs[0].blocks[0].items
        joined = " ".join(items)
        assert "24,5%" not in joined
        assert "850 млрд" not in joined
        assert "Направление A" not in joined
        assert "усл. ед" not in joined
        assert "Предположение:" not in joined
        assert len(items) == 3

    def test_analytics_brief_without_data_returns_honest_missing_data_status(self):
        brief = Brief(
            purpose="DNS - аналитика компании",
            audience="Совет директоров",
            language="ru",
            target_slide_count=11,
            content_fill=ContentFill.expand,
        )
        pack = ContentPack(
            schema_version="1.0",
            id="pack-dns",
            language="ru",
            sections=[
                Section(
                    id="s0",
                    heading="Идея",
                    level=2,
                    blocks=[
                        Block(
                            kind=Kind.paragraph,
                            text="DNS - аналитика компании.",
                            source_ref=SourceRef(artifact_id="brief.md"),
                        )
                    ],
                )
            ],
            tables=[],
            assets=[],
            warnings=[],
        )
        out = _run(fill_content(pack, brief, None, config=CFG, research_config=RC))
        assert not out.pack.charts
        assert not out.pack.tables
        missing_secs = [s for s in out.pack.sections if s.id == "sec-missing-data"]
        assert len(missing_secs) == 1
        assert missing_secs[0].heading == "Каких данных не хватает"
        report = out.to_dict()
        assert report["honest_status"] == "missing_data"
        assert report["built_slides"] < 11
        assert report["shortfall_slides"] == 11 - report["built_slides"]
        assert report["shortfall_reason"]
        assert len(report["missing_data_questions"]) >= 3
