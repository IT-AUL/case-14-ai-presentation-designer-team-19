"""End-to-end POC: template + DeckPlan → multi-slide out.pptx.

The narrowest honest slice of the Constraint Compiler (SOLUTION_RU §4 stage
8, PPTX_COMPILER.md §3): for every SlidePlan pick an exemplar slide, clone
it with its whole relationship graph into a new package, substitute run
texts from that slide's content units, save and report per slide.

Input is a DeckPlan dict (contracts/deck_plan.py / schemas/deck-plan.
schema.json) — no model calls; content is whatever the plan carries.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import replace as dataclass_replace
from pathlib import Path
from typing import Any

from lxml import etree
from pydantic import ValidationError

from deckdna.analysis.shape import shape_for_type
from deckdna.contracts.content_pack import Asset, Chart, Diagram, Table
from deckdna.contracts.deck_plan import DeckPlan, Kind, SlidePlan
from deckdna.contracts.design_dna import DesignDNA
from deckdna.contracts.variant_spec import Strategy
from deckdna.errors import DeckDNAError
from deckdna.pptx.cloning.exemplar import (
    ExemplarChoice,
    select_exemplar_slides,
    shape_count,
    slide_layout_map,
)
from deckdna.pptx.cloning.single_slide import build_multi_slide_deck
from deckdna.pptx.composing.background import (
    align_composed_text_ink,
    restore_background_layers,
)
from deckdna.pptx.composing.card_reflow import (
    _box,
    card_slots,
    clone_cards,
    pair_texts_for_cards,
    reflow_cards,
)
from deckdna.pptx.composing.chart_fill import (
    chart_hosts,
    fill_slide_charts,
    inherited_chart_obstacles,
    readable_chart_host,
    unshare_chart_parts,
)
from deckdna.pptx.composing.chart_style import donor_style
from deckdna.pptx.composing.diagram_fill import (
    DiagramFillReport,
    fill_slide_diagrams,
    layout_busy_boxes,
)
from deckdna.pptx.composing.font_scale import (
    balance_slide_occupancy,
    fit_oversized_body_before_data,
    package_observed_font_scale_from_parts,
    slide_font_scale,
    snap_deck_font_scale,
)
from deckdna.pptx.composing.image_fill import (
    _blip_part_order,
    _empty_picture_placeholders,
    _match_asset,
    fill_slide_images,
    resolve_slide_images,
    unshare_image_parts,
)
from deckdna.pptx.composing.native_compose import compose as native_compose
from deckdna.pptx.composing.native_plan import best_frame, build_frame, decide_native
from deckdna.pptx.composing.orphans import remove_orphans
from deckdna.pptx.composing.placeholders import clear_layout_prompts
from deckdna.pptx.composing.sources_layout import (
    add_missing_title,
    add_slide_footnote,
    compose_cover_slide,
    compose_data_split,
    compose_dense_prose,
    compose_visual_cards,
    is_sources_slide,
    paginate_sources,
)
from deckdna.pptx.composing.table_fill import (
    TableFillReport,
    _empty_body_hosts,
    fill_native_tables,
    remove_empty_table_frames,
)
from deckdna.pptx.composing.text_budget import (
    LAYOUT_ART_THRESHOLD,
    layout_art_ratio,
    slide_background_art_ratio,
)
from deckdna.pptx.composing.text_grow import grow_text
from deckdna.pptx.composing.text_replace import count_content_slots, replace_text_runs
from deckdna.pptx.composing.title_fit import fit_title_above_content
from deckdna.pptx.opc.package import OpcPackage
from deckdna.template.meta import meta_slides

logger = logging.getLogger(__name__)

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
_THANKS_RE = re.compile(r"спасибо|благодар|thank", re.I)
_THANKS_RU_RE = re.compile(r"спасибо|благодар", re.I)
_THANKS_EN_RE = re.compile(r"\bthank(?:s|\s+you)?\b", re.I)
_CONTACT_IN_BRIEF_RE = re.compile(
    r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}|"
    r"(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}|"
    r"https?://\S+|"
    r"(?<!\w)@[a-zA-Z0-9_]{3,}",
    re.I,
)
_NON_TEXT_KINDS = {
    Kind.icon,
}

# visual-стратегия держит слайд под визуальный контент: не больше
# этого числа body-текстов проливается в один слайд. Обрезанное не
# исчезает молча — учитывается в dropped_units["text"] и в warnings.
_VISUAL_MAX_BODY_TEXTS = 4
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


_CHART_URI = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"


def _donor_has_same_lang_thanks(
    source_xml: bytes, title_text: str | None, language: str | None
) -> bool:
    if not _THANKS_RE.search(title_text or ""):
        return False
    donor_text = " ".join(t.text or "" for t in etree.fromstring(source_xml).iter(f"{_A}t"))
    lang = (language or "ru").lower()
    if lang.startswith("ru"):
        return bool(_THANKS_RU_RE.search(donor_text)) and not bool(_THANKS_EN_RE.search(donor_text))
    if lang.startswith("en"):
        return bool(_THANKS_EN_RE.search(donor_text)) and not bool(_THANKS_RU_RE.search(donor_text))
    return False


def _slide_footnote_text(slide: SlidePlan) -> str | None:
    notes: list[str] = []
    for unit in slide.content_units:
        if unit.role == "footnote" and (unit.text or "").strip():
            t = unit.text.strip()
            if t not in notes:
                notes.append(t)
    return "  |  ".join(notes) if notes else None


def _remove_chart_frames(slide_xml: bytes) -> tuple[bytes, int]:
    root = etree.fromstring(slide_xml)
    removed = 0
    for frame in list(
        root.iter("{http://schemas.openxmlformats.org/presentationml/2006/main}graphicFrame")
    ):
        data = frame.find(f".//{_A}graphicData")
        if data is not None and data.get("uri") == _CHART_URI and frame.getparent() is not None:
            frame.getparent().remove(frame)
            removed += 1
    if not removed:
        return slide_xml, 0
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True), removed


_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _remove_unmatched_artwork(
    slide_xml: bytes,
    slide_area: int,
    slide_size: tuple[int, int] | None = None,
    *,
    keep_rids: set[str] | None = None,
) -> tuple[bytes, int]:
    """Remove large donor pictures when the slide has no content image
    (or when a donor picture is not among the claimed *keep_rids* hosts).

    Small logos and thin brand decoration stay. The template's unrelated
    photos or 3-D objects cannot become evidence for the user's topic.
    """
    root = etree.fromstring(slide_xml)
    removed = 0
    regions = []
    for pic in list(root.iter(f"{_P}pic")):
        if keep_rids and any(
            blip.get(f"{{{_R_NS}}}embed") in keep_rids for blip in pic.iter(f"{_A}blip")
        ):
            continue
        ext = pic.find(f"{_P}spPr/{_A}xfrm/{_A}ext")
        if ext is None:
            continue
        try:
            area = int(ext.get("cx", "0")) * int(ext.get("cy", "0"))
        except ValueError:
            continue
        from deckdna.pptx.composing.artwork import covers_canvas

        box = _box(pic)
        background = (
            covers_canvas(box.x, box.y, box.w, box.h, *slide_size)
            if box is not None and slide_size is not None
            else area >= slide_area * 0.95
        )
        if area < slide_area * 0.04 or background:
            continue
        parent = pic.getparent()
        if parent is not None:
            box = _box(pic)
            if box is not None:
                regions.append((parent, box))
            parent.remove(pic)
            removed += 1
    # Небольшие картинки поверх удалённой демо-иллюстрации — её часть.
    # Иначе остаётся, например, крестик от старого мокапа среди выводов.
    for pic in list(root.iter(f"{_P}pic")):
        box = _box(pic)
        parent = pic.getparent()
        if (
            box is not None
            and parent is not None
            and any(
                parent is owner
                and area.x <= box.x
                and area.y <= box.y
                and area.r >= box.r
                and area.b >= box.b
                for owner, area in regions
            )
        ):
            parent.remove(pic)
            removed += 1
    return (etree.tostring(root, encoding="UTF-8", xml_declaration=True), removed)


def _slide_texts(
    slide: SlidePlan,
    *,
    language: str = "ru",
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
    # происхождение текста (модель/код) — в speaker notes и audit,
    # не в заголовке слайда: «Предположение:» читалось как отладочная
    # метка, а не вывод
    texts: list[str] = []
    dropped: dict[str, int] = {}
    table_refs: list[str | None] = []
    chart_refs: list[str | None] = []
    diagram_refs: list[str | None] = []
    image_refs: list[str | None] = []
    for unit in slide.content_units:
        if unit.role in ("eyebrow", "footnote"):
            continue  # Cover kicker and slide footnotes have their own semantic slots.
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
        text = unit.text
        if unit.kind == Kind.title:
            title_text = text
            continue
        texts.append(text)
    from deckdna.planning.story_director import clean_purpose_text

    title_text = clean_purpose_text(title_text)
    # заголовок слайда пишется без точки в конце (многоточие — не трогаем)
    title_locked = any(u.kind == Kind.title and u.locked for u in slide.content_units)
    if (
        title_text
        and not title_locked
        and title_text.endswith(".")
        and not title_text.endswith("..")
    ):
        title_text = title_text[:-1].rstrip()
    if slide.purpose.value == "title":
        title_norm = (title_text or "").strip().casefold()
        unique_subtitles: list[str] = []
        for item in texts:
            norm = item.strip().casefold()
            if not norm or norm == title_norm:
                continue
            if all(norm != prev.strip().casefold() for prev in unique_subtitles):
                unique_subtitles.append(item)
        texts = unique_subtitles[:1]
    return (
        title_text,
        texts,
        dropped,
        table_refs,
        chart_refs,
        diagram_refs,
        image_refs,
    )


def _exemplar_shape_metrics(presentation, slide_part: str, *, source_slide=None) -> dict[str, dict]:
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
    for slide in [source_slide] if source_slide is not None else presentation.slides:
        if source_slide is None and str(slide.part.partname) != wanted:
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
                _effective_font_size_pt(shape, para, slide, ctx) for para in tf.paragraphs
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
                # рамка целиком (с наследованием от макета) — для
                # title_fit: где заголовок стоит на слайде
                if shape.left is not None and shape.top is not None:
                    hint["box"] = (
                        int(shape.left),
                        int(shape.top),
                        int(shape.width),
                        int(shape.height),
                    )
                hint["insets"] = (ml, mt)
            hints[str(shape.shape_id)] = hint
        return hints
    return {}


def slide_needs(
    plan: DeckPlan,
    tables_by_id: dict[str, Table],
    charts_by_id: dict[str, Chart],
    assets_list: list[Asset],
    generated_refs: frozenset[str] = frozenset(),
    diagram_ids: frozenset[str] = frozenset(),
) -> list[frozenset[str]]:
    """Per-slide capability requirements из юнитов плана (OR-004).

    ``chart``/``table``/``image`` требуются только когда реф юнита
    резолвится в данные ContentPack'а — иначе просить капабилити у
    exemplar'а бессмысленно (юнит честно дропнется downstream).
    *generated_refs* (ADR-017) — asset_ref'ы model-сгенерированных
    картинок (нет в ContentPack, поэтому не резолвятся через
    ``_match_asset`` — тот же принцип, что и в ``resolve_slide_images``'s
    ``generated`` параметре). *diagram_ids* (ADR-031 §6) — id схем пака:
    слайду со схемой нужен образец с местом под поток (``has_diagram_room``).
    """
    needs: list[frozenset[str]] = []
    for slide in plan.slides:
        need: set[str] = set()
        for unit in slide.content_units:
            if unit.kind == Kind.chart and unit.chart_ref in charts_by_id:
                # ADR-033 §6: график из данных строится заново по типу —
                # образцу нужна не рамка графика, а место под него
                need.add("data_chart" if charts_by_id[unit.chart_ref].chart_type else "chart")
            if unit.kind == Kind.table and unit.table_ref in tables_by_id:
                # ADR-033: таблица из данных строится с нуля на всю ширину —
                # табличный образец шаблона обрезал бы её колонки
                need.add("data_chart" if tables_by_id[unit.table_ref].source_ref.range else "table")
            if unit.kind == Kind.diagram and unit.diagram_ref in diagram_ids:
                need.add("diagram")
            if unit.kind == Kind.image and (
                unit.asset_ref in generated_refs
                or (_match_asset(unit.asset_ref, assets_list) if unit.asset_ref else assets_list)
            ):
                need.add("image")
        needs.append(frozenset(need))
    return needs


_MIN_DATA_W_EMU = 2_000_000
_MIN_DATA_H_EMU = 1_500_000
_TABLE_ROW_H_EMU = 370_840  # table_fill._ROW_H_EMU


def _has_data_frame(source_xml: bytes, has_table: bool, has_chart: bool) -> bool:
    """У образца есть нативная рамка-носитель нужного вида: a:tbl для
    таблицы или graphicFrame c uri chart для графика. По graphicData, не
    по подстроке ns: экспорт Google Slides объявляет пространство имён
    chart в корне КАЖДОГО слайда."""
    src = source_xml.decode("utf-8", "ignore")
    return (
        has_chart and 'uri="http://schemas.openxmlformats.org/drawingml/2006/chart"' in src
    ) or (has_table and re.search(r"<(?:\w+:)?tbl[\s>]", src) is not None)


def _needs_data_room(
    source_xml: bytes,
    slide_xml: bytes,
    has_table: bool,
    has_chart: bool,
    sw: int,
    sh: int,
    table_rows: int = 0,
    data_chart: bool = False,
    chart_source_note: bool = False,
    chart_obstacles: tuple[tuple[int, int, int, int], ...] = (),
) -> bool:
    """Нужна ли своя раскладка под данные: у образца нет рамки нужного
    вида И на заполненном слайде нет пустой рамки/свободного места под
    график или таблицу разумного размера.

    ADR-033 §6: график из данных — главное на слайде; ему нужно место
    ≥ 40% ширины и высоты (пустое тело или свободная область под
    заголовком), иначе своя раскладка «текст слева, график справа»."""
    if not (has_table or has_chart):
        return False
    # A newly created legacy chart needs the same space as a typed chart.
    # Existing legacy frames retain their donor geometry in this path.
    if has_chart and not data_chart:
        source = etree.fromstring(source_xml)
        data_chart = not any(
            node.tag == "{http://schemas.openxmlformats.org/drawingml/2006/chart}chart"
            for node in source.iter()
        )
    if data_chart:
        root = etree.fromstring(slide_xml)
        return not any(
            readable_chart_host(h, (sw, sh), chart_source_note)
            for h in chart_hosts(root, (sw, sh), chart_obstacles)
        )
    own_frame = _has_data_frame(source_xml, has_table, has_chart)
    if own_frame:
        return False
    hosts = _empty_body_hosts(etree.fromstring(slide_xml), (sw, sh))
    # таблица растёт строками: нужна высота всех строк, не минимум графика
    min_h = max(_MIN_DATA_H_EMU, table_rows * _TABLE_ROW_H_EMU)
    return not any(h[3] >= _MIN_DATA_W_EMU and h[4] >= min_h for h in hosts)


def _default_choices(
    pkg,
    plan,
    tables_by_id,
    charts_by_id,
    assets_list,
    diagrams_by_id,
    generated_images,
    strategy,
    design_dna,
) -> list[ExemplarChoice]:
    """Выбор образцов по плану (детерминированный) — общий для полной
    сборки и пробных слайдов стиля (ADR-034 §5)."""
    needs = slide_needs(
        plan,
        tables_by_id,
        charts_by_id,
        assets_list,
        frozenset(generated_images),
        diagram_ids=frozenset(diagrams_by_id),
    )
    # Capacity-aware reassignment (needs-free picks only): a sparse
    # slide landing on a dense card-grid exemplar left most cards
    # empty, a dense slide landing on a sparse one overflowed/
    # collided with neighbors — both reported live on generated
    # decks. select_exemplar_slides() only reorders which already-
    # selected exemplar goes to which slide position; it never
    # changes the set of exemplars picked.
    visible_texts = [_slide_texts(sp, language=plan.language or "ru")[1] for sp in plan.slides]
    unit_counts = [len(items) for items in visible_texts]
    text_lengths = [max((len(t) for t in items), default=0) for items in visible_texts]
    entity_specific_parts = [
        e.part
        for e in (design_dna.exemplars if design_dna else [])
        if e.content_description and (e.content_is_entity_specific or e.content_is_template_meta)
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
    choices = _assign_frame_choices(pkg, plan, choices, design_dna)
    return _diversify_exemplar_layout_families(
        pkg,
        plan,
        choices,
        needs=needs,
        unit_counts=unit_counts,
        entity_specific_parts=entity_specific_parts,
    )


def _has_three_identical_layout_streak(
    plan: DeckPlan,
    choices: list[ExemplarChoice],
) -> bool:
    """Есть ли серия >= 3 подряд одинаковых layout_part на контентных слайдах."""
    from deckdna.pptx.composing.native_plan import FRAME_PURPOSES

    streak = 0
    prev_layout: str | None = None
    for slide, choice in zip(plan.slides, choices, strict=False):
        if slide.purpose in FRAME_PURPOSES:
            continue
        fam = choice.layout_part or choice.slide_part
        if fam == prev_layout:
            streak += 1
            if streak >= 3:
                return True
        else:
            prev_layout = fam
            streak = 1
    return False


def _diversify_exemplar_layout_families(
    pkg: OpcPackage,
    plan: DeckPlan,
    choices: list[ExemplarChoice],
    *,
    needs: Iterable[Any] | None = None,
    unit_counts: list[int] | None = None,
    entity_specific_parts: Iterable[str] = (),
) -> list[ExemplarChoice]:
    """Разбить серии >= 3 одинаковых семейств макетов по формуле:
    ``cost = fit + 0.8*(family == prev) + 0.3*count_in_last_4``.

    Три одинаковых семейства подряд запрещены, если в шаблоне есть подходящая
    альтернатива (совместимая по носителю/слотам, без offslide и чужих сущностей).
    Тип графика ради разнообразия не меняется.
    """
    if len(choices) < 2:
        return choices

    from deckdna.pptx.cloning.exemplar import (
        _presentation_slide_size,
        content_horizontal_span,
        has_chart_room,
        has_offslide_content_slots,
        is_team_roster_exemplar,
        run_profile,
        slide_capabilities,
    )
    from deckdna.pptx.composing.native_plan import FRAME_PURPOSES, select_layout_family

    size_pair = _presentation_slide_size(pkg) or (9144000, 5143500)
    layout_of = slide_layout_map(pkg)
    entity_set = set(entity_specific_parts) | set(
        meta_slides(pkg, [p for p in pkg.parts if p.startswith("ppt/slides/slide")])
    )

    candidate_info: dict[str, dict[str, Any]] = {}
    for sp, lp in layout_of.items():
        if not lp or sp in entity_set or sp not in pkg.parts:
            continue
        if has_offslide_content_slots(pkg, sp):
            continue
        slots = count_content_slots(pkg.parts[sp], size_pair)
        caps = slide_capabilities(pkg, sp)
        if slots == 0 and not caps:
            continue
        span = content_horizontal_span(pkg, sp)
        prof = run_profile(pkg, sp)
        candidate_info[sp] = {
            "slide_part": sp,
            "layout_part": lp,
            "slots": slots,
            "caps": caps,
            "shapes": shape_count(pkg, sp),
            "chart_room": has_chart_room(pkg, sp),
            "span": span,
            "broad": (span[1] - span[0]) >= 0.45 and span[0] <= 0.35 and span[1] >= 0.80,
            "content_like": prof.is_content_like,
            "mean_len": prof.mean_nonempty_len,
            "team": is_team_roster_exemplar(pkg, sp),
        }

    needs_list = list(needs) if needs is not None else []
    out = list(choices)
    history: list[str] = []
    for idx, (slide, choice) in enumerate(zip(plan.slides, out, strict=False)):
        cur_family = choice.layout_part or choice.slide_part
        if slide.purpose in FRAME_PURPOSES:
            history.append(cur_family)
            continue

        raw_need = needs_list[idx] if idx < len(needs_list) else None
        need: str | None = None
        if isinstance(raw_need, str):
            need = "chart" if raw_need == "data_chart" else raw_need
        elif isinstance(raw_need, (set, frozenset, list, tuple)):
            if "chart" in raw_need or "data_chart" in raw_need:
                need = "chart"
            elif "table" in raw_need:
                need = "table"
            elif "diagram" in raw_need:
                need = "diagram"
            elif "image" in raw_need:
                need = "image"

        if need is None:
            for u in slide.content_units:
                if u.kind == Kind.chart:
                    need = "chart"
                    break
                if u.kind == Kind.table:
                    need = "table"
                    break
                if u.kind == Kind.diagram:
                    need = "diagram"
                    break
                if u.kind == Kind.image:
                    need = "image"
                    break
        need_slots = (
            unit_counts[idx]
            if unit_counts is not None and idx < len(unit_counts)
            else max(1, sum(1 for u in slide.content_units if u.kind != Kind.title))
        )

        # Собираем лучший кандидат для каждого совместимого семейства макета.
        # Разнообразие не побеждает вместимость (§7): альтернатива должна быть
        # не хуже по content_like, широте полосы и окну слотов.
        cur_info = candidate_info.get(choice.slide_part)
        cur_has_native_cap = bool(cur_info and need and need in cur_info["caps"])
        require_content_like = bool(cur_info and cur_info["content_like"])
        require_broad = bool(cur_info and cur_info["broad"])
        max_allowed_slots = max(need_slots + 2, need_slots * 2, choice.content_slots + 2)
        by_family: dict[str, tuple[float, dict[str, Any]]] = {}

        for sp, info in candidate_info.items():
            if info["team"] and slide.purpose.value != "team":
                continue
            if require_content_like and not info["content_like"]:
                continue
            if require_broad and not info["broad"]:
                continue
            if (
                require_content_like
                and info["mean_len"] < 20.0
                and (cur_info and cur_info["mean_len"] >= 20.0)
            ):
                continue
            if need == "chart":
                if cur_has_native_cap:
                    if "chart" not in info["caps"]:
                        continue
                else:
                    if not ("chart" in info["caps"] or (info["chart_room"] and info["broad"])):
                        continue
            elif need in ("table", "image", "diagram"):
                if cur_has_native_cap and need not in info["caps"]:
                    continue
                if not cur_has_native_cap and not (info["broad"] and info["slots"] >= 1):
                    continue
            else:
                if info["slots"] < min(max(need_slots, 1), max(choice.content_slots, 1)):
                    continue
                if info["slots"] > max_allowed_slots:
                    continue

            # Нормализованный fit: 0.0 для текущего образца, штраф за смену и отклонение слотов
            slot_diff = abs(info["slots"] - max(need_slots, 1))
            base_fit = 0.0 if sp == choice.slide_part else round(0.25 + 0.12 * min(slot_diff, 4), 4)
            fam = info["layout_part"]
            prev_entry = by_family.get(fam)
            if prev_entry is None or base_fit < prev_entry[0]:
                by_family[fam] = (base_fit, info)

        if len(by_family) >= 2:
            cand_fits = {fam: entry[0] for fam, entry in by_family.items()}
            chosen_fam = select_layout_family(cand_fits, history)
            if chosen_fam != cur_family and chosen_fam in by_family:
                chosen_info = by_family[chosen_fam][1]
                out[idx] = dataclass_replace(
                    choice,
                    slide_part=chosen_info["slide_part"],
                    layout_part=chosen_info["layout_part"],
                    shape_count=chosen_info["shapes"],
                    content_slots=chosen_info["slots"],
                )
                cur_family = chosen_fam

        history.append(cur_family)

    return out


def _local_design_dna(pkg: OpcPackage) -> DesignDNA:
    """Тот же детерминированный разбор, что при загрузке шаблона в API."""
    from datetime import UTC, datetime
    from io import BytesIO
    from zipfile import ZipFile

    from deckdna.template.dna import build_design_dna

    parts = slide_layout_map(pkg)
    data = BytesIO()
    with ZipFile(data, "w") as archive:
        for name, value in pkg.parts.items():
            archive.writestr(name, value)
    try:
        return build_design_dna(
            data.getvalue(),
            None,
            "local",
            "local",
            {i: f"local-{i}" for i in range(len(parts))},
            datetime.now(UTC),
            "1.0",
        ).design_dna
    except KeyError as exc:
        # python-pptx разрешает связи layout/master раньше OPC-клонирования.
        # Повреждённая связь должна сохранить прежнюю typed-ошибку пакета.
        raise DeckDNAError(
            code="package_corrupt",
            message=f"template relationship cannot be resolved: {exc}",
            stage="composing.template_roles",
        ) from exc


def _assign_frame_choices(
    pkg: OpcPackage,
    plan: DeckPlan,
    choices: list[ExemplarChoice],
    design_dna: DesignDNA | None,
) -> list[ExemplarChoice]:
    """CLI и офлайн-сборка используют те же роли обложки/финала, что API.

    Раньше без сохранённой DNA титул попадал в обычный контентный макет
    с мелким заголовком в углу. Роли вычисляются существующим autopsy,
    по структуре произвольного шаблона.
    """
    from deckdna.pptx.cloning.layout_fit import assign_role_exemplars

    return assign_role_exemplars(pkg, plan, choices, design_dna or _local_design_dna(pkg))


def apply_exemplar_overrides(
    pkg,
    plan: DeckPlan,
    choices: list[ExemplarChoice],
    overrides: dict[str, str],
) -> list[ExemplarChoice]:
    """Подменить образец у слайдов плана по id (``slide id → slide part``)."""
    from dataclasses import replace

    layout_of = slide_layout_map(pkg)
    out = list(choices)
    for i, slide in enumerate(plan.slides):
        part = overrides.get(slide.id)
        if part and part in pkg.parts and i < len(out) and out[i].slide_part != part:
            out[i] = replace(
                out[i],
                slide_part=part,
                layout_part=layout_of.get(part) or out[i].layout_part,
                shape_count=shape_count(pkg, part),
            )
    return out


def plan_exemplar_choices(
    template_path: str | Path,
    deck_plan: DeckPlan,
    *,
    tables: Iterable[Table] = (),
    charts: Iterable[Chart] = (),
    diagrams: Iterable[Diagram] = (),
    assets: Iterable[Asset] = (),
    strategy: Strategy = Strategy.balanced,
    design_dna: DesignDNA | None = None,
) -> list[ExemplarChoice]:
    """Образцы, которые полная сборка выберет для каждого слайда плана
    (тот же код, что в ``generate_deck``)."""
    pkg = OpcPackage.open(Path(template_path))
    return _default_choices(
        pkg,
        deck_plan,
        {t.id: t for t in tables},
        {c.id: c for c in charts},
        list(assets),
        {d.id: d for d in diagrams},
        {},
        strategy,
        design_dna,
    )


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
    exemplar_overrides: dict[str, str] | None = None,
    on_slide_compiled: Callable[[int, str, OpcPackage], None] | None = None,
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
    if exemplar_choices is None:
        choices = _default_choices(
            pkg,
            plan,
            tables_by_id,
            charts_by_id,
            assets_list,
            diagrams_by_id,
            generated_images,
            strategy,
            design_dna,
        )
    else:
        # Предвыбранные exemplar'ы (LLM top-k rerank в pipeline): честный
        # contract — длина строго равна числу слайдов плана.
        choices = list(exemplar_choices)
        if len(choices) != len(plan.slides):
            raise DeckDNAError(
                code="invalid_input",
                message=(
                    f"exemplar_choices has {len(choices)} picks for {len(plan.slides)} plan slides"
                ),
                stage="composing.deck",
            )
        if not exemplar_overrides:
            entity_specific_parts = [
                e.part
                for e in (design_dna.exemplars if design_dna else [])
                if e.content_description
                and (e.content_is_entity_specific or e.content_is_template_meta)
            ]
            choices = _diversify_exemplar_layout_families(
                pkg,
                plan,
                choices,
                entity_specific_parts=entity_specific_parts,
            )

    if exemplar_overrides:
        # ADR-034 §5: образцы пробных слайдов стиля — полная колода берёт те же
        choices = apply_exemplar_overrides(pkg, plan, choices, exemplar_overrides)

    # python-pptx view of the template: per-shape effective font size
    # and usable box incl. placeholder inheritance — fitting baselines.
    from pptx import Presentation

    prs = Presentation(str(template_path))
    hint_cache: dict[str, dict[str, dict]] = {}

    # Physical ZIP names must come from raw relationships. python-pptx
    # renames its slide parts in memory when ``slides`` is accessed.
    template_parts = pkg.ordered_slide_parts()
    template_slides = dict(zip(template_parts, prs.slides, strict=True))
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
                    f"protected slide index {idx} outside template ({len(template_parts)} slides)"
                ),
                stage="composing.deck",
            )

    content = Path(content_path) if content_path is not None else None
    used_asset_ids: set[str] = set()
    plan_specs: list[tuple[str, bytes]] = []
    plan_reports: list[dict[str, Any]] = []
    sw, sh = int(prs.slide_width), int(prs.slide_height)
    tpl_observed_scale = package_observed_font_scale_from_parts(pkg.parts, min_uses=2)
    dna_scale = (
        tuple(fs.size_pt for fs in design_dna.observed.font_sizes if fs.frequency >= 2)
        if design_dna is not None and getattr(design_dna, "observed", None) is not None
        else ()
    )
    from deckdna.planning.config import load_generation_config

    agenda_policy = (
        load_generation_config().raw.get("composition", {}).get("agenda_readability", {})
    )
    agenda_min_width = float(agenda_policy.get("min_slot_width_ratio", 0.22))
    card_policy = load_generation_config().raw.get("composition", {}).get("card_readability", {})
    source_number = 1
    seen_slide_parts: set[str] = set()
    native_family_history: list[str] = []
    frame_cache: dict[str, Any] = {}
    deck_frame: dict[str, Any] = {}

    def _frame_for(part: str):
        """Рамка образца для своей композиции; None — рамка захламлена
        графикой макета или слишком мала (см. best_frame)."""
        if part not in frame_cache:
            frame = build_frame(pkg, part, sw, sh)
            ok = (
                frame is not None
                and frame.region.w > 0
                and frame.region.h > 0
                and frame.region.w * frame.region.h >= 0.25 * sw * sh
                and float(frame.tokens.get("inherited_clutter") or 0.0) <= 0.05
            )
            frame_cache[part] = frame if ok else None
        return frame_cache[part]

    def _deck_frame():
        if "frame" not in deck_frame:
            deck_frame["frame"] = best_frame(pkg, [c.slide_part for c in choices], sw, sh)
        return deck_frame["frame"]

    for slide, choice in zip(plan.slides, choices, strict=True):
        (
            title_text,
            texts,
            dropped,
            table_refs,
            chart_refs,
            diagram_refs,
            image_refs,
        ) = _slide_texts(slide, language=plan.language or "ru")
        if choice.slide_part not in hint_cache:
            hint_cache[choice.slide_part] = _exemplar_shape_metrics(
                prs, choice.slide_part, source_slide=template_slides.get(choice.slide_part)
            )
        sources_slide = (
            is_sources_slide(slide.title_intent)
            and slide.purpose.value != "section_divider"
            and bool(texts)
        )
        source_xml = pkg.parts[choice.slide_part]
        if slide.purpose.value in ("thank_you", "qa", "cta") and not texts:
            from deckdna.planning.story_director import clean_purpose_text

            title_locked = any(u.kind == Kind.title and u.locked for u in slide.content_units)
            cta_clean = clean_purpose_text(plan.brief.call_to_action) or ""
            contacts: list[str] = []
            for raw_src in (plan.brief.purpose or "", plan.brief.call_to_action or ""):
                for match in _CONTACT_IN_BRIEF_RE.findall(raw_src):
                    if match not in contacts and match not in (title_text or ""):
                        contacts.append(match)
            slots_avail = count_content_slots(source_xml, (sw, sh))
            has_sub_ph = (
                etree.fromstring(source_xml).find(f".//{_P}ph[@type='subTitle']") is not None
            )
            if slide.purpose.value == "cta" and cta_clean and not title_locked:
                if slots_avail > 0 and cta_clean.casefold() != (title_text or "").casefold():
                    texts = [cta_clean, *contacts][:slots_avail]
                else:
                    title_text = cta_clean
            elif contacts and slots_avail > 0:
                texts = contacts[:slots_avail]
            elif cta_clean and not title_locked and has_sub_ph and slots_avail > 0:
                texts = [cta_clean][:slots_avail]
        hero_card = (
            strategy == Strategy.visual
            and not sources_slide
            and slide.purpose.value not in ("title", "thank_you", "section_divider")
            and len(texts) == 1
            and not (table_refs or chart_refs or diagram_refs or image_refs)
        )
        dense_prose = (
            not sources_slide
            and not hero_card
            and slide.purpose.value not in ("title", "thank_you", "section_divider")
            and (
                len(texts) > 6 or (len(texts) > 3 and max((len(t) for t in texts), default=0) > 250)
            )
            and not (table_refs or chart_refs or diagram_refs or image_refs)
        )
        # Ось различий вариантов на композиции (native_plan): faithful
        # держит эталон (своя композиция только при повторе того же образца
        # в колоде — копипаст), balanced — когда сетка карточек эталона не
        # вмещает пункты, visual — своя композиция для всех текстовых
        # контентных слайдов. Слайды-носители данных (таблица, график,
        # схема, картинка) и рамки колоды остаются эталонными.
        native = None
        native_frame = None
        if not (
            sources_slide
            or dense_prose
            or hero_card
            or (exemplar_overrides and slide.id in exemplar_overrides)
        ):
            native = decide_native(
                slide,
                source_xml,
                choice.slide_part,
                seen_slide_parts,
                strategy,
                sw,
                sh,
                family_history=native_family_history,
            )
        seen_slide_parts.add(choice.slide_part)
        native_xml = None
        if native is not None:
            native_frame = _frame_for(choice.slide_part) or _deck_frame()
            if native_frame is not None:
                # Своя композиция в рамке эталона (native_compose):
                # заголовок и фон шаблона сохраняются, контент рисуется
                # его токенами. Без права сокращать пункты многоточием —
                # если вербатим не влезает, честнее эталонная заливка с
                # уменьшением кегля, а не потерю слов пользователя.
                native_xml = native_compose(
                    native_frame.xml,
                    native_frame.region,
                    native_frame.tokens,
                    native[0],
                    title_text or slide.title_intent or "",
                    native[1],
                    sw,
                    sh,
                    allow_shorten=False,
                )
            if native_xml is None:
                native = None
            else:
                native_family_history.append(native[0])
                if native_frame.part != choice.slide_part:
                    # XML рамы ссылается на media/layout своего образца.
                    # Клонировать его с relationships прежнего донора нельзя.
                    choice = dataclass_replace(
                        choice,
                        slide_part=native_frame.part,
                        layout_part=slide_layout_map(pkg).get(native_frame.part)
                        or choice.layout_part,
                        shape_count=shape_count(pkg, native_frame.part),
                    )
                    source_xml = pkg.parts[choice.slide_part]
                    seen_slide_parts.add(choice.slide_part)
                    if choice.slide_part not in hint_cache:
                        hint_cache[choice.slide_part] = _exemplar_shape_metrics(
                            prs,
                            choice.slide_part,
                            source_slide=template_slides.get(choice.slide_part),
                        )
        if native is None and slide.purpose.value not in (
            "title",
            "thank_you",
            "section_divider",
            "qa",
            "cta",
        ):
            native_family_history.append(choice.slide_part)
        s_scale = slide_font_scale(
            pkg, choice.slide_part, observed=tpl_observed_scale, dna_scale=dna_scale
        )
        visual_cards = (
            (strategy == Strategy.visual or hero_card)
            and not sources_slide
            and not dense_prose
            and native is None
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
        # финал шаблона уже благодарит («Спасибо!» дисплейным кеглем на том же
        # языке, что колода) — его авторский текст лучше нашего «Спасибо за
        # внимание», который в ту же рамку не помещается; чужой язык («Thanks!»)
        # обязательно заменяется текстом на языке колоды
        if slide.purpose.value in ("thank_you", "qa") and _donor_has_same_lang_thanks(
            source_xml, title_text, plan.language
        ):
            title_text = None
        cards_cloned = 0
        source_pages: list[tuple[bytes, int]] = []
        fill_texts = texts
        baked_grid = rigid_art(choice.slide_part)
        card_col_limit = (
            int(card_policy.get("max_columns", 3))
            if not baked_grid
            and any(len(t) > int(card_policy.get("prose_min_chars", 50)) for t in texts)
            else None
        )
        role_slide = slide.purpose.value in ("title", "thank_you", "section_divider")
        if native is None and not (sources_slide or dense_prose or visual_cards or role_slide):
            n_cards, per_card = card_slots(source_xml, sw, sh)
            if (
                n_cards
                and per_card in (1, 2)
                and count_content_slots(source_xml, (sw, sh)) == n_cards * per_card
            ):
                # на «запечённой» сетке клон ляжет мимо картинки, но потерять
                # пункт хуже; путь с моделью такие несовпадения отсекает ещё
                # при выборе эталона (layout_fit._fits_rigid)
                if (len(texts) > n_cards or card_col_limit) and not (chart_refs or image_refs):
                    source_xml, cards_cloned = clone_cards(
                        source_xml,
                        len(texts),
                        sw,
                        sh,
                        max_columns=card_col_limit,
                    )
                if per_card == 2:
                    fill_texts = pair_texts_for_cards(texts, per_card)
        slide_xml, text_report = replace_text_runs(
            source_xml,
            []
            if sources_slide or dense_prose or visual_cards or native is not None
            else fill_texts,
            title_text=(
                None
                if sources_slide or dense_prose or visual_cards or native is not None
                else title_text
            ),
            shape_hints=hint_cache[choice.slide_part],
            slide_size=(sw, sh),
            font_scale=s_scale,
            cover=slide.purpose.value in ("title", "thank_you", "qa", "cta", "section_divider"),
            kicker_text=next((u.text for u in slide.content_units if u.role == "eyebrow"), None),
        )
        if native_xml is not None:
            slide_xml = native_xml
        elif (
            (table_refs or chart_refs or diagram_refs)
            and not (sources_slide or dense_prose or visual_cards)
            and slide.purpose.value not in ("title", "thank_you", "section_divider")
        ):
            slide_xml, _ = fit_oversized_body_before_data(
                slide_xml, sw, sh, hint_cache[choice.slide_part]
            )
        # Сетку карточек шаблона правят его же блоками (клонирование,
        # reflow, бюджет текста) — решение владельца 28.09: слайды «с нуля»
        # только там, где у эталона сетки нет.
        narrow_agenda = (
            slide.purpose.value == "agenda"
            and not baked_grid
            and any(
                0 < hint_cache[choice.slide_part].get(sid, {}).get("w", 0) < sw * agenda_min_width
                for sid in text_report.filled_shape_ids
            )
        )
        adaptive_cards = (
            not sources_slide
            and not dense_prose
            and not visual_cards
            and native is None
            and (
                narrow_agenda
                or text_report.bodies_overflowing > 0
                # сетка, не вместившая все пункты (часть слотов ушла из
                # зоны заголовка, текстов больше, чем карточек), — тоже
                # не справилась: лучше нативные карточки, чем потерянный пункт
                or text_report.texts_dropped > 0
                or not card_slots(pkg.parts[choice.slide_part], sw, sh)[0]
            )
            and slide.purpose.value not in ("title", "thank_you", "section_divider")
            and 1 <= len(texts) <= 6
            and not (table_refs or chart_refs or diagram_refs or image_refs)
            and (
                text_report.bodies_overflowing > 0
                or text_report.texts_dropped > 0
                or choice.content_slots > max(len(texts) + 2, len(texts) * 2)
                or narrow_agenda
            )
        )
        # A readable native object is not enough if its source caption was
        # squeezed into a microscopic donor slot. Only inspect filled bodies,
        # so system labels and donor decorations cannot trigger this fallback.
        small_caption = False
        if text_report.bodies_shrunk and (table_refs or chart_refs or diagram_refs or image_refs):
            minimum = float(
                load_generation_config().raw.get("composition", {}).get("min_body_font_pt", 10)
            )
            for shape in etree.fromstring(slide_xml).iter(_P + "sp"):
                ident = shape.find(_P + "nvSpPr/" + _P + "cNvPr")
                if ident is None or ident.get("id") not in text_report.filled_shape_ids:
                    continue
                sizes = [
                    float(node.get("sz")) / 100
                    for node in shape.iter()
                    if node.tag in (_A + "rPr", _A + "defRPr") and node.get("sz")
                ]
                small_caption |= bool(sizes and min(sizes) < minimum)
        structural_caption_fallback = bool(
            table_refs or chart_refs or diagram_refs or image_refs
        ) and (text_report.bodies_overflowing > 0 or text_report.texts_dropped > 0 or small_caption)
        slide_charts: list[Chart] = []
        # ADR-033 §4: тип графика из плана (выбор кода или правка
        # пользователя) — поверх типа по умолчанию у графика пака
        spec_types = [
            u.chart_spec.chart_type.value if u.chart_spec and u.chart_spec.chart_type else None
            for u in slide.content_units
            if u.kind == Kind.chart
        ]
        for ref, spec_type in zip(chart_refs, spec_types, strict=False):
            chart = charts_by_id.get(ref) if ref else None
            if chart is None:
                dropped["chart"] = dropped.get("chart", 0) + 1
            else:
                slide_charts.append(shape_for_type(chart, spec_type))
        # график донора без данных колоды показал бы демо-цифры шаблона
        # («42% / 32%» — выдуманный факт): рамка графика удаляется
        demo_charts_removed = 0
        if not slide_charts:
            slide_xml, demo_charts_removed = _remove_chart_frames(slide_xml)
        slide_images, images_dropped = resolve_slide_images(
            image_refs, assets_list, content, used_asset_ids, generated=generated_images
        )
        if images_dropped:
            dropped["image"] = dropped.get("image", 0) + images_dropped
        removed_artwork = 0
        claimed_parts: set[str] = set()
        if not slide_images:
            # Covers and native frames can carry donor figures too. Actual
            # canvas backgrounds and small branding survive this shared rule.
            slide_xml, removed_artwork = _remove_unmatched_artwork(slide_xml, sw * sh, (sw, sh))
        else:
            from deckdna.pptx.opc.package import resolve_target

            s_rels = list(pkg.rels(choice.slide_part))
            claimed_parts = set(
                _blip_part_order(slide_xml, choice.slide_part, s_rels)[: len(slide_images)]
            )
            claimed_rids = {
                rel.id
                for rel in s_rels
                if not rel.is_external
                and resolve_target(choice.slide_part, rel.target) in claimed_parts
            }
            slide_xml, removed_artwork = _remove_unmatched_artwork(
                slide_xml, sw * sh, (sw, sh), keep_rids=claimed_rids
            )
        if (
            slide.purpose.value == "title"
            and title_text
            and text_report.title_placed
            and native is None
        ):
            from deckdna.pptx.composing.text_replace import (
                _body_font_size_pt,
                _is_title_run,
                _shape_id,
            )

            root_check = etree.fromstring(slide_xml)
            has_title_ph = any(_is_title_run(t) for t in root_check.iter(f"{_A}t"))
            if not has_title_ph:
                placed_sz = 0.0
                expected_norm = " ".join(title_text.split())
                for sp in root_check.iter(f"{_P}sp"):
                    tb = sp.find(f"{_P}txBody")
                    if tb is None:
                        continue
                    sp_txt = " ".join(" ".join(t.text or "" for t in tb.iter(f"{_A}t")).split())
                    if sp_txt == expected_norm:
                        placed_sz = _body_font_size_pt(tb) or hint_cache[choice.slide_part].get(
                            _shape_id(tb) or "", {}
                        ).get("sz", 0.0)
                        break
                if 0 < placed_sz < 22.0:
                    kicker_val = next(
                        (u.text for u in slide.content_units if u.role == "eyebrow"), None
                    )
                    slide_xml = compose_cover_slide(
                        slide_xml,
                        title_text,
                        texts[0] if texts else None,
                        kicker_val,
                        sw,
                        sh,
                        has_image=bool(slide_images),
                    )
                    text_report = dataclass_replace(
                        text_report,
                        bodies_truncated=0,
                        texts_dropped=0,
                        bodies_overflowing=0,
                        title_placed=True,
                    )
        # Опустевшее оформление эталона — аватар у пустого «Имя/Должность»,
        # слот картинки, панель очищенной колонки — снимается ДО раскладки
        # данных: иначе мёртвая заливка «занимает» область и график/таблица
        # вынужденно жмутся вниз или за край слайда (живой дефект: пустые
        # чёрные прямоугольники над прижатым графиком).
        orphans_removed = 0
        if not (
            sources_slide or dense_prose or visual_cards or adaptive_cards or native is not None
        ):
            slide_xml, orphans_removed = remove_orphans(
                source_xml,
                slide_xml,
                int(prs.slide_width),
                int(prs.slide_height),
                keep_pictures=bool(slide_images),
                closing=slide.purpose.value in ("thank_you", "qa", "cta"),
            )
        # График/таблица без своей рамки у образца и без места под них
        # (живой дефект 29.09: «1 chart unit dropped») — своя раскладка
        # «текст слева, данные справа» вместо потери данных.
        # Ось вариантов (OR-007): faithful заполняет эталон как есть;
        # balanced и visual на data-слайдах без нативной рамки-носителя
        # собирают свою «текст + данные» раскладку — иначе на шаблоне с
        # малым числом разнотипных слайдов все три варианта сходились на
        # одном образце и одной вёрстке (BE2). Слот картинки в этой
        # раскладке не восстановить — слайды с image-юнитами эталонные.
        strategy_split = (
            strategy != Strategy.faithful
            # одна область данных — один юнит; при двух+ раскладка
            # «текст + данные» отняла бы у второго юнита последний хост
            and len(table_refs) + len(chart_refs) == 1
            and not image_refs
            and not _has_data_frame(source_xml, bool(table_refs), bool(chart_refs))
        )
        image_split = False
        if (
            slide_images
            and not claimed_parts
            and not (table_refs or chart_refs or diagram_refs)
            and texts
        ):
            from deckdna.pptx.composing.table_fill import _free_space_host

            img_root = etree.fromstring(slide_xml)
            if not _empty_picture_placeholders(img_root) and not _empty_body_hosts(img_root):
                img_free = _free_space_host(img_root, sw, sh)
                image_split = (
                    img_free is None
                    or img_free[0] < int(sw * sh * 0.12)
                    or min(img_free[3], img_free[4]) < int(sh * 0.28)
                )
        data_split = (
            not sources_slide
            and not dense_prose
            and not visual_cards
            and native is None
            and slide.purpose.value not in ("title", "thank_you", "section_divider")
            and (
                strategy_split
                or image_split
                or structural_caption_fallback
                or _needs_data_room(
                    source_xml,
                    slide_xml,
                    bool(table_refs),
                    bool(chart_refs),
                    sw,
                    sh,
                    table_rows=max(
                        (len(t.rows) + 1 for r in table_refs if (t := tables_by_id.get(r))),
                        default=0,
                    ),
                    data_chart=any(
                        (c := charts_by_id.get(r)) is not None and c.chart_type
                        for r in chart_refs
                        if r
                    ),
                    chart_source_note=any(
                        (c := charts_by_id.get(r)) is not None and c.source_note
                        for r in chart_refs
                        if r
                    ),
                    chart_obstacles=inherited_chart_obstacles(pkg.parts, choice.slide_part)
                    if chart_refs
                    else (),
                )
            )
        )
        if data_split:
            # visual — зеркальная раскладка «данные слева, текст справа»
            # (график/таблица — главное на слайде, читается первым)
            slide_xml = compose_data_split(
                slide_xml,
                title_text or slide.title_intent,
                texts,
                sw,
                sh,
                mirror=strategy == Strategy.visual,
            )
        if sources_slide:
            # The donor's card boxes may each be far too short for a
            # citation. A dedicated two-column native layout keeps all
            # sources editable and avoids overlap/overflow.
            title = slide.title_intent
            if (plan.language or "").startswith("ru"):
                title = "Источники — продолжение" if source_number > 1 else "Источники"
            source_pages = paginate_sources(
                slide_xml,
                title,
                texts,
                sw,
                sh,
                source_number,
                continuation_title=(
                    "Источники — продолжение"
                    if (plan.language or "").startswith("ru")
                    else f"{title} — continued"
                ),
            )
            slide_xml = source_pages[0][0]
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
                slide_xml,
                title_text or slide.title_intent,
                display_entries,
                int(prs.slide_width),
                int(prs.slide_height),
            )
            if shortened:
                dropped["text_truncated"] = shortened
        else:
            shortened = 0
            flattened_tables = 0
        if visual_cards or adaptive_cards:
            slide_xml, visual_shortened = compose_visual_cards(
                slide_xml,
                title_text or slide.title_intent,
                texts,
                int(prs.slide_width),
                int(prs.slide_height),
                cards=(strategy != Strategy.faithful or len(texts) <= 3) and not narrow_agenda,
                compact=narrow_agenda,
            )
            if adaptive_cards:
                # The donor text boxes have been replaced by native cards;
                # their preliminary clipping/unplaced counts are no longer
                # part of the delivered slide.
                text_report = dataclass_replace(
                    text_report,
                    bodies_truncated=0,
                    texts_dropped=0,
                    bodies_overflowing=0,
                    bodies_eligible=len(texts),
                    bodies_cleared=0,
                    bodies_filled=len(texts),
                    title_placed=True,
                )
            if visual_shortened:
                dropped["text_truncated"] = dropped.get("text_truncated", 0) + visual_shortened
        if sources_slide or dense_prose or visual_cards or data_split or native is not None:
            # Report the delivered native layout, not the discarded donor pass.
            text_report = dataclass_replace(
                text_report,
                bodies_eligible=len(texts),
                bodies_filled=len(texts),
                bodies_cleared=0,
                bodies_truncated=0,
                bodies_overflowing=0,
                texts_supplied=len(texts),
                texts_dropped=0,
                title_placed=bool(title_text or native is not None),
            )
        if (
            not sources_slide
            and not dense_prose
            and not visual_cards
            and not adaptive_cards
            and not data_split
            and native is None
            and title_text
            and not text_report.title_placed
        ):
            slide_xml = add_missing_title(
                slide_xml,
                title_text,
                int(prs.slide_width),
                int(prs.slide_height),
            )
            text_report = dataclass_replace(text_report, title_placed=True)
        if text_report.texts_dropped:
            dropped["text_unplaced"] = dropped.get("text_unplaced", 0) + text_report.texts_dropped
        if text_report.bodies_truncated:
            dropped["text_truncated"] = (
                dropped.get("text_truncated", 0) + text_report.bodies_truncated
            )
        # Fallback compositions clear donor text boxes; a full-canvas shape
        # or group is artwork even if python-pptx gives it an empty txBody.
        slide_xml = restore_background_layers(source_xml, slide_xml, sw, sh)
        slide_xml = align_composed_text_ink(pkg.parts, choice.slide_part, slide_xml, source_xml)
        resolved: list[Table] = []
        unresolved = 0
        for ref in [] if dense_prose else table_refs:
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
            logger.warning(
                "slide %d: %d table unit(s) dropped "
                "(unresolved=%d, exemplar=%s, data_split=%s, hosts=%s)",
                slide.index,
                dropped_tables,
                unresolved,
                choice.slide_part,
                data_split,
                [h[1:5] for h in _empty_body_hosts(etree.fromstring(slide_xml), (sw, sh))],
            )
            dropped["table"] = dropped.get("table", 0) + dropped_tables
        resolved_diagrams: list[Diagram] = []
        for ref in diagram_refs:
            diagram = diagrams_by_id.get(ref) if ref else None
            if diagram is None:
                dropped["diagram"] = dropped.get("diagram", 0) + 1
            else:
                resolved_diagrams.append(diagram)
        if resolved_diagrams and not image_refs:
            # ADR-031 §5: донорские картинки (демо-графики, фото) уходят до
            # выбора места схемы, иначе они «занимают» область под заголовком
            # и схема выпадает или жмётся в узкую полосу; ниже та же чистка
            # повторно ничего не найдёт
            slide_xml, _ = _remove_unmatched_artwork(slide_xml, sw * sh, (sw, sh))
        slide_xml, diagram_report = fill_slide_diagrams(
            slide_xml,
            resolved_diagrams,
            used_hosts=used_hosts,
            slide_size=(sw, sh),
            layout_busy=(layout_busy_boxes(pkg, choice.slide_part) if resolved_diagrams else ()),
            allow_clear=not baked_grid
            and (
                not resolved_diagrams
                or slide_background_art_ratio(pkg, choice.slide_part) < LAYOUT_ART_THRESHOLD
            ),
        )
        if diagram_report.units_dropped:
            dropped["diagram"] = dropped.get("diagram", 0) + diagram_report.units_dropped
        # Незаполненные карточки сетки эталона удаляются, оставшиеся
        # растягиваются на освободившееся место (как del_* у PPTAgent).
        # Графики и картинки вставляются позже в свободные рамки — на таких
        # слайдах сетку не трогаем, чтобы не отнять у них место.
        reflow_report = None
        # собственные раскладки (источники, плотная проза, родные карточки)
        # — не сетка шаблона: reflow принимал их за «пустые карточки» и
        # удалял все пункты (живой дефект 29.09: пустая повестка)
        own_layout = (
            sources_slide
            or dense_prose
            or visual_cards
            or adaptive_cards
            or data_split
            or native is not None
        )
        # ADR-031 §5: схема заняла опустевшую карточку или область под
        # заголовком — reflow считал её членом незаполненной карточки и
        # удалял вместе с ней (схема не доживала до файла); опустевшие
        # подложки и слоты уже снял remove_orphans выше
        own_layout = own_layout or diagram_report.diagrams_created > 0
        # Повторная чистка: первая (выше) освобождает место ДО раскладки,
        # но панель могла опустеть позже — compose_data_split стёр слоты,
        # которые fill показал «заполненными», или fill_native_tables снял
        # донорскую таблицу внутри панели.
        if not (
            sources_slide or dense_prose or visual_cards or adaptive_cards or native is not None
        ):
            slide_xml, orphans_late = remove_orphans(
                source_xml,
                slide_xml,
                int(prs.slide_width),
                int(prs.slide_height),
                keep_pictures=bool(slide_images),
                closing=slide.purpose.value in ("thank_you", "qa", "cta"),
            )
            orphans_removed += orphans_late
        if (
            not slide_charts
            and not slide_images
            and not baked_grid
            and not own_layout
            and not role_slide
        ):
            slide_xml, reflow_report = reflow_cards(
                slide_xml,
                int(prs.slide_width),
                int(prs.slide_height),
                filled_shape_ids=set(text_report.filled_shape_ids),
                font_scale=s_scale,
            )
        # мелкий демо-кегль шаблона → максимальный, который рамка вмещает
        slide_xml, text_grown = grow_text(
            slide_xml,
            hint_cache[choice.slide_part],
            **(
                {}
                if sources_slide
                else {
                    "body_cap": float(card_policy.get("body_cap_pt", 18)),
                    "label_cap": float(card_policy.get("label_cap_pt", 20)),
                    "slide_size": (sw, sh),
                    "large_frame_cap": float(card_policy.get("summary_large_frame_cap_pt", 32))
                    if slide.purpose.value == "overview"
                    else None,
                }
            ),
        )
        # таблицы/схемы уже на месте — заголовок не должен на них наезжать
        slide_xml, title_shrunk = fit_title_above_content(
            slide_xml,
            int(prs.slide_width),
            int(prs.slide_height),
            hint_cache[choice.slide_part],
            title_text=title_text,
            font_scale=s_scale,
        )
        plan_specs.append((choice.slide_part, slide_xml, slide_charts, slide_images, used_hosts))
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
                "native_composition": native[0] if native is not None else None,
                "data_split_layout": bool(data_split),
                "tables_flattened": flattened_tables,
                "card_reflow": reflow_report.to_dict() if reflow_report else None,
                "text_grown": text_grown,
                "cards_cloned": cards_cloned,
                "orphans_removed": orphans_removed,
                "demo_charts_removed": demo_charts_removed,
                "title_shrunk_for_content": title_shrunk,
                "title": title if sources_slide else slide.title_intent,
                "_footnote": _slide_footnote_text(slide),
                **(
                    {
                        "bibliography_page": 1,
                        "bibliography_entries": source_pages[0][1],
                    }
                    if sources_slide
                    else {}
                ),
            }
        )
        if len(source_pages) > 1:
            # Extra bibliography pages stay next to their plan slide. The
            # repeated exemplar keeps native editability and template style.
            for page_number, (page_xml, page_count) in enumerate(source_pages[1:], 2):
                page_title = (
                    "Источники — продолжение"
                    if (plan.language or "").startswith("ru")
                    else f"{title} — continued"
                )
                plan_specs.append((choice.slide_part, page_xml, [], [], set()))
                plan_reports.append(
                    {
                        "index": slide.index,
                        "id": slide.id,
                        "purpose": slide.purpose.value,
                        "exemplar": choice.to_dict(),
                        "text": {},
                        "dropped_units": {},
                        "tables": TableFillReport().to_dict(),
                        "diagrams": DiagramFillReport().to_dict(),
                        "bibliography_page": page_number,
                        "bibliography_entries": page_count,
                        "title": page_title,
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

    out_pkg = build_multi_slide_deck(pkg, [(src, xml) for src, xml, *_ in clone_specs])

    # Chart/image fills: rewrite chart caches and media-part bytes each
    # output slide references (already cloned into out_pkg).
    used_chart_parts: set[str] = set()
    used_image_parts: set[str] = set()
    out_hints: dict[str, dict[str, dict]] = {}
    # ADR-033 §6: стиль графика-донора шаблона — для графиков любого типа
    donor = donor_style(pkg.parts)
    for i, report in enumerate(slide_reports, start=1):
        out_part = f"ppt/slides/slide{i}.xml"
        src_part = (report.get("exemplar") or {}).get("slide_part") or ""
        if src_part in hint_cache:
            out_hints[out_part] = hint_cache[src_part]
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
            donor=donor,
        )
        report["charts"] = chart_report.to_dict()
        if chart_report.units_dropped:
            logger.warning(
                "slide %d: %d chart unit(s) dropped (charts_found=%d, exemplar=%s, hosts=%s)",
                i,
                chart_report.units_dropped,
                chart_report.charts_found,
                (report.get("exemplar") or {}).get("slide_part"),
                [
                    h[1:5]
                    for h in _empty_body_hosts(
                        etree.fromstring(out_pkg.parts[out_part]),
                        (int(prs.slide_width), int(prs.slide_height)),
                    )
                ],
            )
            dropped = report.setdefault("dropped_units", {})
            dropped["chart"] = dropped.get("chart", 0) + chart_report.units_dropped
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
            slide_size=(sw, sh),
        )
        report["images"] = image_report.to_dict()
        if image_report.units_dropped:
            dropped = report.setdefault("dropped_units", {})
            dropped["image"] = dropped.get("image", 0) + image_report.units_dropped

        footnote_text = report.pop("_footnote", None)
        if not report.get("protected"):
            out_scale = slide_font_scale(
                out_pkg, out_part, observed=tpl_observed_scale, dna_scale=dna_scale
            )
            if footnote_text:
                out_pkg.parts[out_part] = add_slide_footnote(
                    out_pkg.parts[out_part],
                    footnote_text,
                    sw,
                    sh,
                    font_scale=out_scale,
                )
                src_xml = pkg.parts.get(src_part, out_pkg.parts[out_part])
                out_pkg.parts[out_part] = align_composed_text_ink(
                    out_pkg.parts, out_part, out_pkg.parts[out_part], src_xml
                )
            out_pkg.parts[out_part], _ = balance_slide_occupancy(
                out_pkg.parts[out_part],
                sw,
                sh,
                out_hints.get(out_part),
                font_scale=out_scale,
                purpose=str(report.get("purpose") or ""),
            )
        snap_deck_font_scale(
            out_pkg,
            protected_indices=set(protected),
            hints_by_part=out_hints,
            observed_scale=tpl_observed_scale,
            only_parts=[out_part],
        )

        if on_slide_compiled is not None:
            # Snapshot the actual filled slide with its relationship closure.
            # The final package is independent and remains fully editable.
            single = build_multi_slide_deck(out_pkg, [(out_part, out_pkg.parts[out_part])])
            clear_layout_prompts(single, {0} if report.get("protected") else set())
            on_slide_compiled(i - 1, report["id"], single)

    prompts_removed = clear_layout_prompts(out_pkg, set(protected))
    # Метаданные шаблона (dc:title/dc:creator исходного файла) не должны
    # протекать в собранную колоду: пакет описывает тему новой колоды.
    from deckdna.planning.story_director import clean_purpose_text

    clean_purpose = clean_purpose_text(plan.brief.purpose)
    clean_obj = clean_purpose_text(plan.objective)
    first_title = plan.slides[0].title_intent if plan.slides else None
    out_pkg.set_core_properties(
        title=clean_purpose or clean_obj or first_title or plan.id,
        subject=clean_obj or clean_purpose or first_title,
        creator="DeckDNA",
    )
    out_pkg.save(out_path)

    from deckdna.planning.locked_copy import validate_locked_deck

    validate_locked_deck(plan, Path(out_path), slide_reports)

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
            f"table data truncated to fit exemplar grid: {cols_dropped} cell value(s) dropped"
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
        "layout_prompts_removed": prompts_removed,
    }
