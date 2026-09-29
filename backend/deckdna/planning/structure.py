"""Stage 1 of ADR-016 (Structure -> Writer -> Layout-fit -> Audit): deck
structure only.

``plan_deck_llm`` (story_director.py) does two jobs in one call today:
decide the slide's STRUCTURAL role (purpose, which evidence it draws on)
and WRITE its content (title_intent, key_message) -- then content_units
are filled in verbatim from evidence (ADR-012), and a further, separate
proofreading pass (content_style, ADR-014) tries to make that verbatim
text read well.

ADR-016 splits "what role this slide plays" from "what it actually says":
this module answers ONLY the first question. ``plan_structure_llm``
returns a lightweight ``DeckStructure`` -- purpose, an evidence pool, and
a one-line ``slide_brief`` (the slide's JOB, not its wording) per slide.
No title, no body: Stage 2 (a later module) writes those FROM this
skeleton, informed by the evidence pool instead of citing it verbatim.

Not yet wired into ``generate()`` -- this is Phase A of ADR-016, built and
tested in isolation. ``plan_deck_llm`` is unchanged and remains the
production path until Stage 2 exists to consume this module's output.

Same reliability discipline as ``plan_deck_llm`` (ADR-012): baseline
``plan_deck()`` computed first (guarantees slide count/order/valid
fallback), slides batched in small independent, parallel calls
(``STRUCTURE_BATCH_SIZE``) with an honest per-slide fallback to the
baseline's own evidence assignment on any batch failure or grounding
rejection -- never all-or-nothing.
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel, Field

from deckdna.contracts.content_pack import ContentPack
from deckdna.contracts.deck_plan import Brief, Kind, Purpose, SlidePlan
from deckdna.contracts.design_dna import DesignDNA
from deckdna.contracts.evidence_graph import EvidenceGraph
from deckdna.contracts.serialize import to_schema_dict
from deckdna.contracts.variant_spec import Strategy
from deckdna.planning.config import GenerationConfig
from deckdna.planning.evidence import build_evidence_graph
from deckdna.planning.story_director import _PROTECTED_OUTLINE_PURPOSES, plan_deck
from deckdna.planning.validation import validate_slide_count
from deckdna.providers.base import ModelGateway

logger = logging.getLogger(__name__)

STRUCTURE_PROMPT = "deck_structure"
STRUCTURE_BATCH_SIZE = 5
MAX_CONCURRENT_STRUCTURE_CALLS = 4

# section_divider — легитимно content-free bookend (см. story_director.py's
# _inject_dividers/generic-divider последний резерв: title-юнит, ноль body).
# SlideStructureItem.evidence_ids требует min_length=1 -- у настоящего
# divider'а честного evidence-пула нет, так что модель вынуждена
# придумать/натянуть evidence_id и slide_brief, описывающий саму РОЛЬ
# слайда ("Слайд задаёт маршрут: от проблемы к решению..."). Это
# slide_brief затем может утечь как title через content_writer's
# fallback (_fallback_units возвращает slide_brief как title_intent),
# давая title-only слайд с обманчиво-«содержательным» заголовком.
# Живой баг, найден на 3 сгенерированных декax.
# Fix: dividers protected тем же способом, что title/thank_you/cta --
# никогда не уходят в LLM, Stage 1 честно копирует baseline.
_STRUCTURE_PROTECTED_PURPOSES = _PROTECTED_OUTLINE_PURPOSES | frozenset({Purpose.section_divider})


class SlideStructureItem(BaseModel):
    """One slide's structural role from the batch response — no title, no
    body, see the module docstring for why."""

    index: int
    purpose: Purpose
    slide_brief: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)


class SlideStructureBatch(BaseModel):
    slides: list[SlideStructureItem] = Field(min_length=1)


class SlideStructure(BaseModel):
    """One slide's finalized Stage-1 structure (post grounding-filter)."""

    id: str
    index: int
    purpose: Purpose
    slide_brief: str
    evidence_ids: list[str]
    mandatory: bool | None = None


