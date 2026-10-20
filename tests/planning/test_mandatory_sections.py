"""Required source sections need substantive, traceable content, not a title."""
import pytest
from deckdna.assistant.plan_edit import apply_action
from deckdna.contracts.content_pack import Block, ContentPack, Section, SourceRef
from deckdna.contracts.deck_plan import Brief
from deckdna.contracts.variant_spec import Strategy
from deckdna.errors import DeckDNAError
from deckdna.planning.story_director import plan_deck

ANCHOR = "В пилоте проверен один результат. Гарантий повторения пока нет."


def pack(*, empty_risks=False):
    src = SourceRef(artifact_id="author.md")
    return ContentPack(schema_version="1.0", id="mandatory-pack", language="ru",
                       title_hint="Обзор", sections=[
        Section(id="work", heading="Работа", level=2, blocks=[
            Block(kind="paragraph", text="Команда подготовила обзор результатов пилота.",
                  source_ref=src),
        ]),
        Section(id="risks", heading="Риски", level=2, blocks=[] if empty_risks else [
            Block(kind="paragraph", text=ANCHOR, source_ref=src),
        ]),
    ], tables=[], assets=[], warnings=[])


def brief(required):
    return Brief(purpose="Обзор", audience="команда", language="ru", target_slide_count=4,
                 mandatory_sections=required)


@pytest.mark.parametrize("required", [["Риски"], ["risks"], ["  риски  "]])
def test_required_section_has_a_preserved_source_anchor(required):
    result = plan_deck(pack(), brief(required), strategy=Strategy.faithful)
    matching = [(s, u) for s in result.slides for u in s.content_units if u.text == ANCHOR]
    assert matching and any(u.locked for _, u in matching)
    assert all(s.mandatory for s, _ in matching)


def test_unknown_required_section_is_not_silently_ignored():
    with pytest.raises(DeckDNAError) as exc:
        plan_deck(pack(), brief(["Экономика"]), strategy=Strategy.faithful)
    assert exc.value.http_status == 422
    assert exc.value.details["rule"] == "request.mandatory_section"


def test_title_only_required_section_does_not_count_as_content():
    with pytest.raises(DeckDNAError) as exc:
        plan_deck(pack(empty_risks=True), brief(["Риски"]), strategy=Strategy.faithful)
    assert exc.value.details["rule"] == "request.mandatory_section"


def test_required_source_section_cannot_be_removed():
    source = pack()
    result = plan_deck(source, brief(["Риски"]), strategy=Strategy.faithful)
    slide = next(s for s in result.slides if any(u.text == ANCHOR for u in s.content_units))
    with pytest.raises(DeckDNAError) as exc:
        apply_action(result, slide.id, "remove", source)
    assert exc.value.details["rule"] in {"request.mandatory_section", "request.locked_copy"}


@pytest.mark.parametrize("heading", ["Следующие шаги", "Вопросы", "Источники", "Контакты"])
def test_required_topic_is_not_restricted_to_a_content_purpose(heading):
    source = pack()
    source.sections[1].heading = heading
    result = plan_deck(source, brief([heading]), strategy=Strategy.faithful)
    assert any(s.mandatory and any(u.text == ANCHOR and u.locked for u in s.content_units)
               for s in result.slides)


@pytest.mark.asyncio
async def test_unknown_required_source_is_rejected_before_fill_inference():
    from deckdna.providers.mock import MockProvider
    from deckdna.research.fill import fill_content

    gateway = MockProvider()
    with pytest.raises(DeckDNAError) as exc:
        await fill_content(pack(), brief(["Экономика"]), gateway)
    assert exc.value.details["rule"] == "request.mandatory_section"
    assert gateway.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["v1", "v2"])
async def test_fully_preserved_source_deck_needs_no_rewriting_calls(version):
    from deckdna.planning.story_director import plan_deck_llm
    from deckdna.planning.story_director_v2 import plan_deck_llm_v2
    from deckdna.providers.mock import MockProvider

    gateway = MockProvider(fixtures={"storyline": {}, "deck_structure": {}, "content_writer": {}})
    fn = plan_deck_llm if version == "v1" else plan_deck_llm_v2
    result = await fn(pack(), brief(["Работа", "Риски"]), gateway, strategy=Strategy.faithful)
    assert any(s.mandatory for s in result.slides)
    assert gateway.calls == []
