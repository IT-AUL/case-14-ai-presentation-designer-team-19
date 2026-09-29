"""OR-007: три реально различных варианта (faithful/balanced/visual).

Доказательство на organizer-шаблоне и реальном контенте: один и тот же
вход, три стратегии — три побитово разных deck.pptx (не только разные
имена файлов). Различия: faithful — без agenda-слайда и recap-инъекций
в плане; visual — переранжированный exemplar pool (визуально-насыщенные
слайды впереди, richness взвешен в exemplar-подборе — см.
select_exemplar_slides), что на практике требует меньше shrink-to-fit
проходов, чем text-first подбор balanced (29.09: более ранний
drop-based кап плотности текста заменён capacity-aware переподбором
exemplar'ов).
"""

import hashlib
import shutil
from pathlib import Path

import pytest
from deckdna.contracts.deck_plan import Brief
from deckdna.contracts.variant_spec import Strategy
from deckdna.generation.pipeline import generate
from deckdna.ingestion.content_parsers import parse_file
from deckdna.planning.story_director import plan_deck
from deckdna.pptx.cloning.exemplar import _ranked_candidate_pool
from deckdna.pptx.opc.package import OpcPackage

FIXTURE = Path("tests/fixtures/pptx/vk_tech_template.pptx")
CONTENT = Path("tests/fixtures/content/poc_article.md")

BRIEF = {
    "purpose": "Показать три варианта колоды",
    "audience": "эксперты VK Tech",
    "language": "ru",
    "target_slide_count": 12,
}

needs_render = pytest.mark.skipif(
    shutil.which("soffice") is None or not FIXTURE.exists(),
    reason="generation pipeline needs the fixture + soffice",
)

STRATEGIES = (Strategy.faithful, Strategy.balanced, Strategy.visual)


@pytest.fixture(scope="module")
def reports(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("variants")
    out = {}
    for s in STRATEGIES:
        out[s.value] = generate(FIXTURE, CONTENT, BRIEF, tmp / s.value, strategy=s)
    return out


def _sha256(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_planning_differs_by_strategy():
    """faithful vs balanced: разные DeckPlan — нет agenda, другой layout
    распределения юнитов; visual == balanced на стадии планирования
    (его различия — exemplar и плотность текста)."""
    pack = parse_file(CONTENT)
    brief = Brief.model_validate(BRIEF)
    plans = {s.value: plan_deck(pack, brief, strategy=s) for s in STRATEGIES}

    faithful_purposes = [sl.purpose.value for sl in plans["faithful"].slides]
    balanced_purposes = [sl.purpose.value for sl in plans["balanced"].slides]
    assert "agenda" not in faithful_purposes
    assert "agenda" in balanced_purposes
    assert faithful_purposes != balanced_purposes
    assert [sl.purpose.value for sl in plans["visual"].slides] == balanced_purposes
    # разные планы — разные id (нет коллизии в записи)
    assert len({p.id for p in plans.values()}) == 3


def test_visual_reorders_exemplar_pool():
    """На organizer-шаблоне visual-порядок отличается от text-first:
    визуально-насыщенные слайды (pic/graphicFrame) идут первыми."""
    pkg = OpcPackage.open(FIXTURE)
    balanced_rank, _, _, _ = _ranked_candidate_pool(pkg, Strategy.balanced)
    visual_rank, _, _, _ = _ranked_candidate_pool(pkg, Strategy.visual)
    assert balanced_rank != visual_rank
    assert set(balanced_rank) == set(visual_rank)  # тот же пул, другой порядок


@needs_render
def test_three_strategies_produce_distinct_decks(reports):
    """Три стратегии → три побитово разных pptx на одном входе."""
    hashes = {
        name: _sha256(report["artifacts"]["pptx"]) for name, report in reports.items()
    }
    assert len(set(hashes.values())) == 3


@needs_render
def test_strategy_surfaced_and_honest(reports):
    for name, report in reports.items():
        assert report["strategy"] == name
        assert report["compose_report"]["strategy"] == name


@needs_render
def test_visual_needs_less_text_shrinking(reports):
    """visual's own text-density-cap-by-dropping mechanism (this test's
    prior name/assertions) was superseded by the exemplar-capacity-aware
    reassignment (29.09, see exemplar.py::select_exemplar_slides): visual
    now supplies the SAME text units as balanced (no drop), but its
    richness-weighted exemplar picks tend to have adequate body capacity
    already, so noticeably fewer bodies need the shrink-to-fit pass than
    balanced's text-first picks -- still an honest, measurable signal
    that visual and balanced treat text density differently, just via a
    different mechanism than a hard per-unit drop."""
    visual = reports["visual"]["compose_report"]
    balanced = reports["balanced"]["compose_report"]
    visual_shrunk = sum(s["text"]["bodies_shrunk"] for s in visual["slides"])
    balanced_shrunk = sum(s["text"]["bodies_shrunk"] for s in balanced["slides"])
    assert visual_shrunk < balanced_shrunk
    visual_supplied = sum(s["text"]["texts_supplied"] for s in visual["slides"])
    balanced_supplied = sum(s["text"]["texts_supplied"] for s in balanced["slides"])
    assert visual_supplied == balanced_supplied


@needs_render
def test_visual_uses_different_exemplars(reports):
    """visual назначает exemplar-слайды в другом порядке, чем text-first
    стратегии (пул тот же — колода длиннее пула и циклится, поэтому
    различие именно в перестановке назначений)."""
    visual_parts = [
        s["exemplar"]["slide_part"] for s in reports["visual"]["compose_report"]["slides"]
    ]
    balanced_parts = [
        s["exemplar"]["slide_part"]
        for s in reports["balanced"]["compose_report"]["slides"]
    ]
    assert visual_parts != balanced_parts


@needs_render
def test_faithful_deck_has_no_agenda_slide(reports):
    faithful = reports["faithful"]["compose_report"]
    purposes = [s["purpose"] for s in faithful["slides"]]
    assert "agenda" not in purposes
    assert "agenda" in [
        s["purpose"] for s in reports["balanced"]["compose_report"]["slides"]
    ]


@needs_render
def test_generate_accepts_strategy_as_string(tmp_path):
    """generate(strategy="faithful") — строковое имя коэрсится в Strategy
    по аналогии с brief: Brief | dict. Раньше падало AttributeError."""
    report = generate(FIXTURE, CONTENT, BRIEF, tmp_path / "gen", strategy="faithful")
    assert report["strategy"] == "faithful"


def test_generate_rejects_unknown_strategy_string(tmp_path):
    """Неизвестная строка — честный ValueError на входе (как
    ValidationError у кривого brief), не AttributeError по дороге."""
    with pytest.raises(ValueError, match="not-a-strategy"):
        generate(
            FIXTURE, CONTENT, BRIEF, tmp_path / "gen", strategy="not-a-strategy"
        )
