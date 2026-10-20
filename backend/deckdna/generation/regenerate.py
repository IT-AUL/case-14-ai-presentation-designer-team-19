"""Доводка: пересборка одного слайда (ADR-034 §7).

Действия: ``other_layout`` (другой образец шаблона), ``shorter`` (вдвое
меньше пунктов), ``other_chart`` (другой тип из альтернатив, ``chart_type``
или следующий по баллу), ``style:<strategy>`` (образец, который выбрала бы
эта стратегия). Слайд верстается мини-колодой и переносится на место
старого (``slide_swap``) — остальные слайды байт-в-байт те же."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from deckdna.assistant.plan_edit import apply_action, change_chart
from deckdna.contracts.content_pack import ContentPack
from deckdna.contracts.deck_plan import ContentUnit, DeckPlan, Kind, Origin
from deckdna.contracts.design_dna import DesignDNA
from deckdna.contracts.variant_spec import Strategy
from deckdna.errors import DeckDNAError
from deckdna.planning.locked_copy import replacement_kinds, validate_locked_transition

ACTIONS = ("other_layout", "shorter", "other_chart", "replace_title", "replace_text",
           "larger_chart", "rebuild", "relayout")


@dataclass
class RegenerateResult:
    deck: bytes
    plan: DeckPlan
    pack: ContentPack
    preview_png: bytes | None
    exemplar: str
    action: str


def _alternative_part(
    template_path: Path, plan: DeckPlan, index: int, current: str | None, pack: ContentPack,
    strategy: Strategy, design_dna: DesignDNA | None,
) -> str:
    """Другой образец для слайда: сначала выбор других стратегий, затем
    ранжированный пул шаблона с теми же возможностями."""
    from deckdna.pptx.cloning.exemplar import (
        _ranked_candidate_pool,
        has_chart_room,
        slide_capabilities,
    )
    from deckdna.pptx.composing.minimal import plan_exemplar_choices
    from deckdna.pptx.opc.package import OpcPackage
    from deckdna.template.meta import meta_slides

    for other in (Strategy.visual, Strategy.balanced, Strategy.faithful):
        if other == strategy:
            continue
        part = plan_exemplar_choices(
            template_path, plan, tables=pack.tables, charts=pack.charts,
            diagrams=pack.diagrams, assets=pack.assets, strategy=other, design_dna=design_dna,
        )[index].slide_part
        if part != current:
            return part
    pkg = OpcPackage.open(template_path)
    slide = plan.slides[index]
    if slide.purpose.value == "title":
        from deckdna.pptx.composing.minimal import _local_design_dna

        dna = design_dna or _local_design_dna(pkg)
        title_parts = [
            ex.part
            for ex in sorted(dna.exemplars, key=lambda e: e.slide_index)
            if ex.role == "title" and ex.part in pkg.parts and not ex.content_is_template_meta
        ]
        for tp in title_parts:
            if tp != current:
                return tp
        if current and current in title_parts:
            return current
    data_chart = any(
        u.kind == Kind.chart and u.chart_spec and u.chart_spec.chart_type
        for u in slide.content_units
    )
    meta = set(meta_slides(pkg, [p for p in pkg.parts if p.startswith("ppt/slides/slide")]))
    ranked = list(_ranked_candidate_pool(pkg, strategy)[0])
    ranked += sorted(
        (p for p in pkg.parts if p.startswith("ppt/slides/slide") and p.endswith(".xml")
         and p not in ranked),
        key=lambda p: int("".join(ch for ch in p.rsplit("/", 1)[-1] if ch.isdigit()) or 0),
    )
    for part in ranked:
        if part == current or part in meta:
            continue
        if data_chart and not has_chart_room(pkg, part):
            continue
        if "chart" in slide_capabilities(pkg, part) and not data_chart:
            continue
        return part
    raise DeckDNAError("invalid_input", "the template has no other suitable layout",
                       http_status=422)


def regenerate_slide(
    template_path: str | Path,
    deck: bytes,
    plan: DeckPlan,
    pack: ContentPack,
    slide_id: str,
    position: int,
    action: str,
    *,
    strategy: str = "balanced",
    chart_type: str | None = None,
    text: str | None = None,
    current_exemplar: str | None = None,
    design_dna: DesignDNA | None = None,
    generated_images: dict[str, bytes] | None = None,
) -> RegenerateResult:
    from deckdna.pptx.composing.minimal import generate_deck, plan_exemplar_choices
    from deckdna.pptx.composing.slide_swap import swap_slide
    from deckdna.pptx.exporting.previews import pdf_page_pngs
    from deckdna.pptx.exporting.render import render_pdf

    template_path = Path(template_path)
    index = next((i for i, s in enumerate(plan.slides) if s.id == slide_id), None)
    if index is None:
        raise DeckDNAError("not_found", f"slide not in plan: {slide_id}")
    strat = Strategy(strategy)
    new_plan, new_pack = plan, pack
    style = None
    if action.startswith("style:"):
        style = action.split(":", 1)[1]
        if style not in ("faithful", "balanced", "visual"):
            raise DeckDNAError("invalid_input", f"unknown style: {style}", http_status=422)
    elif action == "shorter":
        new_plan, extra = apply_action(plan, slide_id, "shorten", pack)
        new_pack = extra or pack
    elif action == "other_chart":
        card = plan.slides[index].chart or {}
        kind = chart_type or next(
            (a["type"] for a in card.get("alternatives") or []
             if a.get("type") != card.get("type")),
            None,
        )
        if kind is None:
            raise DeckDNAError("invalid_input", "this slide has no alternative chart",
                               http_status=422)
        new_plan = change_chart(plan, slide_id, kind, pack)
    elif action in {"replace_title", "replace_text"}:
        if not text or not text.strip():
            raise DeckDNAError("invalid_input", "replacement text is empty", http_status=422)
        slide = new_plan.slides[index]
        if action == "replace_title":
            units = [u.model_copy(update={"text": text, "origin": Origin.user, "locked": True})
                     if u.kind == Kind.title else u for u in slide.content_units]
            if not any(u.kind == Kind.title for u in units):
                units.insert(0, ContentUnit(role="title", kind=Kind.title, text=text,
                                           origin=Origin.user, locked=True))
            changed = slide.model_copy(update={"title_intent": text, "content_units": units})
        else:
            # This is an explicit authored replacement, not an inferred rewrite.
            units = [u for u in slide.content_units if u.kind not in {
                Kind.paragraph, Kind.bullet, Kind.number, Kind.quote, Kind.label,
            }]
            units.append(ContentUnit(role="author_text", kind=Kind.paragraph,
                                     text=text, origin=Origin.user, locked=True))
            changed = slide.model_copy(update={"content_units": units})
        new_plan = new_plan.model_copy(update={"slides": [
            changed if s.id == slide_id else s for s in new_plan.slides
        ]})
    elif action in {"rebuild", "relayout"}:
        pass  # план слайда уже изменён вызывающим (ADR-058)
    elif action not in {"other_layout", "larger_chart"}:
        raise DeckDNAError("invalid_input", f"unknown action: {action}", http_status=422)
    new_plan = new_plan.model_copy(update={"brief": plan.brief})
    validate_locked_transition(plan, new_plan, replacement_slide=slide_id,
                               replaced_kinds=replacement_kinds(action))

    choices = plan_exemplar_choices(
        template_path, new_plan, tables=new_pack.tables, charts=new_pack.charts,
        diagrams=new_pack.diagrams, assets=new_pack.assets,
        strategy=Strategy(style) if style else strat, design_dna=design_dna,
    )
    choice = choices[index]
    if action == "other_layout" or (style and choice.slide_part == current_exemplar):
        part = _alternative_part(template_path, new_plan, index, current_exemplar, new_pack,
                                 strat, design_dna)
        from deckdna.pptx.composing.minimal import apply_exemplar_overrides
        from deckdna.pptx.opc.package import OpcPackage

        choice = apply_exemplar_overrides(
            OpcPackage.open(template_path), new_plan, choices, {slide_id: part}
        )[index]
    elif current_exemplar and action in ("shorter", "other_chart", "replace_title",
                                         "replace_text", "larger_chart", "rebuild"):
        # раскладка та же — меняется только содержание (для обложки не удерживаем
        # случайно попавший контентный образец, если штатный выбор даёт титул)
        from deckdna.pptx.composing.minimal import apply_exemplar_overrides
        from deckdna.pptx.opc.package import OpcPackage

        keep_current = True
        is_title = new_plan.slides[index].purpose.value == "title"
        if is_title and current_exemplar != choice.slide_part:
            from deckdna.pptx.composing.minimal import _local_design_dna

            pkg_check = OpcPackage.open(template_path)
            dna_check = design_dna or _local_design_dna(pkg_check)
            title_parts = {
                ex.part for ex in dna_check.exemplars
                if ex.role == "title" and not ex.content_is_template_meta
            }
            if choice.slide_part in title_parts and current_exemplar not in title_parts:
                keep_current = False
        if keep_current:
            choice = apply_exemplar_overrides(
                OpcPackage.open(template_path), new_plan, choices, {slide_id: current_exemplar}
            )[index]
    one = new_plan.slides[index].model_copy(update={"index": 0})
    mini_plan = new_plan.model_copy(update={"slides": [one]})
    with tempfile.TemporaryDirectory(prefix="deckdna-regen-") as tmp:
        mini = Path(tmp) / "slide.pptx"
        generate_deck(
            template_path, mini_plan, mini, tables=new_pack.tables, charts=new_pack.charts,
            diagrams=new_pack.diagrams, assets=new_pack.assets,
            strategy=Strategy(style) if style else strat,
            exemplar_choices=[replace(choice)], design_dna=design_dna,
            generated_images=generated_images,
        )
        if action == "larger_chart":
            from deckdna.generation.local_edit import enlarge_chart

            enlarge_chart(mini)
        mini_bytes = mini.read_bytes()
        try:
            pngs = pdf_page_pngs(render_pdf(mini, Path(tmp) / "slide.pdf"))
        except Exception:  # noqa: BLE001 — превью дособерётся из PDF колоды
            pngs = []
    new_deck = swap_slide(deck, mini_bytes, position)
    from deckdna.generation.local_edit import update_existing_notes
    from deckdna.planning.talk_track import extractive_track

    new_deck = update_existing_notes(new_deck, position, extractive_track(one))
    from deckdna.planning.request_constraints import validate_deck_count

    validate_deck_count(plan.brief, new_deck)
    return RegenerateResult(
        deck=new_deck, plan=new_plan, pack=new_pack,
        preview_png=pngs[0] if pngs else None, exemplar=choice.slide_part, action=action,
    )


__all__ = ["ACTIONS", "RegenerateResult", "regenerate_slide"]
