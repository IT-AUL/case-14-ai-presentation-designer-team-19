"""ADR-030: точечный ресёрч одного слайда плана — без сети (поиск и
загрузка страниц подменены, модель — MockProvider с fixture'ами)."""

from __future__ import annotations

import asyncio

from deckdna.contracts.content_pack import Block, ContentPack, Kind, Section, SourceRef
from deckdna.contracts.deck_plan import Brief
from deckdna.planning import plan_deck, validate_deck_plan
from deckdna.planning.complete import SLIDE_FILL_PROMPT
from deckdna.planning.config import DeckBounds, GenerationConfig
from deckdna.providers.mock import MockProvider
from deckdna.research.config import ResearchConfig, WebResearchConfig
from deckdna.research.slide import apply_to_plan, find_slide, research_slide
from deckdna.research.web import EXTRACT_PROMPT, QUERIES_PROMPT, Page, SearchHit

CFG = GenerationConfig(deck=DeckBounds(min_slides=3, max_slides=40))
BRIEF = Brief(purpose="Рынок презентаций", audience="руководство", language="ru",
              target_slide_count=4)
RC_WEB = ResearchConfig(budget_s=20, web=WebResearchConfig(search_url="http://s"))
PAGE = ("Рынок программ для презентаций в 2025 году оценивался в 9,3 млрд долларов. "
        "Ежегодный рост рынка составляет 12% по оценке аналитиков отрасли. "
        "Шаблоны помогают сохранять единый стиль компании. ") * 3


def _pack() -> ContentPack:
    return ContentPack(
        schema_version="1.0", id="pack-s", language="ru",
        sections=[Section(id=f"s{i}", heading=h, level=2, blocks=[Block(
            kind=Kind.list,
            items=[f"Пункт {k} раздела «{h}» с пояснением для руководства." for k in range(4)],
            source_ref=SourceRef(artifact_id="a.md"))])
            for i, h in enumerate(["Рынок", "Решение"])],
        tables=[], assets=[], warnings=[],
    )


class Search:
    async def search(self, query, limit, language):
        return [SearchHit(url="https://stats.example.org/market", title="Статистика")]


async def _fetch(url):
    return Page(url=url, title="Статистика рынка", text=PAGE)


def _gateway(focus_seen: list) -> MockProvider:
    def extract(payload):
        focus_seen.append(payload["focus"])
        return {"facts": [
            {"text": "Рынок в 2025 году — 9,3 млрд долларов",
             "quote": "в 2025 году оценивался в 9,3 млрд долларов", "topic": "Рынок"},
            {"text": "Шаблоны сохраняют единый стиль",
             "quote": "Шаблоны помогают сохранять единый стиль компании", "topic": "Стиль"},
        ]}

    return MockProvider(fixtures={
        QUERIES_PROMPT: lambda p: {"queries": [f"{p['focus']} рынок презентаций"]},
        EXTRACT_PROMPT: extract,
    })


def test_numbers_research_adds_only_figures_with_sources():
    plan = plan_deck(_pack(), BRIEF, config=CFG)
    slide = next(s for s in plan.slides if "Рынок" in s.title_intent)
    seen: list = []
    found = asyncio.run(research_slide(plan, slide, _pack(), plan.brief, _gateway(seen), RC_WEB,
                                       focus="numbers", search=Search(), fetch=_fetch))
    assert seen == ["numbers"]
    assert [f[0] for f in found.facts] == ["Рынок в 2025 году — 9,3 млрд долларов"]

    updated = apply_to_plan(plan, slide.id, found, "pack-new")
    new_slide = find_slide(updated, slide.id)
    added = [u for u in new_slide.content_units if u.origin and u.origin.value == "web"]
    assert [u.text for u in added] == ["Рынок в 2025 году — 9,3 млрд долларов"]
    assert added[0].evidence_ids[0].startswith("pack-new:sec-web-")
    sources = next(s for s in updated.slides if s.title_intent == "Источники")
    assert any("stats.example.org" in (u.text or "") for u in sources.content_units)
    assert updated.slides[-1].purpose.value in {"thank_you", "cta"}  # источники перед финалом
    assert updated.evidence_graph_id == "pack:pack-new"
    validate_deck_plan(updated, CFG)


def test_numbers_are_never_taken_from_model_knowledge():
    plan = plan_deck(_pack(), BRIEF, config=CFG)
    invented = {"bullets": ["Рынок вырос на 40% за год, это много"]}
    gw = MockProvider(fixtures={SLIDE_FILL_PROMPT: invented})
    found = asyncio.run(research_slide(plan, plan.slides[1], _pack(), plan.brief, gw,
                                       ResearchConfig(), focus="numbers"))
    assert not found.changed and found.degraded == "web_disabled"


def test_facts_fall_back_to_the_model_without_web():
    plan = plan_deck(_pack(), BRIEF, config=CFG)
    gw = MockProvider(fixtures={SLIDE_FILL_PROMPT: {"bullets": [
        "Первый содержательный пункт по теме слайда для руководства",
        "Второй содержательный пункт по теме слайда для руководства",
    ]}})
    found = asyncio.run(research_slide(plan, plan.slides[1], _pack(), plan.brief, gw,
                                       ResearchConfig(), focus="facts"))
    assert len(found.model_bullets) == 2
    updated = apply_to_plan(plan, plan.slides[1].id, found, "pack-s")
    origins = [u.origin.value for u in updated.slides[1].content_units if u.origin]
    assert origins == ["model", "model"]
    assert len(updated.slides) == len(plan.slides)  # без веб-источников слайд не добавляется
