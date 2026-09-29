"""Exemplar slide selection (exemplar-first strategy, see docs/ARCHITECTURE.md).

Picks the slide to clone: a *content-like* slide — title plus real text
blocks — on the template's most-used layout, preferring richer scenes.
The run profile gates out decorative slides (per-letter layouts, KPI
micro-labels, caption collages) that look dense but carry no substitutable
content. Layout usage mirrors template/autopsy.py but is computed per
slide from the OPC layer, which owns the rels graph.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from lxml import etree

from deckdna.contracts.variant_spec import Strategy
from deckdna.errors import DeckDNAError
from deckdna.pptx.composing.text_replace import (
    _body_is_decorative,
    _is_offslide_body,
    _is_title_run,
    count_content_slots,
)
from deckdna.pptx.opc.package import OpcPackage

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

SLIDE_PART_RE = re.compile(r"^ppt/slides/slide\d+\.xml$")
_SHAPE_TAGS = ("sp", "pic", "grpSp", "graphicFrame", "cxnSp")

# Content-likeness gate (run profile of the whole slide). A slide whose
# text is mostly 1-3 char fragments is decorative typography, not content
# a compiler can substitute; a slide with almost no long runs has nothing
# to pour a brief into either.
_TINY_RUN_MAX = 3
_LONG_RUN_MIN = 15
_MIN_LONG_RUNS = 3
_MAX_TINY_RATIO = 0.30
_MIN_MEAN_RUN_LEN = 8
# Icon/stat-badge grids (e.g. ">50*" repeated across a dozen small shapes)
# pass every check above: each shape holds exactly one run, one run alone
# never trips tiny_ratio, and a 4+ char label like ">50*" isn't even
# "tiny" by _TINY_RUN_MAX. What gives them away is repetition ACROSS
# shapes -- no single-shape check can see that. Found live: a slide with
# 17 shapes all reading literally ">50*" got selected as a content
# exemplar; the 3-4 real content shapes it also had got single fragmented
# words (badge-sized boxes can't hold a sentence), the 17 badges kept the
# template's own stock text untouched (correctly judged decorative by
# text_replace.py's per-shape gate, so skipped rather than cleared).
#
# Naive "flag any repeated short run" over-fires on a real, common
# pattern: a genuine N-card content grid whose template author never
# diversified the demo text, so the SAME title+body repeats across every
# card (e.g. "Безопасность" + a full sentence, both repeated 8x, found
# live in the organizer's own vk_tech_template.pptx slide16 -- a
# perfectly good 8-card exemplar). The real tell isn't repetition alone,
# it's repetition WITHOUT a substantial paired body: a badge grid repeats
# a short label many times against almost no long_runs (ratio sky-high);
# a real card grid repeats a short title exactly as many times as its
# paired long body (ratio ~1). Threshold picked to flag the badge case
# (18 vs long_runs=1 -> ratio 18) and clear the card case (8 vs 8 -> 1).
_MIN_BADGE_REPEATS = 5
_MAX_BADGE_TO_LONG_RATIO = 3


@dataclass(frozen=True)
class ExemplarChoice:
    slide_part: str
    layout_part: str
    shape_count: int
    layout_slide_count: int
    long_runs: int = 0
    # Authoritative capacity: exactly how many content slots
    # replace_text_runs() will actually fill on this slide (see
    # count_content_slots) -- not an approximation via long_runs (any
    # non-empty run >=15 chars anywhere on the slide, regardless of how
    # many distinct txBody's it's spread across). Measured live on
    # vk_tech_template.pptx: one card-grid slide's long_runs=6 while its
    # real fillable capacity is 37 -- long_runs isn't a rough estimate
    # of capacity, it can be off by multiples. Used for capacity-aware
    # exemplar reassignment (unit_counts= on select_exemplar_slides).
    content_slots: int = 0

    def to_dict(self) -> dict:
        return {
            "slide_part": self.slide_part,
            "layout_part": self.layout_part,
            "shape_count": self.shape_count,
            "layout_slide_count": self.layout_slide_count,
            "long_runs": self.long_runs,
            "content_slots": self.content_slots,
        }


def slide_parts(pkg: OpcPackage) -> list[str]:
    return sorted(n for n in pkg.parts if SLIDE_PART_RE.match(n))


def _presentation_slide_size(pkg: OpcPackage) -> tuple[int, int] | None:
    part = pkg.parts.get("ppt/presentation.xml")
    if part is None:
        return None
    size = etree.fromstring(part).find(f".//{{{P}}}sldSz")
    if size is None:
        return None
    try:
        sw, sh = int(size.get("cx", "0")), int(size.get("cy", "0"))
    except ValueError:
        return None
    return (sw, sh) if sw > 0 and sh > 0 else None


def has_offslide_content_slots(pkg: OpcPackage, slide_part: str) -> bool:
    """Whether a donor offers text-like slots crossing the page edge."""
    size = _presentation_slide_size(pkg)
    if size is None:
        return False
    root = etree.fromstring(pkg.parts[slide_part])
    for body in root.iter(f"{{{P}}}txBody"):
        if _body_is_decorative(body) or not _is_offslide_body(body, size):
            continue
        runs = [t for t in body.iter(f"{{{A}}}t") if (t.text or "").strip()]
        if runs and not _is_title_run(runs[0]):
            return True
    return False


def slide_layout_map(pkg: OpcPackage) -> dict[str, str | None]:
    """slide part name → layout part name (None when a slide has no layout rel)."""
    mapping: dict[str, str | None] = {}
    for slide in slide_parts(pkg):
        layout = None
        for rel, target in pkg.internal_dependencies(slide):
            if rel.type_name == "slideLayout":
                layout = target
                break
        mapping[slide] = layout
    return mapping


def shape_count(pkg: OpcPackage, slide_part: str) -> int:
    """All shapes in the scene tree, including shapes nested in groups."""
    root = etree.fromstring(pkg.parts[slide_part])
    sp_tree = root.find(f".//{{{P}}}cSld/{{{P}}}spTree")
    if sp_tree is None:
        return 0
    return sum(1 for tag in _SHAPE_TAGS for _ in sp_tree.iter(f"{{{P}}}{tag}"))


@dataclass(frozen=True)
class RunProfile:
    """Text-run statistics of one slide — what the exemplar gate uses."""

    runs_total: int
    runs_nonempty: int
    tiny_runs: int  # non-empty runs of <= _TINY_RUN_MAX chars
    long_runs: int  # non-empty runs of >= _LONG_RUN_MIN chars
    mean_nonempty_len: float
    max_repeated_short_run: int = 0  # largest identical-run count, runs < _LONG_RUN_MIN only

    @property
    def tiny_ratio(self) -> float:
        return self.tiny_runs / self.runs_nonempty if self.runs_nonempty else 0.0

    @property
    def is_badge_grid(self) -> bool:
        """Repeated short label (icon badge) with no substantial paired
        body -- see the module-level comment on _MIN_BADGE_REPEATS."""
        return (
            self.max_repeated_short_run >= _MIN_BADGE_REPEATS
            and self.max_repeated_short_run > self.long_runs * _MAX_BADGE_TO_LONG_RATIO
        )

    @property
    def is_content_like(self) -> bool:
        """Slide looks like substitutable content, not decorative text."""
        return (
            self.long_runs >= _MIN_LONG_RUNS
            and self.tiny_ratio < _MAX_TINY_RATIO
            and self.mean_nonempty_len >= _MIN_MEAN_RUN_LEN
            and not self.is_badge_grid
        )


def run_profile(pkg: OpcPackage, slide_part: str) -> RunProfile:
    root = etree.fromstring(pkg.parts[slide_part])
    texts = [(t.text or "").strip() for t in root.findall(f".//{{{A}}}t")]
    nonempty = [t for t in texts if t]
    short_repeats = Counter(t for t in nonempty if len(t) < _LONG_RUN_MIN)
    return RunProfile(
        runs_total=len(texts),
        runs_nonempty=len(nonempty),
        tiny_runs=sum(1 for t in nonempty if len(t) <= _TINY_RUN_MAX),
        long_runs=sum(1 for t in nonempty if len(t) >= _LONG_RUN_MIN),
        mean_nonempty_len=(
            sum(len(t) for t in nonempty) / len(nonempty) if nonempty else 0.0
        ),
        max_repeated_short_run=max(short_repeats.values()) if short_repeats else 0,
    )


def visual_richness(pkg: OpcPackage, slide_part: str) -> int:
    """Count of visually-load-bearing shapes (pictures, graphic frames —
    charts/tables — and groups) in the slide scene. Plain ``sp`` text
    shapes don't count: a slide is *visual* when it carries non-text
    content, not when it merely wraps prose."""
    root = etree.fromstring(pkg.parts[slide_part])
    sp_tree = root.find(f".//{{{P}}}cSld/{{{P}}}spTree")
    if sp_tree is None:
        return 0
    return sum(
        1 for tag in ("pic", "graphicFrame", "grpSp") for _ in sp_tree.iter(f"{{{P}}}{tag}")
    )


class LayoutArchetype(Enum):
    """Discrete visual-layout label for an exemplar candidate.

    Why this exists: exemplar rerank (``rerank.py``) asks a weak (~27-30B)
    model to match a plan slide's ``purpose``/``key_message`` against a
    candidate's raw geometric signals (``content_slots``, ``long_runs``,
    ``visual_richness``, ...) — multi-hop numeric reasoning a small model
    is unreliable at (the live-quality finding of 27.09 that motivated
    this). A small,
    named enum a weak model can pattern-match against a slide's own
    purpose is far more robust than asking it to weigh raw numbers.

    Deliberately conservative — only archetypes reliably distinguishable
    from what the compiler already tracks (capabilities, run profile,
    top-level shape geometry) are named; anything that doesn't cleanly
    match one is ``other``, never a forced guess. Real templates have
    irregular, asymmetric layouts (verified against
    ``vk_tech_template.pptx`` while designing this) that no clean grid/list
    label honestly fits — ``other`` is that honest fallback, not a bug.
    """

    chart_data = "chart_data"
    table_data = "table_data"
    icon_badge_grid = "icon_badge_grid"
    image_dominant = "image_dominant"
    single_block = "single_block"
    two_column = "two_column"
    row_grid = "row_grid"
    grid = "grid"
    stacked_list = "stacked_list"
    other = "other"


_TITLE_PH_TYPES = frozenset({"title", "ctrTitle"})
# Row/column clustering tolerance for shape-center positions: two centers
# within this many EMU of each other are considered the same row/column.
# 0.3in — tight enough to separate genuinely distinct columns/rows in the
# organizer fixtures, loose enough to absorb sub-pixel/rounding drift
# between visually-aligned shapes.
_CLUSTER_TOL_EMU = 274320


def _shape_xfrm(shape_el: etree._Element) -> etree._Element | None:
    """The shape's OWN transform element — direct child of the right
    parent per shape type, never a descendant search (a ``grpSp``'s
    children have their own ``spPr/xfrm`` too; a blind ``.//`` search
    would find one of those instead of the group's own frame)."""
    tag = etree.QName(shape_el).localname
    if tag == "grpSp":
        grp_pr = shape_el.find(f"{{{P}}}grpSpPr")
        return grp_pr.find(f"{{{A}}}xfrm") if grp_pr is not None else None
    if tag == "graphicFrame":
        return shape_el.find(f"{{{P}}}xfrm")
    sp_pr = shape_el.find(f"{{{P}}}spPr")
    return sp_pr.find(f"{{{A}}}xfrm") if sp_pr is not None else None


def _content_boxes(root: etree._Element) -> list[tuple[int, int, int, int]]:
    """(x, y, cx, cy) EMU boxes of top-level content-bearing shapes.

    Excludes: the title placeholder (``p:ph[@type=title|ctrTitle]`` —
    reliable OOXML signal, not a geometric guess; a title banner sitting
    far above the body would otherwise corrupt row clustering), and
    ``sp`` shapes whose text is a short decorative label/number (same
    ``_TINY_RUN_MAX`` threshold as the content-likeness gate) — those
    aren't a content block on their own. ``cxnSp`` (connectors/lines)
    never count either."""
    sp_tree = root.find(f".//{{{P}}}cSld/{{{P}}}spTree")
    if sp_tree is None:
        return []
    boxes: list[tuple[int, int, int, int]] = []
    for child in sp_tree:
        tag = etree.QName(child).localname
        if tag not in ("sp", "pic", "graphicFrame", "grpSp"):
            continue
        ph = child.find(f".//{{{P}}}ph")
        if ph is not None and ph.get("type") in _TITLE_PH_TYPES:
            continue
        if tag == "sp":
            text = "".join(t.text or "" for t in child.findall(f".//{{{A}}}t")).strip()
            if len(text) <= _TINY_RUN_MAX:
                continue
        xfrm = _shape_xfrm(child)
        if xfrm is None:
            continue
        off, ext = xfrm.find(f"{{{A}}}off"), xfrm.find(f"{{{A}}}ext")
        if off is None or ext is None:
            continue
        try:
            boxes.append(
                (
                    int(off.get("x")),
                    int(off.get("y")),
                    int(ext.get("cx")),
                    int(ext.get("cy")),
                )
            )
        except (TypeError, ValueError):
            continue
    return boxes


def _cluster_count(values: list[float], tol: int = _CLUSTER_TOL_EMU) -> int:
    """Distinct 1-D position clusters — consecutive sorted values within
    ``tol`` of each other merge into the same row/column."""
    if not values:
        return 0
    ordered = sorted(values)
    clusters = 1
    for prev, cur in zip(ordered, ordered[1:]):  # noqa: B905 — deliberately unequal (pairwise)
        if cur - prev > tol:
            clusters += 1
    return clusters


def _arrangement(boxes: list[tuple[int, int, int, int]]) -> tuple[int, int]:
    """(rows, cols) — distinct row/column clusters among box centers."""
    rows = _cluster_count([y + cy / 2 for _, y, _, cy in boxes])
    cols = _cluster_count([x + cx / 2 for x, _, cx, _ in boxes])
    return rows, cols


def layout_archetype(
    pkg: OpcPackage, slide_part: str, profile: RunProfile | None = None
) -> LayoutArchetype:
    """Classify a candidate exemplar's visual layout (see ``LayoutArchetype``).

    Mostly deterministic — capability/profile/geometry signals the compiler
    already computes for other purposes. No LLM call: the point is to hand
    the rerank model a label it can match against a slide's ``purpose``
    directly, not to make it infer the label itself."""
    profile = profile or run_profile(pkg, slide_part)
    caps = slide_capabilities(pkg, slide_part)
    if "chart" in caps:
        return LayoutArchetype.chart_data
    if "table" in caps:
        return LayoutArchetype.table_data
    if profile.is_badge_grid:
        return LayoutArchetype.icon_badge_grid
    if "image" in caps and profile.long_runs <= 1:
        return LayoutArchetype.image_dominant
    root = etree.fromstring(pkg.parts[slide_part])
    boxes = _content_boxes(root)
    if len(boxes) <= 1:
        return LayoutArchetype.single_block
    rows, cols = _arrangement(boxes)
    if cols == 2 and rows == 1:
        return LayoutArchetype.two_column
    if cols >= 3 and rows == 1:
        return LayoutArchetype.row_grid
    if cols >= 2 and rows >= 2:
        return LayoutArchetype.grid
    if cols == 1 and rows >= 2:
        return LayoutArchetype.stacked_list
    return LayoutArchetype.other


def _is_label_diagram(profile: RunProfile) -> bool:
    """Много коротких подписей: узлы схемы/сетка бейджей, не место для прозы."""
    return profile.runs_nonempty >= 12 and profile.mean_nonempty_len < 20


def _ranked_candidate_pool(
    pkg: OpcPackage,
    strategy: Strategy = Strategy.balanced,
) -> tuple[list[str], str | None, int, dict[str, RunProfile]]:
    """Slides of the dominant layout ordered content-first.

    Returns (ranked candidate part names, dominant layout part, number of
    source slides on that layout, run profiles). Content-like slides come
    first ordered by (content slots, shape count); if none pass the gate
    the whole candidate set is ranked by shape count so a degenerate
    template still yields exemplars.

    Ranking uses ``count_content_slots`` (real fillable capacity), not
    ``RunProfile.long_runs`` — measured live, one exemplar's long_runs=6
    while its actual capacity was 37 (dense card grid: many short runs
    spread across many txBody's, each individually below the "long run"
    length threshold). ``long_runs`` stays the *gate* deciding whether a
    slide counts as content-like at all (a typography signal — real prose
    vs. scattered KPI labels — orthogonal to how many slots it has); only
    the ordering among already-gated slides changes.

    ``strategy=visual`` reorders the same candidate pool by
    :func:`visual_richness` first — visual-heavy exemplars surface ahead
    of text-heavy ones. ``faithful``/``balanced``/``custom`` keep the
    original content-first order.
    """
    layouts = slide_layout_map(pkg)
    if not layouts:
        raise DeckDNAError(
            code="invalid_input",
            message="template has no slides to clone",
            stage="cloning.select",
        )
    usage = Counter(lay for lay in layouts.values() if lay is not None)
    if usage:
        dominant_layout, layout_slide_count = usage.most_common(1)[0]
        candidates = [s for s, lay in layouts.items() if lay == dominant_layout]
    else:  # degenerate template without layout rels — fall back to all slides
        dominant_layout, layout_slide_count = None, len(layouts)
        candidates = list(layouts)

    profiles = {s: run_profile(pkg, s) for s in candidates}
    content_like = [s for s in candidates if profiles[s].is_content_like]
    # Схема из десятков коротких подписей (узлы диаграммы, ~11 симв. в
    # среднем) формально «вмещает» 37 слотов, но под прозу даёт мусор из
    # кружков с обрывками фраз — такие эталоны уходят из пула, если есть
    # хоть одна альтернатива.
    prose = [s for s in content_like if not _is_label_diagram(profiles[s])]
    if prose:
        content_like = prose
    safe_content = [s for s in content_like if not has_offslide_content_slots(pkg, s)]
    if safe_content:
        content_like = safe_content
    visual = strategy == Strategy.visual
    if content_like:
        slide_size = _presentation_slide_size(pkg)
        slots = {s: count_content_slots(pkg.parts[s], slide_size) for s in content_like}
        ranked = sorted(
            content_like,
            key=lambda s: (
                (-visual_richness(pkg, s), -slots[s], s)
                if visual
                else (-slots[s], -shape_count(pkg, s), s)
            ),
        )
    else:
        # No slide passes is_content_like -- last resort, rank by raw
        # shape_count. Still deprioritize badge grids even here: a slide
        # padded with 20 decorative icon shapes isn't a better fallback
        # than one with fewer but genuinely substitutable shapes, even
        # though raw shape_count alone would say otherwise.
        ranked = sorted(
            candidates,
            key=lambda s: (
                (-visual_richness(pkg, s), -shape_count(pkg, s), s)
                if visual
                else (profiles[s].is_badge_grid, -shape_count(pkg, s), s)
            ),
        )
    return ranked, dominant_layout, layout_slide_count, profiles


def select_exemplar_slide(pkg: OpcPackage) -> ExemplarChoice:
    """Most text-rich slide on the most-used layout."""
    return select_exemplar_slides(pkg, count=1)[0]


def slide_capabilities(pkg: OpcPackage, slide_part: str) -> frozenset[str]:
    """Structured-content payloads a slide physically carries.

    ``"chart"`` — the slide's rels reference at least one `c:chart`
    part (same detection as chart_fill.py); ``"table"`` — the slide
    XML contains a native `a:tbl` grid; ``"image"`` — at least one
    `a:blip/@r:embed` on an internal image rel (same detection as
    image_fill.py's blip walk — an image rel never embedded does not
    count). Used for requirement-aware exemplar selection: a plan
    slide asking for a chart or picture needs an exemplar that has
    one — neither has a from-scratch fallback.
    """
    caps: set[str] = set()
    image_rids: set[str] = set()
    for rel, _target in pkg.internal_dependencies(slide_part):
        if rel.type_name == "chart":
            caps.add("chart")
        elif rel.type_name == "image":
            image_rids.add(rel.id)
    root = etree.fromstring(pkg.parts[slide_part])
    if root.find(f".//{{{A}}}tbl") is not None:
        caps.add("table")
    if image_rids and any(
        blip.get(f"{{{R_NS}}}embed") in image_rids
        for blip in root.iter(f"{{{A}}}blip")
    ):
        caps.add("image")
    return frozenset(caps)


def is_team_roster_exemplar(pkg: OpcPackage, slide_part: str) -> bool:
    """Recognize a repeated person-card donor without requiring an LLM.

    Applies to arbitrary templates: the signal is repeated generic person
    fields, not a template name or slide number. Semantic DNA may exclude
    additional entity-specific layouts when a model is available.
    """
    root = etree.fromstring(pkg.parts[slide_part])
    labels = [(t.text or "").casefold().strip() for t in root.iter(f"{{{A}}}t")]
    name_fields = sum(
        bool(re.search(r"\b(имя\s+фамилия|full\s+name|person\s+name)\b", t))
        for t in labels
    )
    role_fields = sum(
        bool(re.search(r"\b(должность|position|job\s+title)\b", t))
        for t in labels
    )
    return name_fields >= 3 and role_fields >= 3


def content_horizontal_span(pkg: OpcPackage, slide_part: str) -> tuple[float, float]:
    """Left/right extent of fillable text boxes, normalized to slide width."""
    slide_size = _presentation_slide_size(pkg)
    width = slide_size[0] if slide_size is not None else 0
    if not width:
        return (0.0, 1.0)
    root = etree.fromstring(pkg.parts[slide_part])
    edges: list[tuple[int, int]] = []
    for shape in root.iter(f"{{{P}}}sp"):
        body = shape.find(f"{{{P}}}txBody")
        if body is None or _body_is_decorative(body) or _is_offslide_body(body, slide_size):
            continue
        runs = [t for t in body.iter(f"{{{A}}}t") if (t.text or "").strip()]
        if not runs or _is_title_run(runs[0]):
            continue
        xfrm = shape.find(f"{{{P}}}spPr/{{{A}}}xfrm")
        if xfrm is None:
            continue
        off = xfrm.find(f"{{{A}}}off")
        ext = xfrm.find(f"{{{A}}}ext")
        if off is None or ext is None:
            continue
        try:
            x = int(off.get("x", "0"))
            cx = int(ext.get("cx", "0"))
        except ValueError:
            continue
        edges.append((x, x + cx))
    if not edges:
        return (0.0, 1.0)
    return (min(a for a, _ in edges) / width, max(b for _, b in edges) / width)


def _rebalance_by_capacity(
    picks: list[str],
    need_list: list[frozenset[str]],
    unit_counts: list[int],
    capacity_of: dict[str, int],
) -> list[str]:
    """Reassign already-picked exemplars across slide positions to better
    match each slide's actual content volume — without changing WHICH
    exemplars were picked, only WHO gets which (so pool diversity/
    coverage guarantees from the caller's cycling are untouched).

    Why: the plain cycle in ``select_exemplar_slides`` assigns exemplars
    by pick position only (richest-first, round-robin) — a sparse
    2-unit slide can land on an 8-card exemplar (7 empty cards) while a
    dense 8-unit slide lands on a 2-run exemplar (severe overflow/
    collision) purely by where each happened to fall in the cycle. Both
    failure modes were reported live on generated decks.

    Only positions with no capability ``need`` are eligible to swap — a
    chart/table/image requirement pins a pick to the one exemplar that
    physically carries it; swapping those would silently lose the
    capability match. Greedy largest-need-to-largest-capacity pairing
    (classic discrepancy-minimizing heuristic): sort swappable slides by
    unit_counts descending, sort their currently-assigned exemplars by
    ``capacity_of`` (real fillable slots, see count_content_slots)
    descending, zip. This can only reduce each swappable slide's
    |capacity - unit_count| relative to the identity pairing on average
    — it is not an exact per-slide guarantee (a slide can still end up
    short if no candidate in the whole pool has enough capacity; the
    compiler's own empty-slot clearing is the honest fallback for that,
    unchanged).
    """
    swappable = [i for i, need in enumerate(need_list) if not need]
    if len(swappable) < 2:
        return picks
    by_need_desc = sorted(swappable, key=lambda i: -unit_counts[i])
    available_parts = sorted(
        (picks[i] for i in swappable),
        key=lambda part: -capacity_of.get(part, 0),
    )
    rebalanced = list(picks)
    for slot, part in zip(by_need_desc, available_parts, strict=True):
        rebalanced[slot] = part
    return rebalanced


def select_exemplar_slides(
    pkg: OpcPackage,
    count: int,
    needs: Iterable[Iterable[str]] = (),
    strategy: Strategy = Strategy.balanced,
    unit_counts: Iterable[int] = (),
    text_lengths: Iterable[int] = (),
    purposes: Iterable[str] = (),
    entity_specific_parts: Iterable[str] = (),
) -> list[ExemplarChoice]:
    """*count* exemplar picks, cycled over the ranked content-like pool.

    Different plan slides get different exemplar sources whenever the
    dominant layout offers more than one content-like candidate; the pool
    wraps around once exhausted rather than cloning the same source twice.

    *needs* — per-pick capability requirements (subsets of
    ``{"chart", "table", "image"}``). A pick with needs first searches the ranked
    pool and then the remaining template slides — also content-likeness
    ordered — for a slide physically carrying the capability. When no
    slide satisfies the needs the pick falls back to the regular cycle
    (the content unit is honestly dropped downstream by its filler).

    ``strategy`` controls the pool ordering (see
    ``_ranked_candidate_pool``): ``visual`` prefers visually-rich
    exemplars, the rest keep the text-first order. needs and strategy
    are orthogonal — needs decides *what* the exemplar must carry,
    strategy decides the order candidates are considered in.

    *unit_counts* — optional real count of text units per pick. When
    given, selection searches the whole template for close capacity,
    avoiding sparse content poured into huge grids. ``text_lengths``
    also avoids short-label scenes for long prose. Without unit counts,
    the legacy ranked cycle remains unchanged.
    """
    if count < 1:
        return []
    visual = strategy == Strategy.visual
    ranked, dominant_layout, layout_slide_count, profiles = _ranked_candidate_pool(
        pkg, strategy
    )
    # Found live (28.09): capacity-aware picks (below) routinely choose a
    # slide off the dominant layout -- reporting `dominant_layout` for
    # EVERY pick regardless of which slide_part was actually chosen was a
    # latent bug (pre-dates this function's capacity-aware branch) that
    # only became visible once picks started diversifying away from it.
    layout_of = slide_layout_map(pkg)
    need_list = [frozenset(n) for n in needs]
    need_list.extend([frozenset()] * (count - len(need_list)))
    need_list = need_list[:count]

    caps_cache: dict[str, frozenset[str]] = {}

    def caps_of(part: str) -> frozenset[str]:
        if part not in caps_cache:
            caps_cache[part] = slide_capabilities(pkg, part)
        return caps_cache[part]

    # Widened pool for need-satisfying picks: ranked candidates first,
    # then every other template slide ranked by content-likeness — a
    # chart/table slide may live outside the dominant layout. Ordered by
    # real fillable capacity (count_content_slots), same reasoning as
    # _ranked_candidate_pool above — long_runs undercounts dense grids.
    extras = [s for s in slide_parts(pkg) if s not in set(ranked)]
    slide_size = _presentation_slide_size(pkg)
    extras.sort(
        key=lambda s: (
            has_offslide_content_slots(pkg, s),
            -count_content_slots(pkg.parts[s], slide_size),
            -shape_count(pkg, s),
            s,
        )
    )
    pool = ranked + extras

    need_cursor: dict[frozenset[str], int] = {}

    def pick(i: int) -> str:
        need = need_list[i]
        if need:
            start = need_cursor.get(need, 0)
            matches = [cand for cand in pool if need <= caps_of(cand)]
            safe = [cand for cand in matches if not has_offslide_content_slots(pkg, cand)]
            candidates = safe or matches
            if candidates:
                need_cursor[need] = start + 1
                return candidates[start % len(candidates)]
        return ranked[i % len(ranked)]

    picks = [pick(i) for i in range(count)]

    def long_runs_of(part: str) -> int:
        return (
            profiles[part].long_runs if part in profiles else run_profile(pkg, part).long_runs
        )

    slots_cache: dict[str, int] = {}
    span_cache: dict[str, tuple[float, float]] = {}

    def slots_of(part: str) -> int:
        if part not in slots_cache:
            slots_cache[part] = count_content_slots(pkg.parts[part], slide_size)
        return slots_cache[part]

    def span_of(part: str) -> tuple[float, float]:
        if part not in span_cache:
            span_cache[part] = content_horizontal_span(pkg, part)
        return span_cache[part]

    unit_counts_list = list(unit_counts)
    if unit_counts_list:
        unit_counts_list.extend([0] * (count - len(unit_counts_list)))
        unit_counts_list = unit_counts_list[:count]
        text_lengths_list = list(text_lengths)
        purpose_list = list(purposes)
        semantic_exclusions = set(entity_specific_parts)
        if not text_lengths_list and not purpose_list and not semantic_exclusions:
            picks = _rebalance_by_capacity(
                picks, need_list, unit_counts_list,
                {part: slots_of(part) for part in picks},
            )
            return [
                ExemplarChoice(
                    slide_part=part,
                    layout_part=layout_of.get(part) or dominant_layout or "",
                    shape_count=shape_count(pkg, part),
                    layout_slide_count=layout_slide_count,
                    long_runs=long_runs_of(part),
                    content_slots=slots_of(part),
                )
                for part in picks
            ]
        text_lengths_list.extend([0] * (count - len(text_lengths_list)))
        purpose_list.extend([""] * (count - len(purpose_list)))
        excluded = semantic_exclusions | {
            part for part in pool if is_team_roster_exemplar(pkg, part)
        }
        used: set[str] = set()
        fitted: list[str] = []
        for i, target in enumerate(unit_counts_list):
            need = need_list[i]
            eligible = [
                part for part in pool
                if need <= caps_of(part)
                and (purpose_list[i] == "team" or part not in excluded)
                and (target == 0 or slots_of(part) > 0)
            ]
            if not eligible:
                eligible = [
                    part for part in pool
                    if purpose_list[i] == "team" or part not in excluded
                ]
            if not eligible:
                eligible = pool
            safe_eligible = [
                part for part in eligible if not has_offslide_content_slots(pkg, part)
            ]
            if safe_eligible:
                eligible = safe_eligible
            if "image" not in need:
                no_donor_artwork = [
                    part for part in eligible
                    if "image" not in caps_of(part)
                    and slots_of(part) >= target
                    and slots_of(part) <= max(target + 2, target * 2)
                ]
                if no_donor_artwork:
                    eligible = no_donor_artwork
            if target >= 2:
                broad = [
                    part for part in eligible
                    if span_of(part)[0] <= 0.35 and span_of(part)[1] >= 0.65
                    and slots_of(part) >= target
                    and slots_of(part) <= max(target + 2, target * 2)
                ]
                if broad:
                    eligible = broad
            # Empty cards are a visible defect. Prefer a candidate with
            # enough slots but as little unused capacity as possible;
            # a shortage is worse because content would be dropped.
            def score(
                part: str,
                target: int = target,
                need: frozenset[str] = need,
                text_length: int = text_lengths_list[i],
            ) -> tuple[int, int, int, int]:
                slots = slots_of(part)
                shortage = max(0, target - slots)
                surplus = max(0, slots - target)
                unwanted_image = int("image" in caps_of(part) and "image" not in need)
                profile = profiles.get(part) or run_profile(pkg, part)
                prose_mismatch = int(
                    text_length > 80 and profile.mean_nonempty_len < 20
                )
                # Found live (29.09, OR-007): capacity-fit alone routinely
                # converged visual and balanced onto the IDENTICAL pick --
                # surplus/prose_mismatch rarely tie between candidates, so
                # a richness-only tiebreak placed after them was almost
                # never reached; `visual` needs richness to actually
                # compete with fit quality, not just settle ties fit
                # quality already decided. Weighted into the same cost
                # term surplus/prose_mismatch/unwanted_image share (not a
                # separate tuple position) so a visibly richer candidate a
                # couple of slots off can outrank a plainer exact fit --
                # shortage (dropped content) is untouched, still dominant.
                richness_cost = -2 * visual_richness(pkg, part) if visual else 0
                return (
                    shortage,
                    surplus + 3 * prose_mismatch + (3 if part in used else 0)
                    + 2 * unwanted_image + richness_cost,
                    unwanted_image,
                    pool.index(part),
                )

            chosen = min(eligible, key=score)
            fitted.append(chosen)
            used.add(chosen)
        picks = fitted

    return [
        ExemplarChoice(
            slide_part=part,
            layout_part=layout_of.get(part) or dominant_layout or "",
            shape_count=shape_count(pkg, part),
            layout_slide_count=layout_slide_count,
            long_runs=long_runs_of(part),
            content_slots=slots_of(part),
        )
        for part in picks
    ]