class DeckStructure(BaseModel):
    """Stage-1 output: a deck-level skeleton with no written content yet.

    Internal to the pipeline, not a public API/contract schema — Stage 2
    (content writer, not yet built) and Stage 3 (layout-fit, not yet
    built) are meant to consume this directly, in-process."""

    id: str
    brief: Brief
    evidence_graph_id: str
    language: str
    slides: list[SlideStructure] = Field(min_length=1)
    used_llm: int = 0
    total_slides: int = 0


def _fallback_structure(base: SlidePlan) -> SlideStructure:
    """Baseline slide -> SlideStructure, for slides the model never saw
    (protected bookends) or where its batch failed/was rejected. The
    baseline's own key_message (or, failing that, its title) stands in
    for slide_brief — not as good as a real model-written job
    description, but an honest, already-grounded placeholder."""
    return SlideStructure(
        id=base.id,
        index=base.index,
        purpose=base.purpose,
        slide_brief=base.key_message or base.title_intent,
        evidence_ids=list(base.evidence_ids),
        mandatory=base.mandatory,
    )


def _compact_graph(graph: EvidenceGraph) -> dict:
    """Граф фактов для промпта без служебных полей.

    ``source_ref``/``confidence`` модели для выбора пула не нужны, а
    рёбра ``belongs_to`` сворачиваются в поле ``section`` узла: полный
    граф 15-страничного PDF — ~10k токенов на каждый батч, что не
    проходит лимит запроса у провайдеров с малым окном (Groq free: 413)
    и впустую тратит токены у остальных."""
    parent = {
        e["from"]: e["to"]
        for e in to_schema_dict(graph).get("edges", [])
        if e.get("type") == "belongs_to"
    }
    nodes = []
    for n in graph.nodes:
        node = {"id": n.id, "type": getattr(n.type, "value", n.type), "text": n.text}
        if n.id in parent:
            node["section"] = parent[n.id]
        nodes.append(node)
    return {"id": graph.id, "nodes": nodes}


async def _run_structure_batch(
    gateway: ModelGateway,
    batch_slides: list[SlidePlan],
    *,
    brief_payload: dict,
    graph_payload: dict,
    capacities_payload: dict | None,
    deck_context: list[dict],
    known_evidence_ids: set[str],
    sem: asyncio.Semaphore,
) -> dict[int, SlideStructureItem]:
    """One batch call. Returns {index: item} only for slides the model
    answered validly for AND grounded in real evidence ids — partial
    success is returned as-is; the caller falls back per-slide for the
    rest, the same discipline as plan_deck_llm's outline batches."""
    wanted = {s.index for s in batch_slides}
    payload = {
        "brief": brief_payload,
        "evidence_graph": graph_payload,
        "design_dna_capacities": capacities_payload,
        "deck_context": deck_context,
        "batch": {
            "indices": sorted(wanted),
            "hints": [
                {
                    "index": s.index,
                    "purpose": s.purpose.value,
                    "baseline_evidence_count": len(s.evidence_ids),
                    "baseline_evidence_ids": s.evidence_ids,
                }
                for s in batch_slides
            ],
        },
    }
    async with sem:
        try:
            out = await gateway.text_json(STRUCTURE_PROMPT, payload, SlideStructureBatch)
        except Exception as exc:  # noqa: BLE001 — любой сбой провайдера = fallback этих слайдов
            logger.warning(
                "structure batch %s failed (%s); baseline evidence pool kept for these slides",
                sorted(wanted),
                exc,
            )
            return {}
    if not isinstance(out, SlideStructureBatch):
        return {}
    result: dict[int, SlideStructureItem] = {}
    for item in out.slides:
        if item.index not in wanted or item.index in result:
            continue  # вне батча или дубль индекса — честно отклоняем именно этот item
        grounded = [eid for eid in item.evidence_ids if eid in known_evidence_ids]
        if not grounded:
            continue  # ни одной настоящей ссылки — пулу не доверяем
        result[item.index] = item.model_copy(update={"evidence_ids": grounded})
    return result


