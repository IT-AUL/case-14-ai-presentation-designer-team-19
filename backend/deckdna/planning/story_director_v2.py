"""ADR-016 assembly: Stage 1 (structure.py) + Stage 2 (content_writer.py)
combined into a full ``DeckPlan`` — the drop-in ADR-016 replacement for
``story_director.py::plan_deck_llm``.

Same public contract as ``plan_deck_llm`` (pack, brief, gateway, optional
design_dna/config/strategy -> ``DeckPlan``, same ``Provenance`` semantics)
so every existing caller (``generation/pipeline.py``, ``api/app.py``)
works unchanged — see ``configs/generation.default.yaml``'s
``planning.pipeline_version`` for how a caller picks between this and the
v1 (verbatim-evidence) planner.

What changes vs. v1: content_units are SYNTHESIZED per slide (Stage 2,
one call per slide, run in full parallel) from an evidence POOL Stage 1
selected, instead of quoted verbatim from a single, smaller evidence_ids
selection made in the same call as title_intent/key_message. Everything
downstream (exemplar selection, composing, content_style, text_fit,
contextual audit) is unchanged — it only ever sees a ``DeckPlan``, and
this still produces one.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging

from deckdna.contracts.content_pack import ContentPack
from deckdna.contracts.deck_plan import Brief, DeckPlan, Kind, Provenance, SlidePlan
from deckdna.contracts.design_dna import DesignDNA
from deckdna.contracts.variant_spec import Strategy
from deckdna.planning import content_writer
from deckdna.planning.config import GenerationConfig
from deckdna.planning.evidence import build_evidence_graph
from deckdna.planning.story_director import (
    LLM_PLANNER_VERSION,
    PLANNER_VERSION,
    SCHEMA_VERSION,
    _density,
    _desired_visual,
    plan_deck,
)
from deckdna.planning.structure import (
    _STRUCTURE_PROTECTED_PURPOSES,
    STRUCTURE_PROMPT,
    plan_structure_llm,
)
from deckdna.planning.validation import validate_deck_plan
from deckdna.providers.base import ModelGateway
from deckdna.providers.profiles import ModelProfileSource
from deckdna.providers.prompts import load_prompt

logger = logging.getLogger(__name__)

ADR016_PLANNER_VERSION = "story-director/adr016-structure-writer-0.1.0"


async def _slide_task(
    idx: int,
    base_by_index: dict[int, SlidePlan],
    struct_by_index: dict,
    gateway: ModelGateway,
    nodes_by_id: dict,
    capacities,
    language: str,
    can_write: bool,
    sem: asyncio.Semaphore,
) -> tuple[int, str, str, list, bool]:
    base = base_by_index[idx]
    source_heading = base.title_intent.casefold().strip()
    if source_heading.startswith((
        "references", "bibliography", "источники", "список источников", "литература"
    )):
        # Bibliography is structured source data, not a writing prompt.
        # Keep every grounded entry in display form from ingestion and
        # prevent the writer from turning it into a prose fact dump.
        continuation = any(word in source_heading for word in ("часть", "продолжение", "part"))
        title = "Источники — продолжение" if continuation else "Источники"
        units = [
            u.model_copy(update={"text": title}) if u.kind == Kind.title else u
            for u in base.content_units
        ]
        return idx, title, title, units, False
    if base.purpose in _STRUCTURE_PROTECTED_PURPOSES or not can_write:
        # protected bookends: never touched, same as v1 (plan_deck_llm).
        # section_divider included (see structure.py's
        # _STRUCTURE_PROTECTED_PURPOSES) -- a legitimately content-free
        # transition slide has no honest evidence pool, forcing Stage 1's
        # schema-mandated evidence_ids to be fabricated otherwise.
        # !can_write (untrusted/mock gateway without a fixture): honest
        # deterministic content, same spirit as v1's own grounding gate.
        return idx, base.title_intent, base.key_message, base.content_units, False
    struct = struct_by_index[idx]
    title, key_message, units, used = await content_writer.write_slide_content(
        gateway, struct, nodes_by_id, capacities, language, sem
    )
    # Structured objects belong to their source section. The writer's
    # prose response cannot replace references to native table/chart/diagram
    # objects or equations, even if Stage 1 selected other evidence.
    for original in base.content_units:
        if original.role == "equation" or original.kind in {Kind.table, Kind.chart, Kind.diagram}:
            if not any(
                u.kind == original.kind and u.text == original.text
                and u.table_ref == original.table_ref and u.chart_ref == original.chart_ref
                and u.diagram_ref == original.diagram_ref for u in units
            ):
                units.append(original)
    if not units:
        # content_writer couldn't produce ANYTHING usable -- typically a
        # slide (e.g. agenda/meta) whose baseline evidence_ids reference
        # something other than real EvidenceGraph nodes (section ids, not
        # claim/entity/number nodes), so its pool never resolves to real
        # text no matter what content_writer tries. Its own (slide_brief,
        # slide_brief, []) placeholder is not a real title/key_message --
        # fall back to the actual baseline slide instead of publishing a
        # half-built result with the wrong words duplicated into title.
        return idx, base.title_intent, base.key_message, base.content_units, False
    if not used:
        # slide_brief describes the writer's JOB ("дать вторую часть
        # списка..."); it is never presentation copy.  A writer fallback
        # retains the complete deterministic slide, including structured
        # objects and qualifications, rather than a capped evidence excerpt.
        return idx, base.title_intent, base.key_message, base.content_units, False
    return idx, title, key_message, units, used


async def plan_deck_llm_v2(
    pack: ContentPack,
    brief: Brief,
    gateway: ModelGateway,
    design_dna: DesignDNA | None = None,
    config: GenerationConfig | None = None,
    strategy: Strategy = Strategy.balanced,
) -> DeckPlan:
    """ADR-016 Stage 1 + Stage 2 -> DeckPlan. See module docstring."""
    cfg = config
    if cfg is None:
        from deckdna.planning.config import load_generation_config

        cfg = load_generation_config()

    baseline = plan_deck(pack, brief, cfg, strategy=strategy)
    graph = build_evidence_graph(pack)
    nodes_by_id = {n.id: n for n in graph.nodes}
    capacities = design_dna.capacities if design_dna else None

    structure = await plan_structure_llm(pack, brief, gateway, design_dna, cfg, strategy)
    struct_by_index = {s.index: s for s in structure.slides}
    base_by_index = {s.index: s for s in baseline.slides}

    can_write = content_writer.can_rewrite(gateway)
    sem = asyncio.Semaphore(content_writer.MAX_CONCURRENT_WRITER_CALLS)

    results = await asyncio.gather(
        *(
            _slide_task(
                idx,
                base_by_index,
                struct_by_index,
                gateway,
                nodes_by_id,
                capacities,
                baseline.language,
                can_write,
                sem,
            )
            for idx in base_by_index
        )
    )

    slides: list[SlidePlan] = []
    llm_used = 0
    for idx, title, key_message, units, used in sorted(results, key=lambda r: r[0]):
        base = base_by_index[idx]
        evidence_ids = [eid for u in units for eid in (u.evidence_ids or [])] or list(
            base.evidence_ids
        )
        slides.append(
            SlidePlan(
                id=base.id,
                index=idx,
                purpose=base.purpose,
                title_intent=title,
                key_message=key_message,
                evidence_ids=evidence_ids,
                content_units=units or base.content_units,
                desired_visual=(_desired_visual(units) if units else base.desired_visual),
                density_budget=(_density(units) if units else base.density_budget),
                speaker_note=base.speaker_note,
                mandatory=base.mandatory,
            )
        )
        if used:
            llm_used += 1

    plan = baseline.model_copy(update={"slides": slides})
    structure_version = load_prompt(STRUCTURE_PROMPT).version
    writer_version = load_prompt(content_writer.CONTENT_WRITER_PROMPT).version
    plan.provenance = Provenance(
        planner=LLM_PLANNER_VERSION if llm_used else PLANNER_VERSION,
        prompt_version=f"deck_structure/{structure_version}+content_writer/{writer_version}",
        schema_version=SCHEMA_VERSION,
        model_id=(
            gateway.used_model_ids().get("text")
            if isinstance(gateway, ModelProfileSource)
            else None
        ),
        input_hashes=[
            hashlib.sha256(pack.model_dump_json().encode()).hexdigest(),
            hashlib.sha256(graph.model_dump_json().encode()).hexdigest(),
        ],
    )
    plan.evidence_graph_id = graph.id
    # структура унаследована от baseline — это страховка, отказа не ожидаем
    validate_deck_plan(plan, cfg)
    logger.info(
        "ADR-016 Stage 1+2: %d/%d slides from the model, %d deterministic (baseline/fallback)",
        llm_used,
        len(slides),
        len(slides) - llm_used,
    )
    return plan
