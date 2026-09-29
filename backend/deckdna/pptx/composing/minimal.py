"""End-to-end POC: template + DeckPlan → multi-slide out.pptx.

The narrowest honest slice of the Constraint Compiler (see
docs/ARCHITECTURE.md, compile stage): for every SlidePlan pick an exemplar slide, clone
it with its whole relationship graph into a new package, substitute run
texts from that slide's content units, save and report per slide.

Input is a DeckPlan dict (contracts/deck_plan.py / schemas/deck-plan.
schema.json) — no model calls; content is whatever the plan carries.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace as dataclass_replace
from pathlib import Path
from typing import Any

from lxml import etree
from pydantic import ValidationError

from deckdna.contracts.content_pack import Asset, Chart, Diagram, Table
from deckdna.contracts.deck_plan import DeckPlan, Kind, SlidePlan
from deckdna.contracts.design_dna import DesignDNA
from deckdna.contracts.variant_spec import Strategy
from deckdna.errors import DeckDNAError
from deckdna.pptx.cloning.exemplar import (
    ExemplarChoice,
    select_exemplar_slides,
)
from deckdna.pptx.cloning.single_slide import build_multi_slide_deck
from deckdna.pptx.composing.card_reflow import (
    card_slots,
    clone_cards,
    pair_texts_for_cards,
    reflow_cards,
)
from deckdna.pptx.composing.chart_fill import (
    fill_slide_charts,
    unshare_chart_parts,
)
from deckdna.pptx.composing.diagram_fill import fill_slide_diagrams
from deckdna.pptx.composing.image_fill import (
    _match_asset,
    fill_slide_images,
    resolve_slide_images,
    unshare_image_parts,
)
from deckdna.pptx.composing.orphans import remove_orphans
from deckdna.pptx.composing.sources_layout import (
    add_missing_title,
    compose_dense_prose,
    compose_sources,
    compose_visual_cards,
    is_sources_slide,
)
from deckdna.pptx.composing.table_fill import fill_native_tables, remove_empty_table_frames
from deckdna.pptx.composing.text_budget import LAYOUT_ART_THRESHOLD, layout_art_ratio
from deckdna.pptx.composing.text_grow import grow_text
from deckdna.pptx.composing.text_replace import count_content_slots, replace_text_runs
from deckdna.pptx.opc.package import OpcPackage
from deckdna.template.meta import meta_slides

# Kinds whose payload is an asset reference, not substitutable prose.
# They are counted into dropped_units/warnings (visible loss), not
# silently skipped. Kind.table is handled separately: a unit whose
# table_ref resolves to a real ContentPack Table pours into an existing
# a:tbl of the exemplar (table_fill.py); unresolved refs and units
# beyond the slide's table grids still land in dropped_units.
# Kind.chart likewise resolves chart_ref against ContentPack.charts and
# rewrites the c:numCache/c:strCache of the slide's chart parts
# (chart_fill.py). Kind.image resolves asset_ref against ContentPack
# .assets and swaps media-part bytes behind a:blip embeds
# (image_fill.py). Kind.diagram resolves diagram_ref against
# ContentPack.diagrams into a SmartArt-like group (diagram_fill.py).
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_THANKS_RE = re.compile(r"спасибо|благодар|thank", re.I)
_NON_TEXT_KINDS = {
    Kind.icon,
}

# visual-стратегия держит слайд под визуальный контент: не больше
# этого числа body-текстов проливается в один слайд. Обрезанное не
# исчезает молча — учитывается в dropped_units["text"] и в warnings.
_VISUAL_MAX_BODY_TEXTS = 4
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _remove_unmatched_artwork(slide_xml: bytes, slide_area: int) -> tuple[bytes, int]:
    """Remove large donor pictures when the slide has no content image.

    Small logos and thin brand decoration stay. The template's unrelated
    photos or 3-D objects cannot become evidence for the user's topic.
    """
    root = etree.fromstring(slide_xml)
    removed = 0
    for pic in root.iter(f"{_P}pic"):
        ext = pic.find(f"{_P}spPr/{_A}xfrm/{_A}ext")
        if ext is None:
            continue
        try:
            area = int(ext.get("cx", "0")) * int(ext.get("cy", "0"))
        except ValueError:
            continue
        if area < slide_area * 0.04 or area > slide_area * 0.85:
            continue
        parent = pic.getparent()
        if parent is not None:
            parent.remove(pic)
            removed += 1
    return (etree.tostring(root, encoding="UTF-8", xml_declaration=True), removed)


def _slide_texts(
    slide: SlidePlan,
) -> tuple[
    str | None,
    list[str],
    dict[str, int],
    list[str | None],
    list[str | None],
    list[str | None],
]:
    """(title, body texts, dropped units by kind, table refs, chart refs,
    diagram refs, image refs) for the slide.

    A `title`-kind unit wins the title slot over `title_intent`; body
    texts are the remaining text-bearing units in plan order. Units of
    non-text kinds (chart/image/...) are counted, not silently
    skipped — the compose report surfaces how much structured content
    the plan asked for that this stage cannot yet compose. Kind.table
    units yield their ``table_ref`` (None when absent) for the caller
    to resolve against ContentPack.tables.
    """
    title_text: str | None = slide.title_intent
    texts: list[str] = []
    dropped: dict[str, int] = {}
    table_refs: list[str | None] = []
    chart_refs: list[str | None] = []
    diagram_refs: list[str | None] = []
    image_refs: list[str | None] = []
    for unit in slide.content_units:
        if unit.kind == Kind.table:
            table_refs.append(unit.table_ref)
            continue
        if unit.kind == Kind.chart:
            chart_refs.append(unit.chart_ref)
            continue
        if unit.kind == Kind.diagram:
            diagram_refs.append(unit.diagram_ref)
            continue
        if unit.kind == Kind.image:
            image_refs.append(unit.asset_ref)
            continue
        if unit.kind in _NON_TEXT_KINDS:
            key = unit.kind.value
            dropped[key] = dropped.get(key, 0) + 1
            continue
        if not unit.text:
            continue
        if unit.kind == Kind.title:
            title_text = unit.text
            continue
        texts.append(unit.text)
    # заголовок слайда пишется без точки в конце (многоточие — не трогаем)
    if title_text and title_text.endswith(".") and not title_text.endswith(".."):
        title_text = title_text[:-1].rstrip()
    return (
        title_text, texts, dropped,
        table_refs, chart_refs, diagram_refs, image_refs,
    )


def _exemplar_shape_metrics(presentation, slide_part: str) -> dict[str, dict]:
    """shape_id -> {sz, w, h, wrap_none} the audit would measure on the
    exemplar: effective font size and usable text box, both including
    placeholder inheritance (layout → master) that the slide XML alone
    doesn't carry. Used as the fitting baseline for bodies that declare
    no own sz/xfrm."""
    from types import SimpleNamespace

    from pptx.enum.shapes import MSO_SHAPE_TYPE

    from deckdna.audit.basic import DEFAULT_FONT_PT, _effective_font_size_pt

    ctx = SimpleNamespace(default_font_pt=DEFAULT_FONT_PT)
    wanted = f"/{slide_part}"
    for slide in presentation.slides:
        if str(slide.part.partname) != wanted:
            continue

        def walk(shapes):
            for shape in shapes:
                yield shape
                if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                    yield from walk(shape.shapes)

        hints: dict[str, dict] = {}
        for shape in walk(slide.shapes):
            if not shape.has_text_frame:
                continue
            tf = shape.text_frame
            para_sizes = [
                _effective_font_size_pt(shape, para, slide, ctx)
                for para in tf.paragraphs
            ]
            hint: dict = {
                "sz": max(para_sizes) if para_sizes else DEFAULT_FONT_PT,
                "wrap_none": tf.word_wrap is False,
            }
            if shape.width and shape.height:
                # OOXML default insets when the frame omits them
                ml = int(tf.margin_left or 91440)
                mr = int(tf.margin_right or 91440)
                mt = int(tf.margin_top or 45720)
                mb = int(tf.margin_bottom or 45720)
                hint["w"] = int(shape.width) - ml - mr
                hint["h"] = int(shape.height) - mt - mb
            hints[str(shape.shape_id)] = hint
        return hints
    return {}


def slide_needs(
    plan: DeckPlan,
    tables_by_id: dict[str, Table],
    charts_by_id: dict[str, Chart],
    assets_list: list[Asset],
    generated_refs: frozenset[str] = frozenset(),
) -> list[frozenset[str]]:
    """Per-slide capability requirements из юнитов плана (OR-004).

    ``chart``/``table``/``image`` требуются только когда реф юнита
    резолвится в данные ContentPack'а — иначе просить капабилити у
    exemplar'а бессмысленно (юнит честно дропнется downstream).
    *generated_refs* (ADR-017) — asset_ref'ы model-сгенерированных
    картинок (нет в ContentPack, поэтому не резолвятся через
    ``_match_asset`` — тот же принцип, что и в ``resolve_slide_images``'s
    ``generated`` параметре).
    """
    needs: list[frozenset[str]] = []
    for slide in plan.slides:
        need: set[str] = set()
        for unit in slide.content_units:
            if unit.kind == Kind.chart and unit.chart_ref in charts_by_id:
                need.add("chart")
            if unit.kind == Kind.table and unit.table_ref in tables_by_id:
                need.add("table")
            if unit.kind == Kind.image and (
                unit.asset_ref in generated_refs
                or (
                    _match_asset(unit.asset_ref, assets_list)
                    if unit.asset_ref
                    else assets_list
                )
            ):
                need.add("image")
        needs.append(frozenset(need))
    return needs


def generate_deck(
    template_path: str | Path,
    deck_plan: DeckPlan | dict[str, Any],
    out_path: str | Path,
    protected_slide_indices: Iterable[int] = (),
    tables: Iterable[Table] = (),
    charts: Iterable[Chart] = (),
    diagrams: Iterable[Diagram] = (),
    assets: Iterable[Asset] = (),
    content_path: str | Path | None = None,
    strategy: Strategy = Strategy.balanced,
    exemplar_choices: Iterable[ExemplarChoice] | None = None,
    generated_images: dict[str, bytes] | None = None,
    design_dna: DesignDNA | None = None,
) -> dict[str, Any]:
    """Build a multi-slide deck from *template_path* and a DeckPlan.

    *protected_slide_indices* — 0-based позиции слайдов шаблона
    (например обязательные submission-слайды 7–11, OR-031), которые
    клонируются в выходную колоду ВЕРБАТИМ на своих позициях: без
    текстовой подмены и любых мутаций. Контентные слайды плана
    раздвигаются вокруг них; длина выходной колоды =
    ``len(plan.slides) + len(protected)``.

    *assets* + *content_path* — метаданные ContentPack.assets и исходный
    content-файл пользователя: Kind.image-юниты резолвят asset_ref в
    Asset, чьи байты перечитываются из content_path по artifact_id
    (image_fill.py). Без content_path юниты честно дропаются.

    *generated_images* (ADR-017) — ``asset_ref -> bytes`` для
    model-сгенерированных картинок, уже готовых в памяти (не в
    ContentPack, поэтому не резолвятся через *assets*/*content_path*) —
    caller (``generation/pipeline.py``) отвечает за то, чтобы юниты с
    этими asset_ref уже были в переданном ``deck_plan``.

    Returns a JSON-serialisable composition report: per-slide exemplar
    and text-replacement stats plus totals.
    """
    template_path = Path(template_path)
    out_path = Path(out_path)
    if not isinstance(deck_plan, DeckPlan):
        try:
            plan = DeckPlan.model_validate(deck_plan)
        except ValidationError as exc:
            raise DeckDNAError(
                code="invalid_input",
                message=f"deck plan failed schema validation: {exc.error_count()} errors",
                stage="composing.deck",
            ) from exc
    else:
        plan = deck_plan

    pkg = OpcPackage.open(template_path)
    dangling = pkg.check_rel_integrity()
    if dangling:
        shown = "; ".join(dangling[:10])
        if len(dangling) > 10:
            shown += f"; …и ещё {len(dangling) - 10}"
        raise DeckDNAError(
            code="package_corrupt",
            message=f"template has {len(dangling)} dangling relationship(s): {shown}",
            stage="composing.deck",
        )
    tables_by_id = {t.id: t for t in tables}
    charts_by_id = {c.id: c for c in charts}
    diagrams_by_id = {d.id: d for d in diagrams}
    assets_list = list(assets)

    # Requirement-aware exemplar choice (OR-004): a slide plan asking
    # for a resolvable chart/table gets an exemplar physically carrying
    # one — from-scratch creation (add_table/add_chart) покрывает
    # chartless/tableless случаи post-clone, но носитель exemplar всё
    # равно предпочтительнее (его стиль/оси сохранены). Same for images:
    # a Kind.image unit prefers a slide with a blip embed to receive
    # bytes into; add_picture fallback covers the no-blip case
    # post-clone.
    generated_images = generated_images or {}
    needs = slide_needs(
        plan, tables_by_id, charts_by_id, assets_list, frozenset(generated_images)
    )
    if exemplar_choices is None:
        # Capacity-aware reassignment (needs-free picks only): a sparse
        # slide landing on a dense card-grid exemplar left most cards
        # empty, a dense slide landing on a sparse one overflowed/
        # collided with neighbors — both reported live on generated
        # decks. select_exemplar_slides() only reorders which already-
        # selected exemplar goes to which slide position; it never
        # changes the set of exemplars picked.
        visible_texts = [_slide_texts(sp)[1] for sp in plan.slides]
        unit_counts = [len(items) for items in visible_texts]
        text_lengths = [max((len(t) for t in items), default=0) for items in visible_texts]
        entity_specific_parts = [
            e.part for e in (design_dna.exemplars if design_dna else [])
            if e.content_description
            and (e.content_is_entity_specific or e.content_is_template_meta)
        ] + sorted(meta_slides(pkg, [p for p in pkg.parts if p.startswith("ppt/slides/slide")]))
        choices = select_exemplar_slides(
            pkg,
            count=len(plan.slides),
            needs=needs,
            strategy=strategy,
            unit_counts=unit_counts,
            text_lengths=text_lengths,
            purposes=[sp.purpose.value for sp in plan.slides],
            entity_specific_parts=entity_specific_parts,
        )
    else:
        # Предвыбранные exemplar'ы (LLM top-k rerank в pipeline): честный
        # contract — длина строго равна числу слайдов плана.
        choices = list(exemplar_choices)
        if len(choices) != len(plan.slides):
            raise DeckDNAError(
                code="invalid_input",
                message=(
                    f"exemplar_choices has {len(choices)} picks for "
                    f"{len(plan.slides)} plan slides"
                ),
                stage="composing.deck",
            )

    # python-pptx view of the template: per-shape effective font size
    # and usable box incl. placeholder inheritance — fitting baselines.
    from pptx import Presentation

    prs = Presentation(str(template_path))
    hint_cache: dict[str, dict[str, dict]] = {}

    # Порядок слайдов шаблона — по presentation.xml (slide_parts()
    # сортирует лексикографически: slide1, slide10, slide2 — не годится
    # для позиционной защиты).
    template_parts = [str(s.part.partname).lstrip("/") for s in prs.slides]
    template_slides = {str(s.part.partname).lstrip("/"): s for s in prs.slides}
    art_cache: dict[str, float] = {}

    def rigid_art(part: str) -> bool:
        """Подложки карточек «запечены» в картинку макета — сетку не трогаем."""
        slide_obj = template_slides.get(part)
        if slide_obj is None:
            return False
        ratio = layout_art_ratio(slide_obj, int(prs.slide_width), int(prs.slide_height), art_cache)
        return ratio >= LAYOUT_ART_THRESHOLD
    protected = sorted(set(int(i) for i in (protected_slide_indices or ())))
    for idx in protected:
        if idx < 0 or idx >= len(template_parts):
            raise DeckDNAError(
                code="invalid_input",
                message=(
                    f"protected slide index {idx} outside template "
                    f"({len(template_parts)} slides)"
                ),
                stage="composing.deck",
            )

    content = Path(content_path) if content_path is not None else None
    used_asset_ids: set[str] = set()
    plan_specs: list[tuple[str, bytes]] = []
    plan_reports: list[dict[str, Any]] = []
    sw, sh = int(prs.slide_width), int(prs.slide_height)
    source_number = 1
    for slide, choice in zip(plan.slides, choices, strict=True):
        (
            title_text,
            texts,
            dropped,
            table_refs,
            chart_refs,
            diagram_refs,
            image_refs,
        ) = _slide_texts(slide)
        if choice.slide_part not in hint_cache:
            hint_cache[choice.slide_part] = _exemplar_shape_metrics(
                prs, choice.slide_part
            )
        sources_slide = is_sources_slide(slide.title_intent)
        hero_card = (
            strategy == Strategy.visual and not sources_slide
            and slide.purpose.value not in ("title", "thank_you", "section_divider")
            and len(texts) == 1
            and not (table_refs or chart_refs or diagram_refs or image_refs)
        )
        dense_prose = (
            not sources_slide and not hero_card
            and slide.purpose.value not in ("title", "thank_you", "section_divider")
            and (len(texts) > 6 or max((len(t) for t in texts), default=0) > 250)
            and not (table_refs or chart_refs or diagram_refs or image_refs)
        )
        visual_cards = (
            (strategy == Strategy.visual or hero_card)
            and not sources_slide and not dense_prose
            and slide.purpose.value not in ("title", "thank_you", "section_divider")
            and 1 <= len(texts) <= _VISUAL_MAX_BODY_TEXTS
            and not (table_refs or chart_refs or diagram_refs or image_refs)
        )
        # Сетка карточек эталона — «глина»: пунктов больше, чем карточек, —
        # карточки клонируются в стиле шаблона; меньше — лишние потом
        # удаляются (reflow_cards). Пара слотов «заголовок + текст» — один
        # пункт на карточку, а не поток текстов по слотам подряд. Только
        # когда не сработала ни одна нативная раскладка — compose_*
        # перерисовывают слайд сами и paired-тексты им не нужны.
        source_xml = pkg.parts[choice.slide_part]
        # финал шаблона уже благодарит («Спасибо!» дисплейным кеглем) —
        # его авторский текст лучше нашего «Спасибо за внимание», который
        # в ту же рамку не помещается
        if slide.purpose.value in ("thank_you", "qa") and _THANKS_RE.search(
            " ".join(t.text or "" for t in etree.fromstring(source_xml).iter(f"{{{_A}}}t"))
        ):
            title_text = None
        cards_cloned = 0
        fill_texts = texts
        baked_grid = rigid_art(choice.slide_part)
        if not (sources_slide or dense_prose or visual_cards):
            n_cards, per_card = card_slots(source_xml, sw, sh)
            if n_cards and per_card in (1, 2) and count_content_slots(
                source_xml, (sw, sh)
            ) == n_cards * per_card:
                # на «запечённой» сетке клон ляжет мимо картинки, но потерять
                # пункт хуже; путь с моделью такие несовпадения отсекает ещё
                # при выборе эталона (layout_fit._fits_rigid)
                if len(texts) > n_cards and not (chart_refs or image_refs):
                    source_xml, cards_cloned = clone_cards(source_xml, len(texts), sw, sh)
                if per_card == 2:
                    fill_texts = pair_texts_for_cards(texts, per_card)
        slide_xml, text_report = replace_text_runs(
            source_xml,
            [] if sources_slide or dense_prose or visual_cards else fill_texts,
            title_text=None if sources_slide or dense_prose or visual_cards else title_text,
            shape_hints=hint_cache[choice.slide_part],
            slide_size=(sw, sh),
        )
        # Сетку карточек шаблона правят его же блоками (клонирование,
        # reflow, бюджет текста) — решение владельца 28.09: слайды «с нуля»
        # только там, где у эталона сетки нет.
        adaptive_cards = (
            not sources_slide and not dense_prose and not visual_cards
            and not card_slots(pkg.parts[choice.slide_part], sw, sh)[0]
            and slide.purpose.value not in ("title", "thank_you", "section_divider")
            and 1 <= len(texts) <= 6
            and not (table_refs or chart_refs or diagram_refs or image_refs)
            and (
                text_report.bodies_overflowing > 0
                or text_report.texts_dropped > 0
                or choice.content_slots > max(len(texts) + 2, len(texts)*2)
            )
        )
        if sources_slide:
            # The donor's card boxes may each be far too short for a
            # citation. A dedicated two-column native layout keeps all
            # sources editable and avoids overlap/overflow.
            title = slide.title_intent
            if (plan.language or "").startswith("ru"):
                title = "Источники — продолжение" if source_number > 1 else "Источники"
            slide_xml = compose_sources(
                slide_xml, title, texts,
                int(prs.slide_width), int(prs.slide_height), source_number,
            )
            source_number += len(texts)
        if dense_prose:
            display_entries = list(texts)
            flattened_tables = 0
            for ref in table_refs:
                table = tables_by_id.get(ref) if ref else None
                if table is None:
                    continue
                flattened_tables += 1
                for row in table.rows:
                    display_entries.append(
                        "; ".join(
                            f"{header}: {value}"
                            for header, value in zip(table.headers, row, strict=False)
                            if value is not None
                        )
                    )
            slide_xml, shortened = compose_dense_prose(
                slide_xml, title_text or slide.title_intent, display_entries,
                int(prs.slide_width), int(prs.slide_height),
            )
            if shortened:
                dropped["text_truncated"] = shortened
        else:
            shortened = 0
            flattened_tables = 0
        if visual_cards or adaptive_cards:
            slide_xml, visual_shortened = compose_visual_cards(
                slide_xml, title_text or slide.title_intent, texts,
                int(prs.slide_width), int(prs.slide_height),
                cards=strategy != Strategy.faithful,
            )
            if adaptive_cards:
                # The donor text boxes have been replaced by native cards;
                # their preliminary clipping/unplaced counts are no longer
                # part of the delivered slide.
                text_report = dataclass_replace(
                    text_report, bodies_truncated=0, texts_dropped=0, bodies_overflowing=0,
                    bodies_eligible=len(texts), bodies_cleared=0,
                    bodies_filled=len(texts), title_placed=True,
                )
            if visual_shortened:
                dropped["text_truncated"] = (
                    dropped.get("text_truncated", 0) + visual_shortened
                )
        if sources_slide or dense_prose or visual_cards:
            # Report the delivered native layout, not the discarded donor pass.
            text_report = dataclass_replace(
                text_report, bodies_eligible=len(texts), bodies_filled=len(texts),
                bodies_cleared=0, bodies_truncated=0, bodies_overflowing=0,
                texts_supplied=len(texts), texts_dropped=0, title_placed=bool(title_text),
            )
        if (
            not sources_slide and not dense_prose and not visual_cards and not adaptive_cards
            and title_text and not text_report.title_placed
        ):
            slide_xml = add_missing_title(
                slide_xml, title_text, int(prs.slide_width), int(prs.slide_height)
            )
            text_report = dataclass_replace(text_report, title_placed=True)
        if text_report.texts_dropped:
            dropped["text_unplaced"] = (
                dropped.get("text_unplaced", 0) + text_report.texts_dropped
            )
        if text_report.bodies_truncated:
            dropped["text_truncated"] = (
                dropped.get("text_truncated", 0) + text_report.bodies_truncated
            )
        resolved: list[Table] = []
        unresolved = 0
        for ref in ([] if dense_prose else table_refs):
            table = tables_by_id.get(ref) if ref else None
            if table is None:
                unresolved += 1
            else:
                resolved.append(table)
        # shared: table/diagram/pic-creation hosts don't collide
        used_hosts: set[tuple[int, int, int, int]] = set()
        slide_xml, fill_report = fill_native_tables(
            slide_xml,
            resolved,
            used_hosts=used_hosts,
            slide_size=(int(prs.slide_width), int(prs.slide_height)),
        )
        slide_xml, empty_tables_removed = remove_empty_table_frames(slide_xml)
        dropped_tables = unresolved + fill_report.units_dropped
        if dropped_tables:
            dropped["table"] = dropped.get("table", 0) + dropped_tables
        resolved_diagrams: list[Diagram] = []
        for ref in diagram_refs:
            diagram = diagrams_by_id.get(ref) if ref else None
            if diagram is None:
                dropped["diagram"] = dropped.get("diagram", 0) + 1
            else:
                resolved_diagrams.append(diagram)
        slide_xml, diagram_report = fill_slide_diagrams(
            slide_xml, resolved_diagrams, used_hosts=used_hosts
        )
        if diagram_report.units_dropped:
            dropped["diagram"] = (
                dropped.get("diagram", 0) + diagram_report.units_dropped
            )
        slide_charts: list[Chart] = []
        for ref in chart_refs:
            chart = charts_by_id.get(ref) if ref else None
            if chart is None:
                dropped["chart"] = dropped.get("chart", 0) + 1
            else:
                slide_charts.append(chart)
        slide_images, images_dropped = resolve_slide_images(
            image_refs, assets_list, content, used_asset_ids, generated=generated_images
        )
        if images_dropped:
            dropped["image"] = dropped.get("image", 0) + images_dropped
        if not slide_images and slide.purpose.value not in ("title", "thank_you"):
            slide_xml, removed_artwork = _remove_unmatched_artwork(
                slide_xml, int(prs.slide_width) * int(prs.slide_height)
            )
        else:
            removed_artwork = 0
        # Незаполненные карточки сетки эталона удаляются, оставшиеся
        # растягиваются на освободившееся место (как del_* у PPTAgent).
        # Диаграммы уже созданы в освободившихся телах, а графики и
        # картинки вставляются позже в свободные рамки — на таких слайдах
        # сетку не трогаем, чтобы не отнять им место и не удалить созданную
        # grpSp как незаполненную карточку.
        reflow_report = None
        if (not slide_charts and not slide_images and not baked_grid
                and not resolved_diagrams):
            slide_xml, reflow_report = reflow_cards(
                slide_xml,
                int(prs.slide_width),
                int(prs.slide_height),
                filled_shape_ids=set(text_report.filled_shape_ids),
            )
        # оформление очищенных слотов (подложка аватара у пустых «Имя /
        # Должность», пустая плашка) не остаётся на слайде сиротой
        orphans_removed = 0
        if not (sources_slide or dense_prose or visual_cards or adaptive_cards
                or slide_images):
            slide_xml, orphans_removed = remove_orphans(
                source_xml, slide_xml, int(prs.slide_width), int(prs.slide_height)
            )
        # мелкий демо-кегль шаблона → максимальный, который рамка вмещает
        slide_xml, text_grown = grow_text(slide_xml, hint_cache[choice.slide_part])
        plan_specs.append(
            (choice.slide_part, slide_xml, slide_charts, slide_images, used_hosts)
        )
        plan_reports.append(
            {
                "index": slide.index,
                "id": slide.id,
                "purpose": slide.purpose.value,
                "exemplar": choice.to_dict(),
                "text": text_report.to_dict(),
                "dropped_units": dropped,
                "tables": fill_report.to_dict(),
                "empty_tables_removed": empty_tables_removed,
                "diagrams": diagram_report.to_dict(),
                "unmatched_artwork_removed": removed_artwork,
                "dense_prose_layout": dense_prose,
                "visual_cards_layout": visual_cards,
                "adaptive_layout": adaptive_cards,
                "tables_flattened": flattened_tables,
                "card_reflow": reflow_report.to_dict() if reflow_report else None,
                "text_grown": text_grown,
                "cards_cloned": cards_cloned,
                "orphans_removed": orphans_removed,
            }
        )

    # Защищённые слайды шаблона клонируются вербатим (исходный XML
    # без подмены) на своих позициях; контентные слайды сдвигаются.
    total = len(plan_specs) + len(protected)
    overflow_idx = [i for i in protected if i >= total]
    if overflow_idx:
        raise DeckDNAError(
            code="invalid_input",
            message=(
                f"protected slide indices {overflow_idx} beyond output "
                f"deck length {total} — increase target_slide_count"
            ),
            stage="composing.deck",
        )
    protected_map = {idx: template_parts[idx] for idx in protected}
    clone_specs: list[tuple] = []
    slide_reports: list[dict[str, Any]] = []
    plan_it = iter(zip(plan_specs, plan_reports, strict=True))
    for pos in range(total):
        if pos in protected_map:
            part = protected_map[pos]
            clone_specs.append((part, pkg.parts[part], [], []))
            slide_reports.append(
                {
                    "index": pos,
                    "id": f"protected:{part}",
                    "purpose": "protected",
                    "protected": True,
                    "verbatim": True,
                    "exemplar": {"slide_part": part},
                    "text": {},
                    "dropped_units": {},
                    "tables": {},
                    "diagrams": {},
                }
            )
        else:
            spec, report = next(plan_it)
            clone_specs.append(spec)
            report["_charts"] = spec[2]
            report["_images"] = spec[3]
            report["_used_hosts"] = spec[4]
            slide_reports.append(report)

    out_pkg = build_multi_slide_deck(
        pkg, [(src, xml) for src, xml, *_ in clone_specs]
    )

    # Chart/image fills: rewrite chart caches and media-part bytes each
    # output slide references (already cloned into out_pkg).
    used_chart_parts: set[str] = set()
    used_image_parts: set[str] = set()
    for i, report in enumerate(slide_reports, start=1):
        out_part = f"ppt/slides/slide{i}.xml"
        # Один used_hosts-сет на все create-fallback'и (table уже
        # получил его на этапе plan_specs, chart/image — здесь): новый
        # graphicFrame/pic не пересечётся с созданной таблицей/диаграммой.
        used_hosts = report.pop("_used_hosts", None)
        slide_charts = report.pop("_charts", [])
        if slide_charts:
            # Два выходных слайда от одного chart-exemplar делят один
            # физический chart-парт — отдаём этому слайду свою копию
            # (pristine байты из исходного pkg), иначе второй юнит
            # честно дропался бы в fill_slide_charts.
            unshare_chart_parts(
                out_pkg.parts,
                pkg.parts,
                out_part,
                used_chart_parts,
                tag=f"dup{i}",
            )
        chart_report = fill_slide_charts(
            out_pkg.parts,
            out_part,
            slide_charts,
            out_pkg.rels(out_part),
            used_chart_parts,
            used_hosts=used_hosts,
        )
        report["charts"] = chart_report.to_dict()
        if chart_report.units_dropped:
            dropped = report.setdefault("dropped_units", {})
            dropped["chart"] = (
                dropped.get("chart", 0) + chart_report.units_dropped
            )
        slide_images = report.pop("_images", [])
        if slide_images:
            # Два выходных слайда от одного image-exemplar делят один
            # физический media-парт — отдаём этому слайду свою копию
            # (pristine байты из исходного pkg), иначе второй юнит
            # честно дропался бы в fill_slide_images.
            unshare_image_parts(
                out_pkg.parts,
                pkg.parts,
                out_part,
                used_image_parts,
                tag=f"dup{i}",
            )
        image_report = fill_slide_images(
            out_pkg.parts,
            out_part,
            slide_images,
            out_pkg.rels(out_part),
            used_image_parts,
            used_hosts=used_hosts,
        )
        report["images"] = image_report.to_dict()
        if image_report.units_dropped:
            dropped = report.setdefault("dropped_units", {})
            dropped["image"] = (
                dropped.get("image", 0) + image_report.units_dropped
            )

    out_pkg.save(out_path)

    totals: dict[str, int] = {}
    dropped_totals: dict[str, int] = {}
    for report in slide_reports:
        for key, value in report["text"].items():
            if type(value) is int:
                totals[key] = totals.get(key, 0) + value
        for kind, count in report["dropped_units"].items():
            dropped_totals[kind] = dropped_totals.get(kind, 0) + count

    warnings = [
        (
            f"{count} text unit(s) trimmed — visual strategy lowers "
            "text density to leave room for visual content"
            if kind == "text"
            else f"{count} text unit(s) dropped — slides had fewer "
            "eligible text slots than planned text units"
            if kind == "text_unplaced"
            else f"{count} text unit(s) shortened to fit editable boxes"
            if kind == "text_truncated"
            else f"{count} {kind} unit(s) dropped — {kind} content not yet composable"
        )
        for kind, count in sorted(dropped_totals.items())
        if count
    ]
    cols_dropped = sum(r["tables"]["cols_dropped"] for r in plan_reports)
    if cols_dropped:
        warnings.append(
            f"table data truncated to fit exemplar grid: "
            f"{cols_dropped} cell value(s) dropped"
        )

    return {
        "template": str(template_path),
        "output": str(out_path),
        "deck_plan_id": plan.id,
        "slides": slide_reports,
        "protected_indices": protected,
        "totals": totals,
        "dropped_units": dropped_totals,
        "warnings": warnings,
        "parts_emitted": len(out_pkg.parts),
        "slides_out": len(slide_reports),
        "strategy": strategy.value,
    }