async def plan_structure_llm(
    pack: ContentPack,
    brief: Brief,
    gateway: ModelGateway,
    design_dna: DesignDNA | None = None,
    config: GenerationConfig | None = None,
    strategy: Strategy = Strategy.balanced,
) -> DeckStructure:
    """Stage 1 (ADR-016): purpose + evidence pool + one-line slide_brief
    per slide. See the module docstring for the full design rationale."""
    cfg = config
    if cfg is None:
        from deckdna.planning.config import load_generation_config

        cfg = load_generation_config()
    validate_slide_count(brief.target_slide_count, cfg)

    baseline = plan_deck(pack, brief, cfg, strategy=strategy)
    graph: EvidenceGraph = build_evidence_graph(pack)
    known_evidence_ids = {n.id for n in graph.nodes}
    capacities = design_dna.capacities if design_dna else None

    # The deterministic planner uses pack:block ids; Stage 2 consumes graph
    # ids. Resolve these before either the prompt or fallback uses them.
    aliases: dict[str, list[str]] = {}
    for si, section in enumerate(pack.sections):
        for bi, block in enumerate(section.blocks):
            candidates = [f"ev_blk_{si}_{bi}"] + [
                f"ev_lst_{si}_{bi}_{k}" for k in range(len(block.items or []))
            ]
            aliases[f"{pack.id}:{section.id}:b{bi}"] = [
                eid for eid in candidates if eid in known_evidence_ids
            ]
    mapped_slides = []
    protected_by_index: dict[int, list[str]] = {}
    for base in baseline.slides:
        resolved = list(dict.fromkeys(
            eid for old in base.evidence_ids
            for eid in aliases.get(old, [old])
        ))
        protected_by_index[base.index] = list(dict.fromkeys(
            eid for unit in base.content_units
            if unit.role == "equation" or unit.kind in {Kind.table, Kind.chart, Kind.diagram}
            for old in unit.evidence_ids or [] for eid in aliases.get(old, [])
        ))
        mapped_slides.append(base.model_copy(update={"evidence_ids": resolved}))
    baseline = baseline.model_copy(update={"slides": mapped_slides})

    eligible = [s for s in baseline.slides if s.purpose not in _STRUCTURE_PROTECTED_PURPOSES]
    if not eligible:
        return DeckStructure(
            id=baseline.id,
            brief=brief,
            evidence_graph_id=graph.id,
            language=baseline.language,
            slides=[_fallback_structure(s) for s in baseline.slides],
            used_llm=0,
            total_slides=len(baseline.slides),
        )

    brief_payload = to_schema_dict(brief)
    graph_payload = _compact_graph(graph)
    capacities_payload = to_schema_dict(capacities) if capacities else None
    deck_context = [
        {"index": s.index, "purpose": s.purpose.value, "title_intent": s.title_intent}
        for s in baseline.slides
    ]

    batches = [
        eligible[i : i + STRUCTURE_BATCH_SIZE]
        for i in range(0, len(eligible), STRUCTURE_BATCH_SIZE)
    ]
    sem = asyncio.Semaphore(MAX_CONCURRENT_STRUCTURE_CALLS)
    batch_results = await asyncio.gather(
        *(
            _run_structure_batch(
                gateway,
                batch,
                brief_payload=brief_payload,
                graph_payload=graph_payload,
                capacities_payload=capacities_payload,
                deck_context=deck_context,
                known_evidence_ids=known_evidence_ids,
                sem=sem,
            )
            for batch in batches
        )
    )
    by_index: dict[int, SlideStructureItem] = {}
    for batch_result in batch_results:
        by_index.update(batch_result)

    slides: list[SlideStructure] = []
    used_llm = 0
    for base in baseline.slides:
        item = by_index.get(base.index)
        if item is None:
            slides.append(_fallback_structure(base))
            continue
        slides.append(
            SlideStructure(
                id=base.id,
                index=base.index,
                purpose=item.purpose,
                slide_brief=item.slide_brief,
                evidence_ids=list(dict.fromkeys(
                    item.evidence_ids + protected_by_index[base.index]
                )),
                mandatory=base.mandatory,
            )
        )
        used_llm += 1

    logger.info(
        "Stage 1 structure: %d/%d slides from the model, %d deterministic (baseline/fallback)",
        used_llm,
        len(slides),
        len(slides) - used_llm,
    )
    return DeckStructure(
        id=baseline.id,
        brief=brief,
        evidence_graph_id=graph.id,
        language=baseline.language,
        slides=slides,
        used_llm=used_llm,
        total_slides=len(slides),
    )
