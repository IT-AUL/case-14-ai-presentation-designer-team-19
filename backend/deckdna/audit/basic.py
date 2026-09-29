"""Render Arena — базовые детерминированные проверки качества .pptx.

Работают напрямую через python-pptx над готовым файлом (без Scene Graph).
Правила (docs/AUDIT.md, стадия «Render Arena»); коды и severity —
по frozen-таксономии официального Приложения 1 ТЗ,
они публичные идентификаторы AuditIssue:

- ``text.overflow`` — текст выходит за границы фигуры (оценка по метрикам
  шрифта, детерминированная, без рендера; error);
- ``integrity.empty_slide`` — пустой слайд: нет ни одной фигуры с текстом
  или картинкой (и другого контентного элемента; error);
- ``image.aspect_ratio`` — картинка растянута/искажена относительно
  исходных пропорций (crop-aware; error);
- ``editability.raster_only`` — одна картинка занимает ≥90% площади
  слайда и нет текстовых фреймов (blocker);
- ``layout.out_of_bounds`` — фигура выходит за границы слайда (bbox в
  нормализованных координатах выходит за [0,1] с допуском; error);
- ``integrity.duplicate_slide`` — слайд почти идентичен другому слайду
  той же колоды по нормализованному текстовому контенту (warning);
- ``integrity.placeholder_text`` — текст-заглушка шаблона (lorem/
  TODO/«Заголовок»/«вставьте текст» и т.п.; error);
- ``density.bullet_count`` — больше 6 буллет-параграфов в одном
  текстовом теле (explicit buChar/buAutoNum/buBlip; warning);
- ``density.bullet_length`` — буллет-параграф длиннее 15 слов (тот же
  маркер буллета; warning);
- ``layout.edge_margin`` — контентная текстовая фигура прижата к краю
  слайда без отступа (<3% по оси; warning).

Каждое правило — отдельно адресуемое: функция принимает слайд и возвращает
AuditIssue'ы по schemas/audit-issue.schema.json.
"""

from __future__ import annotations

import colorsys
import copy
import math
import re
import uuid
import zipfile
from collections.abc import Iterable
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER
from pptx.enum.text import MSO_AUTO_SIZE
from pptx.shapes.picture import Picture

from deckdna.pptx.opc.package import resolve_target

from .catalog import is_repairable
from .config import AuditConfig, default_audit_config
from .issues import AuditIssue

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
C_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
NSMAP = {"a": A, "p": P_NS}

# Frozen rule IDs — официальное Приложение 1 ТЗ, аудит-маппинг.
# Не переименовывать без ADR: эти строки —
# публичные идентификаторы для UI, coverage report и repair mapping.
RULE_TEXT_OVERFLOW = "text.overflow"
RULE_EMPTY_SLIDE = "integrity.empty_slide"
RULE_ASPECT_RATIO = "image.aspect_ratio"
RULE_RASTER_ONLY = "editability.raster_only"
RULE_OUT_OF_BOUNDS = "layout.out_of_bounds"
RULE_DUPLICATE_SLIDE = "integrity.duplicate_slide"
RULE_PLACEHOLDER_TEXT = "integrity.placeholder_text"
RULE_BULLET_COUNT = "density.bullet_count"
RULE_BULLET_LENGTH = "density.bullet_length"
RULE_TABLE_SIZE = "density.table_size"
RULE_FONT_FAMILY = "template.font_family"
RULE_CHART_SERIES = "density.chart_series"
RULE_OCCUPANCY = "density.occupancy"
RULE_FONT_SCALE = "template.font_scale"
RULE_COLOR_PALETTE = "template.color_palette"
RULE_PACKAGE = "integrity.package"
RULE_CHART_METADATA = "chart.metadata"
RULE_ANCHOR_POSITION = "template.anchor_position"
RULE_SLIDE_CLIP = "text.slide_clip"
RULE_LAYOUT_ORIGIN = "template.layout_origin"
RULE_UNINTENDED_OVERLAP = "layout.unintended_overlap"
RULE_EDGE_MARGIN = "layout.edge_margin"
RULE_FONT_FLOOR = "text.font_floor"
RULE_CONTRAST = "accessibility.contrast"

# Все пороги правил — из configs/audit.default.yaml (AuditConfig,
# audit/config.py), а не константы: правки файла реально меняют
# поведение audit_deck. Файл резолвится от пакета, не от CWD.
# Дефолты AuditConfig повторяют отгружаемый yaml — оба источника
# синхронизированы, единственный runtime-источник истины один.
# Маркеры placeholder-текста. Два яруса:
#  - STRONG — вхождение в любом месте текста (lorem ipsum, TODO/FIXME
#    как отдельные слова, «click to add», «insert text»): безусловная
#    заглушка, где бы ни встретилась;
#  - WHOLE — весь текст фигуры целиком является стоковой подписью
#    («Заголовок», «Текст», «Основной текст», «введите текст»,
#    «Заголовок в две строчки» и подобные). Частое слово внутри
#    нормального предложения не флагуется — только целый фрейм.
_PLACEHOLDER_STRONG_RE = re.compile(
    r"(?:lorem|dolor\s+sit\s+amet|\b(?:todo|fixme|xxx|tbd)\b"
    r"|click\s+to\s+(?:add|edit)|insert\s+(?:text|title|your))",
    re.IGNORECASE,
)
_PLACEHOLDER_WHOLE_RE = re.compile(
    r"^(?:"
    r"заголовок(?:\s+(?:в\s+)?[\w\s]*?строч\w*)?"        # «Заголовок», «Заголовок в две строчки»
    r"|заголовок\s+(?:текст|описание)"
    r"|подзаголовок"
    r"|текст(?:\s+текст)*|текст\s+(?:описания|слайда)|основной\s+текст"
    r"|(?:введите|вставьте|добавьте|напишите)\b[\w\s]*"
    r"|название|описание|комментарий|примечание|пункт"
    r"|product(?:\s+name)?|company(?:\s+name)?"
    r"|your\s+(?:text|title)\s+here|slide\s+title|add\s+(?:title|text)|title\s+here"
    r")$",
    re.IGNORECASE,
)
# Порог Appendix «more than six bullets» = configs/audit.default.yaml
# max_bullets_per_slide. Счёт идёт по текстовому телу, а не по слайду:
# карточная сетка из N карточек по 1 буллету — не перегруз читаемости.
_BULLET_TAGS = frozenset(
    {
        f"{{{A}}}buChar",
        f"{{{A}}}buAutoNum",
        f"{{{A}}}buBlip",
    }
)

# Assumed line-height factor when the paragraph sets no explicit spacing.
LINE_HEIGHT_FACTOR = 1.25
# Fallback average glyph width (share of font size) per script, measured on
# DejaVu Sans: cyrillic is ~13% wider than latin at the same point size.
CHAR_WIDTH_FACTOR_LATIN = 0.55
CHAR_WIDTH_FACTOR_CYRILLIC = 0.62
_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")
DEFAULT_FONT_PT = 18.0
EMU_PER_PT = 12700

# Metric font lookup. Golden-тесты откалиброваны под метрики DejaVu Sans
# (в backend/Dockerfile ставится fonts-dejavu + fonts-liberation). Поиск
# переносимый: сначала Pillow резолвит по имени (fontconfig на Linux,
# системные font-директории на macOS), затем явный детерминированный
# скан стандартных директорий обеих ОС.
_FONT_NAMES = (
    "DejaVuSans.ttf",
    "DejaVu Sans",
    "LiberationSans-Regular.ttf",
    "Liberation Sans",
)
_FONT_DIRS = (
    "/usr/share/fonts",  # Linux / Docker-образ
    "/usr/local/share/fonts",
    "/System/Library/Fonts",  # macOS system
    "/Library/Fonts",  # macOS local
    str(Path.home() / "Library" / "Fonts"),  # macOS user (brew fonts)
)
_PREFERRED_TTF = ("DejaVuSans.ttf", "LiberationSans-Regular.ttf")

_font_cache: dict[float, object] = {}
_font_loader: object = ...  # Ellipsis = ещё не резолвлен; None = не найден


def _resolve_font_loader():
    """callable(size_px) -> PIL ImageFont | None, резолвится один раз."""
    from PIL import ImageFont

    for name in _FONT_NAMES:
        try:
            ImageFont.truetype(name, 12)
        except OSError:
            continue
        return lambda px: ImageFont.truetype(name, px)
    # explicit scan: сначала предпочтительные файлы, затем первый .ttf
    # по алфавиту — порядок детерминирован
    paths = [
        p
        for d in _FONT_DIRS
        if (d_path := Path(d)).is_dir()
        for p in sorted(d_path.rglob("*.ttf"))
    ]
    for wanted in _PREFERRED_TTF:
        for p in paths:
            if p.name == wanted:
                return lambda px: ImageFont.truetype(str(p), px)
    if paths:
        font_path = str(paths[0])
        return lambda px: ImageFont.truetype(font_path, px)
    return None


def _measure_font(size_pt: float):
    """DejaVu/Liberation metrics via PIL; None if no font is installed."""
    global _font_loader
    if _font_loader is ...:
        _font_loader = _resolve_font_loader()
    if _font_loader is None:
        return None
    key = round(size_pt, 1)
    if key not in _font_cache:
        _font_cache[key] = _font_loader(int(round(key * 4)))
    return _font_cache[key]


def _text_width_emu(text: str, size_pt: float) -> int:
    """Rendered width estimate for a single line of text, in EMU."""
    font = _measure_font(size_pt)
    if font is not None:
        # font is loaded at 4x size for sub-point precision
        return int(font.getlength(text) / 4 * EMU_PER_PT)
    factor = CHAR_WIDTH_FACTOR_CYRILLIC if _CYRILLIC_RE.search(text) else CHAR_WIDTH_FACTOR_LATIN
    return int(len(text) * size_pt * factor * EMU_PER_PT)


def _iter_shapes(shapes) -> Iterable:
    for shape in shapes:
        yield shape
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _iter_shapes(shape.shapes)


def _is_picture(shape) -> bool:
    """Растровая картинка на слайде: обычный p:pic И картинка, вставленная
    через picture placeholder (insert_picture → PlaceholderPicture,
    у него shape_type остаётся PLACEHOLDER, а не PICTURE)."""
    return isinstance(shape, Picture)


# Found live (28.09): a slide the plan sent zero real content units for
# (e.g. an entirely empty source document) still got a synthetic "DeckDNA
# heading"/"DeckDNA heading backdrop" shape from
# ``pptx/composing/sources_layout.py::add_missing_title`` -- that
# fallback runs whenever a donor slide has no title placeholder,
# independent of whether the slide has any real content. check_empty_slide
# below must not count these two purely-structural shapes as content, or
# a genuinely empty slide reads as non-empty. The other _slide_census
# caller (occupancy/density) keeps counting them -- they ARE real ink on
# the slide for that purpose, just not evidence of substantive content.
_SYNTHETIC_DECORATIVE_SHAPE_NAMES = frozenset(
    {"DeckDNA heading", "DeckDNA heading backdrop"}
)


def _slide_census(
    slide, *, exclude_names: frozenset[str] = frozenset()
) -> tuple[int, int, int]:
    """(text_chars, pictures, content_shapes) — единый обход для правил,
    опирающихся на наличие текстового/контентного наполнения слайда."""
    text_chars = 0
    pictures = 0
    content_shapes = 0
    for shape in _iter_shapes(slide.shapes):
        if shape.name in exclude_names:
            continue
        if _is_picture(shape):
            pictures += 1
        if shape.has_text_frame:
            text_chars += len(re.sub(r"\s", "", shape.text_frame.text))
            if shape.text_frame.text.strip():
                content_shapes += 1
        if getattr(shape, "has_table", False) or getattr(shape, "has_chart", False):
            content_shapes += 1
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    for cell in row.cells:
                        text_chars += len(re.sub(r"\s", "", cell.text))
    return text_chars, pictures, content_shapes


def _shape_bbox(shape, ctx) -> dict[str, float] | None:
    """bbox фигуры в нормализованных координатах [0,1] от размера слайда —
    формат, который ждёт UI overlay (не сырые EMU)."""
    try:
        if shape.left is None or shape.top is None:
            return None
        sw, sh = float(ctx.slide_width), float(ctx.slide_height)
        if sw <= 0 or sh <= 0:
            return None
        return {
            "x": float(shape.left) / sw,
            "y": float(shape.top) / sh,
            "w": float(shape.width or 0) / sw,
            "h": float(shape.height or 0) / sh,
        }
    except AttributeError:
        return None


def _issue_id(run_id: str, rule: str, slide_index: int, seq: int) -> str:
    return f"{run_id}:{rule}:s{slide_index}:{seq}"


def _defrpr_sz(container_el, path: str) -> float | None:
    """sz в сотых долях пункта из defRPr по xpath-пути, либо None."""
    el = container_el.find(path, NSMAP) if container_el is not None else None
    if el is not None and el.get("sz"):
        return int(el.get("sz")) / 100
    return None


def _lst_style_sz(tx_body_el, lvl: int) -> float | None:
    return _defrpr_sz(tx_body_el, f"a:lstStyle/a:lvl{lvl}pPr/a:defRPr")


def _placeholder_inherited_sz(shape, slide, lvl: int) -> float | None:
    """Размер из цепочки layout placeholder → master txStyles."""
    try:
        ph_idx = shape.placeholder_format.idx
    except (AttributeError, ValueError):
        return None
    try:
        layout = slide.slide_layout
    except AttributeError:
        return None
    for ph in layout.placeholders:
        if ph.placeholder_format.idx == ph_idx:
            tx = ph.element.find(f".//{{{P_NS}}}txBody")
            sz = _lst_style_sz(tx, lvl)
            if sz is not None:
                return sz
            break
    # master txStyles: title / body / other по типу placeholder'а
    try:
        master_el = layout.slide_master.element
        ph_type = shape.placeholder_format.type
    except AttributeError:
        return None
    if ph_type in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE):
        style = "p:titleStyle"
    elif ph_type == PP_PLACEHOLDER.BODY:
        style = "p:bodyStyle"
    else:
        style = "p:otherStyle"
    return _defrpr_sz(master_el, f"p:txStyles/{style}/a:lvl{lvl}pPr/a:defRPr")


def _effective_font_size_pt(shape, paragraph, slide, ctx) -> float:
    """Эффективный размер кегля с учётом наследования OOXML.

    Порядок: явный a:rPr@sz у runs → paragraph defRPr → endParaRPr →
    lstStyle фигуры → placeholder-цепочка (layout → master txStyles) →
    presentation.xml defaultTextStyle lvl1 → 18pt.
    """
    sizes = [r.font.size.pt for r in paragraph.runs if r.font.size is not None]
    if paragraph.font.size is not None:
        sizes.append(paragraph.font.size.pt)
    if sizes:
        return max(sizes)
    lvl = min(int(paragraph.level or 0) + 1, 9)
    tx_body = shape.text_frame._txBody
    sz = _lst_style_sz(tx_body, lvl)
    if sz is not None:
        return sz
    try:
        if shape.is_placeholder:
            sz = _placeholder_inherited_sz(shape, slide, lvl)
            if sz is not None:
                return sz
    except AttributeError:
        pass
    return ctx.default_font_pt


def _para_line_height_emu(paragraph, size_pt: float) -> float:
    spacing = paragraph.line_spacing
    if spacing is None:
        return size_pt * LINE_HEIGHT_FACTOR * EMU_PER_PT
    if isinstance(spacing, float):  # multiple of single line height
        return size_pt * LINE_HEIGHT_FACTOR * spacing * EMU_PER_PT
    return float(spacing)  # Length — absolute EMU


def _para_space(paragraph, attr: str) -> float:
    value = getattr(paragraph, attr)
    return float(value) if value is not None else 0.0


def _wrap_metrics(text: str, size_pt: float, usable_width: int) -> tuple[int, float]:
    """Жадный перенос по словам: (число строк, ширина самого длинного слова).

    Word wrap в PowerPoint рвёт строку только по границам слов — остаток
    строки, в который не влезает следующее слово, теряется. Слово шире
    строки получает собственную строку и визуально вылезает горизонтально.
    """
    words = text.split()
    if not words:
        return 1, 0.0
    space_w = _text_width_emu(" ", size_pt)
    lines = 1
    cur = 0.0
    widest = 0.0
    for word in words:
        word_w = _text_width_emu(word, size_pt)
        widest = max(widest, word_w)
        if cur == 0.0:
            cur = word_w
        elif cur + space_w + word_w <= usable_width:
            cur += space_w + word_w
        else:
            lines += 1
            cur = word_w
    return lines, widest


def _estimate_text_height_emu(
    shape, text_frame, slide, ctx, usable_width: int
) -> tuple[float, float]:
    """(требуемая высота текста, ширина самого длинного слова) в EMU."""
    total = 0.0
    widest_word = 0.0
    for paragraph in text_frame.paragraphs:
        size_pt = _effective_font_size_pt(shape, paragraph, slide, ctx)
        line_height = _para_line_height_emu(paragraph, size_pt)
        hard_lines = re.split(r"[\v\n]", paragraph.text) or [""]
        lines = 0
        for line in hard_lines:
            n_lines, widest = _wrap_metrics(line, size_pt, usable_width)
            lines += n_lines
            widest_word = max(widest_word, widest)
        total += lines * line_height
        total += _para_space(paragraph, "space_before")
        total += _para_space(paragraph, "space_after")
    return total, widest_word


def _max_line_width_emu(shape, text_frame, slide, ctx) -> tuple[float, float]:
    """Widest hard line and its font size, for wrap='none' frames."""
    widest, size_at = 0.0, DEFAULT_FONT_PT
    for paragraph in text_frame.paragraphs:
        size_pt = _effective_font_size_pt(shape, paragraph, slide, ctx)
        for line in re.split(r"[\v\n]", paragraph.text):
            width = _text_width_emu(line, size_pt)
            if width > widest:
                widest, size_at = width, size_pt
    return widest, size_at


def _measure_text_overflow(shape, slide, ctx: _Ctx, bbox: dict | None = None) -> dict | None:
    """Pure geometry check behind ``text.overflow`` — no issue built.

    Returns ``None`` when the shape's own text fits its own bounds,
    else a dict with everything ``check_text_overflow`` needs to build
    the issue message. Factored out so ``check_unintended_overlap`` can
    ask the same question ("does THIS shape's text actually overflow
    its own box") without duplicating the estimation math or building
    throwaway ``AuditIssue``s (``ctx.issue`` bumps a shared sequence
    counter -- calling it for a value we'd discard would shift every
    later issue's id on the slide).

    ``bbox`` — optional, the shape's already-computed slide-space bbox
    (from ``_iter_bbox``, which correctly composes a group's
    chOff/chExt child-space transform through nested ``grpSp``). When
    given, box width/height (and text-frame margins, which are insets
    declared in the SAME child-space unit as the shape's own
    width/height) are rescaled by the same factor before comparing
    against font-metric-based text measurements. Without it (the
    default), raw ``shape.width``/``shape.height`` are used verbatim —
    correct for top-level shapes (scale 1:1), WRONG for a shape nested
    in a scaled group (measured live: up to ~2.7x on
    vk_tech_template.pptx). Font size itself is never rescaled by a
    group transform (verified empirically: a `sz` run inside a 0.5x
    chExt/ext group renders pixel-identical to the same `sz` outside
    any group) — only shape geometry is, so ``_estimate_text_height_emu``/
    ``_max_line_width_emu`` (both font-metric-driven) are untouched.
    """
    if not shape.has_text_frame:
        return None
    tf = shape.text_frame
    if not tf.text.strip():
        return None
    # TEXT_TO_FIT_SHAPE shrinks text into the frame; SHAPE_TO_FIT_TEXT
    # grows the frame — neither lets glyphs leave the shape bounds.
    if tf.auto_size in (MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE, MSO_AUTO_SIZE.SHAPE_TO_FIT_TEXT):
        return None
    if not shape.width or not shape.height:
        return None
    scale_x = scale_y = 1.0
    real_w, real_h = float(shape.width), float(shape.height)
    if bbox is not None and bbox["w"] > 0 and bbox["h"] > 0:
        sw, sh = float(ctx.slide_width), float(ctx.slide_height)
        real_w = bbox["w"] * sw
        real_h = bbox["h"] * sh
        scale_x = real_w / float(shape.width)
        scale_y = real_h / float(shape.height)
    usable_w = int(real_w - (int(tf.margin_left) + int(tf.margin_right)) * scale_x)
    usable_h = int(real_h - (int(tf.margin_top) + int(tf.margin_bottom)) * scale_y)
    if usable_w <= 0 or usable_h <= 0:
        return None

    if tf.word_wrap is False:  # a:bodyPr wrap="none" — overflow is horizontal
        widest, _ = _max_line_width_emu(shape, tf, slide, ctx)
        overflow = widest - usable_w
        if overflow <= usable_w * ctx.cfg.overflow_tolerance:
            return None
        return {
            "message": (
                f"Текст выходит за горизонтальные границы фигуры "
                f"'{shape.name}' (wrap=none)"
            ),
            "measured_value": round(widest / EMU_PER_PT, 1),
            "threshold": round(usable_w / EMU_PER_PT, 1),
            "evidence_detail": (
                f"widest line ≈{widest / EMU_PER_PT:.1f}pt > "
                f"usable width {usable_w / EMU_PER_PT:.1f}pt"
            ),
        }

    required_h, widest_word = _estimate_text_height_emu(shape, tf, slide, ctx, usable_w)
    horizontal = widest_word > usable_w * (1 + ctx.cfg.overflow_tolerance)
    vertical = required_h > usable_h * (1 + ctx.cfg.overflow_tolerance)
    if not horizontal and not vertical:
        return None
    details = []
    if horizontal:
        details.append(
            f"unbreakable word ≈{widest_word / EMU_PER_PT:.1f}pt > "
            f"usable width {usable_w / EMU_PER_PT:.1f}pt"
        )
    if vertical:
        details.append(
            f"estimated text height ≈{required_h / EMU_PER_PT:.1f}pt > "
            f"usable height {usable_h / EMU_PER_PT:.1f}pt"
        )
    return {
        "message": f"Текст выходит за границы фигуры '{shape.name}'",
        "measured_value": round(max(required_h, widest_word) / EMU_PER_PT, 1),
        "threshold": (
            round(usable_h / EMU_PER_PT, 1) if vertical else round(usable_w / EMU_PER_PT, 1)
        ),
        "evidence_detail": "; ".join(details),
    }


def check_text_overflow(slide, slide_index: int, ctx: _Ctx) -> list[AuditIssue]:
    # _iter_bbox (not _iter_shapes) -- composes a group's chOff/chExt
    # child-space transform through nested grpSp, so both the reported
    # bbox AND the bbox passed to _measure_text_overflow (which rescales
    # usable_w/usable_h by it) are correct for a shape nested in a
    # scaled group, not just top-level ones.
    issues: list[AuditIssue] = []
    for shape, bbox in _iter_bbox(slide.shapes, ctx, (0.0, 1.0), (0.0, 1.0)):
        if bbox is None:
            continue
        measured = _measure_text_overflow(shape, slide, ctx, bbox)
        if measured is None:
            continue
        issues.append(
            ctx.issue(
                RULE_TEXT_OVERFLOW,
                "error",
                measured["message"],
                slide,
                slide_index,
                [str(shape.shape_id)],
                bbox,
                measured_value=measured["measured_value"],
                threshold=measured["threshold"],
                evidence_kind="geometry",
                evidence_detail=measured["evidence_detail"],
                proposed_actions=["shorten_text", "split_text", "resize_shape"],
            )
        )
    return issues


def check_empty_slide(slide, slide_index: int, ctx: _Ctx) -> list[AuditIssue]:
    _, pictures, content_shapes = _slide_census(
        slide, exclude_names=_SYNTHETIC_DECORATIVE_SHAPE_NAMES
    )
    if pictures == 0 and content_shapes == 0:
        return [
            ctx.issue(
                RULE_EMPTY_SLIDE,
                "error",
                "Пустой слайд: нет фигур с текстом, картинок, таблиц или графиков",
                slide,
                slide_index,
                [],
                None,
                measured_value=0,
                threshold=1,
                evidence_kind="geometry",
                evidence_detail="content element count = 0",
                proposed_actions=["merge_slide", "remove_slide", "add_content"],
            )
        ]
    return []


def check_aspect_ratio(slide, slide_index: int, ctx: _Ctx) -> list[AuditIssue]:
    issues: list[AuditIssue] = []
    for shape in _iter_shapes(slide.shapes):
        if not _is_picture(shape):
            continue
        try:
            px_w, px_h = shape.image.size
        except (KeyError, OSError, AttributeError):
            # image part unreadable — package integrity is a separate rule
            continue
        src_w = px_w * (1 - shape.crop_left - shape.crop_right)
        src_h = px_h * (1 - shape.crop_top - shape.crop_bottom)
        if src_w <= 0 or src_h <= 0 or not shape.width or not shape.height:
            continue
        src_ratio = src_w / src_h
        disp_ratio = shape.width / shape.height
        deviation = abs(disp_ratio / src_ratio - 1)
        if deviation > ctx.cfg.aspect_tolerance:
            issues.append(
                ctx.issue(
                    RULE_ASPECT_RATIO,
                    "error",
                    f"Картинка '{shape.name}' искажена относительно исходных пропорций",
                    slide,
                    slide_index,
                    [str(shape.shape_id)],
                    _shape_bbox(shape, ctx),
                    measured_value=round(disp_ratio, 3),
                    threshold=f"{round(src_ratio, 3)} ±{int(ctx.cfg.aspect_tolerance * 100)}%",
                    evidence_kind="geometry",
                    evidence_detail=(
                        f"frame aspect {disp_ratio:.3f} vs source {src_ratio:.3f} "
                        f"(deviation {deviation:.1%})"
                    ),
                    proposed_actions=["recrop_image", "contain_image"],
                )
            )
    return issues


def check_raster_only(slide, slide_index: int, ctx: _Ctx) -> list[AuditIssue]:
    slide_area = float(ctx.slide_width) * float(ctx.slide_height)
    if slide_area <= 0:
        return []
    text_chars, _, _ = _slide_census(slide)
    if text_chars > 0:
        return []
    issues: list[AuditIssue] = []
    for shape in _iter_shapes(slide.shapes):
        if not _is_picture(shape) or not shape.width or not shape.height:
            continue
        share = (float(shape.width) * float(shape.height)) / slide_area
        if share >= ctx.cfg.raster_only_slide_area:
            issues.append(
                ctx.issue(
                    RULE_RASTER_ONLY,
                    "blocker",
                    "Raster-only слайд: одна картинка покрывает почти весь слайд, "
                    "текста нет — слайд не редактируемый",
                    slide,
                    slide_index,
                    [str(shape.shape_id)],
                    _shape_bbox(shape, ctx),
                    measured_value=round(share, 3),
                    threshold=ctx.cfg.raster_only_slide_area,
                    evidence_kind="geometry",
                    evidence_detail=f"picture covers {share:.1%} of slide area, no text frames",
                    proposed_actions=["native_rebuild"],
                )
            )
    return issues


def _iter_bbox(shapes, ctx, map_x, map_y):
    """(shape, bbox) пары с bbox в нормализованном slide-space [0,1],
    рекурсивно заходя в grpSp. map_x/map_y — (t, s) аффинная карта
    local→slide EMU текущего координатного пространства."""
    sw, sh = float(ctx.slide_width), float(ctx.slide_height)
    if sw <= 0 or sh <= 0:
        return
    for shape in shapes:
        try:
            left, top = shape.left, shape.top
        except AttributeError:
            left = top = None
        if left is not None and top is not None:
            yield shape, {
                "x": (map_x[0] + map_x[1] * float(left)) / sw,
                "y": (map_y[0] + map_y[1] * float(top)) / sh,
                "w": map_x[1] * float(shape.width or 0) / sw,
                "h": map_y[1] * float(shape.height or 0) / sh,
            }
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _iter_bbox(
                shape.shapes, ctx, *_group_map(shape, map_x, map_y)
            )


def _group_map(shape, map_x, map_y):
    """Аффинная карта child-space → slide EMU для детей группы.

    slide = off′ + (child − chOff)·ext′/chExt, где off′/ext′ — slide-space
    (через родительскую карту) геометрия группы. Раскладывается на
    (t′, s′): s′ = s·ext/chExt, t′ = t + s·off − s′·chOff."""
    xfrm = shape.element.find(f"{{{P_NS}}}grpSpPr/{{{A}}}xfrm")
    if xfrm is None:
        return map_x, map_y
    vals = {}
    for tag in ("off", "ext", "chOff", "chExt"):
        el = xfrm.find(f"{{{A}}}{tag}")
        if el is None:
            return map_x, map_y
        vals[tag] = (int(el.get("x", el.get("cx"))), int(el.get("y", el.get("cy"))))
    if vals["chExt"][0] == 0 or vals["chExt"][1] == 0:
        return map_x, map_y

    def _conv(axis_map, off, ext, ch_off, ch_ext):
        t, s = axis_map
        s2 = s * ext / ch_ext
        return (t + s * off - s2 * ch_off, s2)

    return (
        _conv(map_x, vals["off"][0], vals["ext"][0], vals["chOff"][0], vals["chExt"][0]),
        _conv(map_y, vals["off"][1], vals["ext"][1], vals["chOff"][1], vals["chExt"][1]),
    )


def _estimated_table_height_emu(shape, bbox: dict, ctx: _Ctx) -> float:
    """Estimate OOXML table height after a renderer expands wrapped rows.

    The graphicFrame height is just the sum of declared ``a:tr@h`` minima.
    Long cell text can make LibreOffice/PowerPoint grow those rows without
    updating the frame ext, so bbox checks alone miss clipped bottom rows.
    """
    scale_x = bbox["w"] * float(ctx.slide_width) / float(shape.width)
    scale_y = bbox["h"] * float(ctx.slide_height) / float(shape.height)
    total = 0.0
    table = shape.table
    for row in table.rows:
        row_height = float(row.height) * scale_y
        for col_index, cell in enumerate(row.cells):
            tf = cell.text_frame
            if not tf.text.strip() or tf.auto_size == MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE:
                continue
            usable_w = int(
                (float(table.columns[col_index].width)
                 - float(tf.margin_left) - float(tf.margin_right)) * scale_x
            )
            if usable_w <= 0:
                continue
            required = (float(tf.margin_top) + float(tf.margin_bottom)) * scale_y
            for paragraph in tf.paragraphs:
                declared_sizes = [
                    run.font.size.pt for run in paragraph.runs if run.font.size is not None
                ]
                if paragraph.font.size is not None:
                    declared_sizes.append(paragraph.font.size.pt)
                size_pt = max(declared_sizes, default=ctx.default_font_pt)
                lines = 0
                for hard_line in re.split(r"[\v\n]", paragraph.text):
                    wrapped, widest_word = _wrap_metrics(hard_line, size_pt, usable_w)
                    if tf.word_wrap is False:
                        lines += 1
                    else:
                        lines += max(wrapped, math.ceil(widest_word / usable_w))
                required += lines * _para_line_height_emu(paragraph, size_pt)
                required += _para_space(paragraph, "space_before")
                required += _para_space(paragraph, "space_after")
            row_height = max(row_height, required)
        total += row_height
    return total


def check_out_of_bounds(slide, slide_index: int, ctx: _Ctx) -> list[AuditIssue]:
    issues: list[AuditIssue] = []
    tol = ctx.cfg.out_of_bounds_tolerance
    # рекурсивно: у children внутри grpSp координаты заданы в child space
    # (chOff/chExt) — _iter_bbox конвертирует их в slide-space аффинной
    # картой, вложенные группы композируются той же картой.
    for shape, bbox in _iter_bbox(slide.shapes, ctx, (0.0, 1.0), (0.0, 1.0)):
        if bbox is None:
            continue
        overhang = max(
            -bbox["x"],
            -bbox["y"],
            bbox["x"] + bbox["w"] - 1.0,
            bbox["y"] + bbox["h"] - 1.0,
        )
        if overhang > tol:
            issues.append(
                ctx.issue(
                    RULE_OUT_OF_BOUNDS,
                    "error",
                    f"Фигура '{shape.name}' выходит за границы слайда",
                    slide,
                    slide_index,
                    [str(shape.shape_id)],
                    bbox,
                    measured_value=round(overhang, 4),
                    threshold=tol,
                    evidence_kind="geometry",
                    evidence_detail=(
                        f"bbox x={bbox['x']:.3f} y={bbox['y']:.3f} "
                        f"right={bbox['x'] + bbox['w']:.3f} bottom={bbox['y'] + bbox['h']:.3f} "
                        f"outside [0,1] slide bounds by {overhang:.1%}"
                    ),
                    proposed_actions=["move_shape", "resize_shape", "swap_layout"],
                )
            )
            continue
        if not shape.has_table or not shape.height:
            continue
        rendered_height = _estimated_table_height_emu(shape, bbox, ctx)
        projected_bottom = bbox["y"] + rendered_height / float(ctx.slide_height)
        if projected_bottom <= 1.0 + tol:
            continue
        issue = ctx.issue(
                RULE_OUT_OF_BOUNDS,
                "error",
                f"Текст таблицы '{shape.name}' выталкивает строки за границы слайда",
                slide,
                slide_index,
                [str(shape.shape_id)],
                bbox,
                measured_value=round(projected_bottom - 1.0, 4),
                threshold=tol,
                evidence_kind="geometry",
                evidence_detail=(
                    f"declared bottom={bbox['y'] + bbox['h']:.3f}, "
                    f"estimated rendered bottom={projected_bottom:.3f}; "
                    "wrapped table cell text expands row heights"
                ),
                proposed_actions=["swap_layout", "split_table", "move_shape"],
        )
        # A bbox clamp cannot repair this case: the declared frame is
        # already inside the slide. It would create a no-op resize action.
        issue.repairable = False
        issues.append(issue)
    return issues


def _slide_text_tokens(slide) -> frozenset[str]:
    """Множество нормализованных токенов всего текста слайда (включая
    таблицы) — отпечаток текстового контента для duplicate-сравнения."""
    tokens: set[str] = set()
    for shape in _iter_shapes(slide.shapes):
        if shape.has_text_frame:
            tokens.update(shape.text_frame.text.lower().split())
        if getattr(shape, "has_table", False):
            for row in shape.table.rows:
                for cell in row.cells:
                    tokens.update(cell.text.lower().split())
    return frozenset(tokens)


def _find_slide_duplicates(
    slides, similarity_threshold: float
) -> dict[int, tuple[int, float]]:
    """idx слайда → (idx первого почти-идентичного, jaccard-сходство).

    Пары без текста пропускаются: два пустых слайда — зона
    integrity.empty_slide, дубликат из нуля токенов не считается.
    """
    signatures = [_slide_text_tokens(s) for s in slides]
    duplicates: dict[int, tuple[int, float]] = {}
    for j in range(len(signatures)):
        if not signatures[j]:
            continue
        for i in range(j):
            if not signatures[i]:
                continue
            inter = len(signatures[i] & signatures[j])
            union = len(signatures[i] | signatures[j])
            score = inter / union if union else 0.0
            if score >= similarity_threshold:
                duplicates[j] = (i, score)
                break
    return duplicates


def _bullet_paragraph_count(tx_body) -> int:
    """Число a:p с явным буллетом в pPr (buChar/buAutoNum/buBlip).

    Наследуемые буллеты из lstStyle/master и lvl-отступ без bu*
    НЕ считаем: надёжный детерминированный маркер буллета в OOXML —
    только explicit glyph/numbering в pPr."""
    count = 0
    for par in tx_body.findall(f"{{{A}}}p"):
        ppr = par.find(f"{{{A}}}pPr")
        if ppr is None:
            continue
        if any(c.tag in _BULLET_TAGS for c in ppr):
            count += 1
    return count


def check_bullet_count(slide, slide_index: int, ctx: _Ctx) -> list[AuditIssue]:
    """Перегруженный буллетами фрейм (>6 в одном txBody, warning)."""
    issues: list[AuditIssue] = []
    for shape in _iter_shapes(slide.shapes):
        if not shape.has_text_frame:
            continue
        count = _bullet_paragraph_count(shape.text_frame._txBody)
        if count <= ctx.cfg.max_bullets_per_slide:
            continue
        issues.append(
            ctx.issue(
                RULE_BULLET_COUNT,
                "warning",
                f"Больше {ctx.cfg.max_bullets_per_slide} буллетов во фрейме '{shape.name}'",
                slide,
                slide_index,
                [str(shape.shape_id)],
                _shape_bbox(shape, ctx),
                measured_value=count,
                threshold=ctx.cfg.max_bullets_per_slide,
                evidence_kind="geometry",
                evidence_detail=(
                    f"{count} bullet paragraphs in one txBody "
                    "(explicit buChar/buAutoNum/buBlip)"
                ),
                proposed_actions=["shorten_text", "split_slide"],
            )
        )
    return issues


def check_duplicate_slide(slide, slide_index: int, ctx: _Ctx) -> list[AuditIssue]:
    match = ctx.slide_duplicates.get(slide_index)
    if match is None:
        return []
    first_idx, score = match
    return [
        ctx.issue(
            RULE_DUPLICATE_SLIDE,
            "warning",
            f"Слайд почти идентичен слайду[{first_idx}] по текстовому контенту",
            slide,
            slide_index,
            [],
            None,
            measured_value=round(score, 3),
            threshold=ctx.cfg.duplicate_similarity_threshold,
            evidence_kind="text",
            evidence_detail=(
                f"jaccard similarity of normalized slide text = {score:.0%} "
                f"vs slide[{first_idx}]"
            ),
            proposed_actions=["merge_slide", "remove_slide"],
        )
    ]


def check_placeholder_text(slide, slide_index: int, ctx: _Ctx) -> list[AuditIssue]:
    """Текст-заглушка: стоковая подпись шаблона осталась в колоде.

    STRONG-ярус ловит маркер в любом месте текста (lorem, TODO),
    WHOLE-ярус — фигуру, весь текст которой является стоковой
    подписью («Заголовок», «Текст», «вставьте текст»). Короткий
    легитимный текст («Риски», «Выводы») не флагуется — только
    точное совпадение целого фрейма со стоковой фразой.
    """
    issues: list[AuditIssue] = []
    for shape in _iter_shapes(slide.shapes):
        if not shape.has_text_frame:
            continue
        text = shape.text_frame.text
        if not text.strip():
            continue
        strong = bool(_PLACEHOLDER_STRONG_RE.search(text))
        norm = re.sub(r"[\x0b\n]+", " ", text)
        norm = re.sub(r"\s+", " ", norm).strip().rstrip(".").lower()
        whole = bool(_PLACEHOLDER_WHOLE_RE.match(norm)) or any(
            rx.fullmatch(norm) for rx in ctx.placeholder_extra_res
        )
        if not strong and not whole:
            continue
        issues.append(
            ctx.issue(
                RULE_PLACEHOLDER_TEXT,
                "error",
                f"Placeholder-текст во фрейме '{shape.name}'",
                slide,
                slide_index,
                [str(shape.shape_id)],
                _shape_bbox(shape, ctx),
                measured_value=len(norm),
                threshold=0,
                evidence_kind="text",
                evidence_detail=(
                    f"{'strong marker' if strong else 'whole-frame stock phrase'}: "
                    f"{norm[:80]!r}"
                ),
                proposed_actions=["replace_text", "remove_placeholder"],
            )
        )
    return issues


# Гейты «намеренности» перекрытия — пороги из AuditConfig:
# overlap_min_cover — минимальная доля покрытия меньшей фигуры;
# overlap_containment — пересечение ≥ доли площади меньшей фигуры
# считается вложенностью (иконка/текст внутри карточки), не коллизией;
# overlap_min_shape_area — фигура <1% слайда (акцент/иконка);
# overlap_bg_area — фигура ≥80% слайда (фоновая плашка).


def _bbox_intersection_area(a: dict[str, float], b: dict[str, float]) -> float:
    """Площадь пересечения двух нормализованных bbox ({x,y,w,h})."""
    w = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    h = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    return max(0.0, w) * max(0.0, h)


def _fmt_bbox(b: dict[str, float]) -> str:
    return f"[{b['x']:.2f},{b['y']:.2f} {b['w']:.2f}x{b['h']:.2f}]"


def check_unintended_overlap(slide, slide_index: int, ctx: _Ctx) -> list[AuditIssue]:
    """Пара контентных фигур частично пересекается — вероятная коллизия.

    Консервативные гейты «намеренности» (лучше пропустить сомнительное,
    чем флагнуть декор):
    - фигура <1% площади слайда — акцент/иконка, пару пропускаем;
    - фигура ≥80% слайда — фоновая плашка, всё поверх неё легитимно;
    - пересечение <25% площади меньшей — касание краями;
    - пересечение ≥90% меньшей — вложенность (текст/иконка ВНУТРИ
      карточки или плашки — осознанная композиция);
    - text-vs-text: перекрытие текста текстом делает хотя бы один
      нечитаемым — явный дефект, флагуется всегда;
    - text-vs-picture: текст поверх картинки/плашки — стандартный
      приём дизайна (заголовок на hero-фото, подпись на цветном фоне),
      не флагаем — но только пока текст реально умещается в СВОИХ
      границах; если тот же текст-шейп уже сам по себе нарушает
      ``text.overflow`` (см. ``_measure_text_overflow``), пересечение
      с картинкой — не дизайн-приём, а тот overflow физически
      залезающий на соседа, и это флагуется (живой репро: текст,
      сжатый до font-floor, всё равно не помещался в карточку и
      наезжал на decorative-иконку рядом — ни один rule это не ловил).
      Пробовал дополнительно гейтить по размеру картинки (маленькая
      icon-картинка не считается legit hero-фоном) — откатил: сломал
      на реальном organizer-шаблоне легитимный слайд (четыре карточки
      01-04, соединённые декоративной пунктирной линией, продетой
      специально сквозь них) — короткий родной текст там прекрасно
      умещается, а пересечение с линией — сама суть композиции, не
      дефект. Чистая геометрия не отличает «маленький декор, который и
      есть layout-приём» от «маленький декор, в который случайно
      залез текст» — overflow остаётся единственным надёжным сигналом;
    - picture-vs-picture: коллаж/декор, не трогаем.

    Сравнивает фигуры в нормализованном slide-space; children групп
    рекурсивно преобразуются из child-space через ``_iter_bbox``.
    """
    entries: list[tuple[object, dict[str, float], bool]] = []
    for shape, bbox in _iter_bbox(slide.shapes, ctx, (0.0, 1.0), (0.0, 1.0)):
        # The group container is only a coordinate system. Its bbox would
        # otherwise overlap every child and produce a false positive.
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            continue
        if bbox is None or bbox["w"] <= 0 or bbox["h"] <= 0:
            continue
        area = bbox["w"] * bbox["h"]
        if area < ctx.cfg.overlap_min_shape_area or area >= ctx.cfg.overlap_bg_area:
            continue
        has_text = bool(shape.has_text_frame and shape.text_frame.text.strip())
        if not has_text and not _is_picture(shape):
            continue
        entries.append((shape, bbox, has_text))

    issues: list[AuditIssue] = []
    for j in range(len(entries)):
        for i in range(j):
            shape_a, a, text_a = entries[i]
            shape_b, b, text_b = entries[j]
            if not text_a and not text_b:
                continue  # two non-text (picture) shapes — collage/decor
            inter = _bbox_intersection_area(a, b)
            if inter <= 0:
                continue
            smaller = min(a["w"] * a["h"], b["w"] * b["h"])
            cover = inter / smaller
            if cover < ctx.cfg.overlap_min_cover or cover >= ctx.cfg.overlap_containment:
                continue
            if text_a != text_b:
                # one text, one picture: only a defect if the text
                # shape doesn't even fit its own box -- that overflow
                # is what's spilling onto the neighbor, not an
                # intentional caption-over-image composition. A
                # size-based "picture must be hero-sized to count as
                # legitimate" gate was tried and reverted: it flagged a
                # real organizer-template slide (four cards numbered
                # 01-04, connected by a thin decorative dashed line
                # threading behind them) that is a legitimate, deliberate
                # layout with short text that obviously fits -- the
                # dashed line is small by area but its overlap with the
                # cards is the whole point of that design, not a defect.
                # Geometry alone can't tell "small decoration doubling as
                # a layout element" from "small decoration text
                # accidentally collided with" -- overflow is the one
                # signal that's actually reliable here.
                text_shape = shape_a if text_a else shape_b
                text_bbox = a if text_a else b
                if _measure_text_overflow(text_shape, slide, ctx, text_bbox) is None:
                    continue
            ix = {
                "x": max(a["x"], b["x"]),
                "y": max(a["y"], b["y"]),
                "w": min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]),
                "h": min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]),
            }
            issues.append(
                ctx.issue(
                    RULE_UNINTENDED_OVERLAP,
                    "error",
                    f"Фигуры '{shape_a.name}' и '{shape_b.name}' "
                    f"частично пересекаются ({cover:.0%} меньшей)",
                    slide,
                    slide_index,
                    [str(shape_a.shape_id), str(shape_b.shape_id)],
                    ix,
                    measured_value=round(cover, 3),
                    threshold=ctx.cfg.overlap_min_cover,
                    evidence_kind="geometry",
                    evidence_detail=(
                        f"intersection covers {cover:.0%} of smaller bbox "
                        f"(a={_fmt_bbox(a)} b={_fmt_bbox(b)})"
                    ),
                    proposed_actions=["realign", "reflow", "swap_layout"],
                )
            )
    return issues


# ── общие гейты «декоративности» для text.font_floor / accessibility.
# contrast: зеркало composing._body_is_decorative (audit не может
# импортировать composing — тот уже импортирует audit: circular).
_FOOTERISH_PH = frozenset(
    {PP_PLACEHOLDER.DATE, PP_PLACEHOLDER.FOOTER, PP_PLACEHOLDER.SLIDE_NUMBER}
)
_NARROW_CX_EMU = 450_000
_TINY_RUN_MAX = 3
_TINY_RUN_RATIO = 0.5


def _is_footerish_placeholder(shape) -> bool:
    """Дата/футер/номер слайда — конвенционально мелкий служебный текст,
    пороги читаемости к нему не применяем (консервативно)."""
    try:
        if not shape.is_placeholder:
            return False
        return shape.placeholder_format.type in _FOOTERISH_PH
    except (AttributeError, ValueError):
        return False


def _is_decorative_audit_body(shape) -> bool:
    """Декоративная типографика, не «контентный текст»:
    vert-письмо, побуквенная раскладка (большинство run ≤3 символов),
    узкий микро-лейбл <~0.5"."""
    tx = shape.text_frame._txBody
    body_pr = tx.find(f"{{{A}}}bodyPr")
    if body_pr is not None and body_pr.get("vert"):
        return True
    try:
        if shape.width is not None and shape.width < _NARROW_CX_EMU:
            return True
    except AttributeError:
        pass
    runs = [(t.text or "").strip() for t in tx.findall(f".//{{{A}}}t")]
    nonempty = [t for t in runs if t]
    if len(nonempty) >= 2:
        tiny = sum(1 for t in nonempty if len(t) <= _TINY_RUN_MAX)
        if tiny / len(nonempty) > _TINY_RUN_RATIO:
            return True
    return False


def check_font_floor(slide, slide_index: int, ctx: _Ctx) -> list[AuditIssue]:
    """Видимый текст мельче порога читаемости (font_floor_pt, error).

    Меряем минимальный эффективный кегль среди непустых параграфов
    (полная цепочка наследования _effective_font_size_pt). Гейты против
    FP: декоративная типографика (vert/tiny-run/микро-лейбл) и служебные
    placeholder'ы (дата/футер/номер) пропускаются — там мелкий кегль
    конвенционален.
    """
    issues: list[AuditIssue] = []
    floor = ctx.cfg.font_floor_pt
    for shape in _iter_shapes(slide.shapes):
        if not shape.has_text_frame:
            continue
        tf = shape.text_frame
        if not tf.text.strip():
            continue
        if _is_footerish_placeholder(shape) or _is_decorative_audit_body(shape):
            continue
        sizes = [
            _effective_font_size_pt(shape, p, slide, ctx)
            for p in tf.paragraphs
            if p.text.strip()
        ]
        if not sizes:
            continue
        smallest = min(sizes)
        if smallest >= floor:
            continue
        issues.append(
            ctx.issue(
                RULE_FONT_FLOOR,
                "error",
                f"Текст во фрейме '{shape.name}' мельче порога читаемости "
                f"({smallest:.1f}pt < {floor:.0f}pt)",
                slide,
                slide_index,
                [str(shape.shape_id)],
                _shape_bbox(shape, ctx),
                measured_value=round(smallest, 1),
                threshold=floor,
                evidence_kind="geometry",
                evidence_detail=(
                    f"min effective font size {smallest:.1f}pt "
                    f"< {floor:.0f}pt floor"
                ),
                proposed_actions=["map_font", "resize_shape"],
            )
        )
    return issues


# ── accessibility.contrast: разрешение цветов ─────────────────────────
# Консервативная модель: считаем только то, что разрешается
# детерминированно до конкретного sRGB. Неразрешимое (градиент/битмап/
# паттерн/неизвестный трансформ) — честный skip фигуры, не угадывание.

_UNRESOLVED = object()  # маркер «цвет не разрешён» вместо None-vs-rgb

_SCHEME_ALIAS = {"tx1": "dk1", "bg1": "lt1", "tx2": "dk2", "bg2": "lt2"}


def _hex_rgb(val: str) -> tuple[int, int, int]:
    return int(val[0:2], 16), int(val[2:4], 16), int(val[4:6], 16)


def _theme_palette(slide, ctx) -> dict[str, tuple[int, int, int]]:
    """{слот clrScheme: rgb} темы слайда — кешируется на ctx по master-part."""
    try:
        master_part = slide.slide_layout.slide_master.part
    except AttributeError:
        return {}
    key = str(master_part.partname)
    if key in ctx.theme_palettes:
        return ctx.theme_palettes[key]
    palette: dict[str, tuple[int, int, int]] = {}
    for rel in master_part.rels.values():
        if rel.is_external or not rel.reltype.endswith("/theme"):
            continue
        try:
            theme_el = etree.fromstring(rel.target_part.blob)
        except etree.XMLSyntaxError:
            continue
        scheme = theme_el.find(f".//{{{A}}}clrScheme")
        if scheme is None:
            continue
        for slot in scheme:
            name = etree.QName(slot).localname
            for clr in slot:
                tag = etree.QName(clr).localname
                if tag == "srgbClr" and clr.get("val"):
                    palette[name] = _hex_rgb(clr.get("val"))
                elif tag == "sysClr" and (clr.get("lastClr") or clr.get("val")):
                    palette[name] = _hex_rgb(clr.get("lastClr") or clr.get("val"))
    # bg1/tx1/bg2/tx2 идут через clrMap мастера, а не по умолчанию
    # (bg2=lt2): в шаблоне VK Tech bg2=dk2 — тёмная подложка иначе
    # считалась белой, и auto-fix перекрашивал белый текст в чёрный
    clr_map = slide.slide_layout.slide_master.element.find(f"{{{P_NS}}}clrMap")
    if clr_map is not None:
        for alias in ("bg1", "tx1", "bg2", "tx2"):
            target = clr_map.get(alias)
            if target in palette:
                palette[alias] = palette[target]
    ctx.theme_palettes[key] = palette
    return palette


def _apply_color_transforms(clr_el, rgb: tuple[int, int, int]):
    """OOXML-трансформы цвета; неизвестный трансформ → _UNRESOLVED."""
    for t in clr_el:
        tag = etree.QName(t).localname
        val = int(t.get("val", "0")) / 100000
        if tag == "lumMod":
            h, lum, s = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
            rgb = tuple(
                round(c * 255)
                for c in colorsys.hls_to_rgb(h, min(1.0, lum * val), s)
            )
        elif tag == "lumOff":
            h, lum, s = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
            rgb = tuple(
                round(c * 255)
                for c in colorsys.hls_to_rgb(h, min(1.0, max(0.0, lum + val)), s)
            )
        elif tag == "tint":  # интерполяция к белому в sRGB
            rgb = tuple(round(c + (255 - c) * val) for c in rgb)
        elif tag == "shade":
            rgb = tuple(round(c * val) for c in rgb)
        else:  # satMod/inv/comp/gray/alpha и пр. — не угадываем
            return _UNRESOLVED
    return rgb


def _resolve_color_element(clr_el, palette) -> tuple[int, int, int] | object:
    """Цветной элемент (srgbClr/schemeClr/sysClr/scrgbClr) → sRGB."""
    if clr_el is None:
        return _UNRESOLVED
    tag = etree.QName(clr_el).localname
    if tag == "srgbClr" and clr_el.get("val"):
        rgb = _hex_rgb(clr_el.get("val"))
    elif tag == "sysClr":
        base = clr_el.get("lastClr") or clr_el.get("val")
        rgb = _hex_rgb(base) if base else _UNRESOLVED
    elif tag == "schemeClr":
        val = clr_el.get("val")
        slot = val if val in palette else _SCHEME_ALIAS.get(val, val)
        if slot not in palette:
            return _UNRESOLVED
        rgb = palette[slot]
    elif tag == "scrgbClr":
        try:
            rgb = tuple(round(int(clr_el.get(k, "0")) / 100000 * 255) for k in "rgb")
        except (TypeError, ValueError):
            return _UNRESOLVED
    else:  # prstClr и прочее — не угадываем
        return _UNRESOLVED
    return _apply_color_transforms(clr_el, rgb)


def _solid_fill_rgb(parent_el, palette):
    """rgb твёрдой заливки, _UNRESOLVED если solidFill есть но цвет не
    разрешён, None если solidFill нет."""
    if parent_el is None:
        return None
    sf = parent_el.find(f"{{{A}}}solidFill")
    if sf is None:
        return None
    return _resolve_color_element(next(iter(sf), None), palette)


def _shape_own_fill_rgb(shape, palette):
    """Собственная заливка фигуры: rgb / _UNRESOLVED / None.

    None — заливки нет (noFill или не задана): фигура прозрачна и
    пропускает нижележащее. _UNRESOLVED — заливка не сводится к одному
    sRGB (градиент/битмап/паттерн/неразрешимый цвет).
    """
    el = shape._element
    sp_pr = el.find(f"{{{P_NS}}}spPr")
    if sp_pr is not None:
        sf = sp_pr.find(f"{{{A}}}solidFill")
        if sf is not None:
            return _resolve_color_element(next(iter(sf), None), palette)
        if sp_pr.find(f"{{{A}}}noFill") is not None:
            return None
        for tag in ("gradFill", "blipFill", "pattFill", "grpFill"):
            if sp_pr.find(f"{{{A}}}{tag}") is not None:
                return _UNRESOLVED
    style = el.find(f"{{{P_NS}}}style")
    fill_ref = style.find(f"{{{A}}}fillRef") if style is not None else None
    if fill_ref is not None and int(fill_ref.get("idx", "0")) > 0:
        # приближение: цвет fillRef (schemeClr/phClr → слот палитры);
        # fmtScheme-стиль под ним может быть градиентом — считаем по
        # заявленному цвету слота, это детерминировано
        rgb = _resolve_color_element(next(iter(fill_ref), None), palette)
        if rgb is not _UNRESOLVED:
            return rgb
    return None


def _fill_with_alpha(shape, palette):
    """Собственная заливка фигуры как (rgb, alpha) / _UNRESOLVED / None.

    Как ``_shape_own_fill_rgb``, но полупрозрачная твёрдая заливка
    (``a:alpha`` на цвете) не считается неразрешимой: цвет разрешается без
    alpha, а доля непрозрачности возвращается отдельно — её смешивает с
    подложкой вызывающий код."""
    sp_pr = shape._element.find(f"{{{P_NS}}}spPr")
    sf = sp_pr.find(f"{{{A}}}solidFill") if sp_pr is not None else None
    clr = next(iter(sf), None) if sf is not None else None
    if clr is not None:
        alpha_el = clr.find(f"{{{A}}}alpha")
        if alpha_el is not None:
            alpha = int(alpha_el.get("val", "100000")) / 100000
            opaque = copy.deepcopy(clr)
            for el in opaque.findall(f"{{{A}}}alpha"):
                opaque.remove(el)
            rgb = _resolve_color_element(opaque, palette)
            if rgb is _UNRESOLVED:
                return _UNRESOLVED
            return rgb, max(0.0, min(1.0, alpha))
    own = _shape_own_fill_rgb(shape, palette)
    if own is _UNRESOLVED or own is None:
        return own
    return own, 1.0


def _mix(top, alpha: float, under):
    return tuple(round(t * alpha + u * (1 - alpha)) for t, u in zip(top, under, strict=True))


def effective_fill_rgb(shape, slide, ctx, palette):
    """Видимый цвет под текстом фигуры: собственная заливка (полупрозрачная —
    смешанная с тем, что под ней), иначе подложка по z-order.

    None не возвращается: без заливки — подложка/фон слайда."""
    own = _fill_with_alpha(shape, palette)
    if own is _UNRESOLVED:
        return _UNRESOLVED
    if own is not None and own[1] >= 0.999:
        return own[0]
    under = _backdrop_rgb(shape, slide, ctx, palette)
    if own is None or under is _UNRESOLVED:
        return under if own is None else _UNRESOLVED
    return _mix(own[0], own[1], under)


def _backdrop_rgb(shape, slide, ctx, palette):
    """Подложка под центром текстовой фигуры по z-order.

    Верхняя нижележащая фигура, закрывающая центр нашего bbox: твёрдая
    заливка → её цвет; картинка/неразрешимая заливка → _UNRESOLVED
    (фото/градиент за текстом — честный skip). Ничего не закрывает —
    фон слайда по цепочке наследования.
    """
    bbox = _shape_bbox(shape, ctx)
    if bbox is None:
        return _UNRESOLVED
    cx, cy = bbox["x"] + bbox["w"] / 2, bbox["y"] + bbox["h"] / 2
    bg = None
    for s2 in _iter_shapes(slide.shapes):
        if s2._element is shape._element:
            break  # document order ≈ z-order: всё ниже нас просмотрено
        b2 = _shape_bbox(s2, ctx)
        if b2 is None or not (
            b2["x"] <= cx <= b2["x"] + b2["w"]
            and b2["y"] <= cy <= b2["y"] + b2["h"]
        ):
            continue
        own = _fill_with_alpha(s2, palette)
        if own is _UNRESOLVED or isinstance(s2, Picture):
            return _UNRESOLVED
        if own is None:
            continue
        rgb, alpha = own
        if alpha >= 0.999:
            bg = rgb
            continue
        # полупрозрачная подложка смешивается с тем, что под ней
        under = bg if bg is not None else _slide_bg_rgb(slide, ctx, palette)
        if under is _UNRESOLVED:
            return _UNRESOLVED
        bg = _mix(rgb, alpha, under)
    if bg is not None:
        return bg
    return _slide_bg_rgb(slide, ctx, palette)


def _slide_bg_rgb(slide, ctx, palette):
    """Фон слайда по цепочке slide → layout → master → белый (спек).

    bgPr с твёрдой заливкой или bgRef (phClr → слот палитры) разрешаются;
    не-твёрдая заливка на любом уровне → _UNRESOLVED.
    """
    try:
        owners = (slide, slide.slide_layout, slide.slide_layout.slide_master)
    except AttributeError:
        owners = (slide,)
    for owner in owners:
        bg = owner.element.find(f"{{{P_NS}}}cSld/{{{P_NS}}}bg")
        if bg is None:
            continue
        bg_pr = bg.find(f"{{{P_NS}}}bgPr")
        if bg_pr is not None:
            if bg_pr.find(f"{{{A}}}solidFill") is not None:
                return _solid_fill_rgb(bg_pr, palette) or _UNRESOLVED
            for tag in ("gradFill", "blipFill", "pattFill", "grpFill"):
                if bg_pr.find(f"{{{A}}}{tag}") is not None:
                    return _UNRESOLVED
        bg_ref = bg.find(f"{{{P_NS}}}bgRef")
        if bg_ref is not None:
            clr = next(iter(bg_ref), None)
            if clr is not None:
                return _resolve_color_element(clr, palette)
    return (255, 255, 255)  # спек-дефолт: белый


def _defrpr_rgb(container_el, path: str, palette):
    el = container_el.find(path, NSMAP) if container_el is not None else None
    if el is None:
        return None
    rgb = _solid_fill_rgb(el, palette)
    return rgb if rgb is not _UNRESOLVED else None


def _inherited_text_rgb(shape, slide, lvl: int, palette):
    """Цвет по цепочке lstStyle → layout placeholder → master txStyles."""
    tx = shape.text_frame._txBody
    rgb = _defrpr_rgb(tx, f"a:lstStyle/a:lvl{lvl}pPr/a:defRPr", palette)
    if rgb is not None:
        return rgb
    try:
        ph_idx = shape.placeholder_format.idx
        layout = slide.slide_layout
    except (AttributeError, ValueError):
        ph_idx, layout = None, None
    if ph_idx is not None and layout is not None:
        for ph in layout.placeholders:
            if ph.placeholder_format.idx == ph_idx:
                rgb = _defrpr_rgb(
                    ph.element.find(f".//{{{P_NS}}}txBody"),
                    f"a:lstStyle/a:lvl{lvl}pPr/a:defRPr",
                    palette,
                )
                break
        if rgb is not None:
            return rgb
        try:
            master_el = layout.slide_master.element
            ph_type = shape.placeholder_format.type
            style = (
                "p:titleStyle"
                if ph_type in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE)
                else "p:bodyStyle"
                if ph_type == PP_PLACEHOLDER.BODY
                else "p:otherStyle"
            )
            rgb = _defrpr_rgb(
                master_el, f"p:txStyles/{style}/a:lvl{lvl}pPr/a:defRPr", palette
            )
        except AttributeError:
            pass
    return rgb


def _run_text_rgb(run, paragraph, shape, slide, ctx, palette):
    """Цвет рана: rPr → pPr/defRPr → наследование → theme dk1 (спек)."""
    rgb = _solid_fill_rgb(run._r.find(f"{{{A}}}rPr"), palette)
    if rgb is _UNRESOLVED:
        return _UNRESOLVED  # явный цвет не разрешён — честный skip рана
    if rgb is not None:
        return rgb
    lvl = min(int(paragraph.level or 0) + 1, 9)
    rgb = _defrpr_rgb(
        paragraph._p, "a:pPr/a:defRPr", palette
    )
    if rgb is None:
        rgb = _inherited_text_rgb(shape, slide, lvl, palette)
    if rgb is None:
        rgb = palette.get("dk1", (0, 0, 0))  # OOXML default = dk1
    return rgb


def _rel_luminance(rgb: tuple[int, int, int]) -> float:
    def lin(c: int) -> float:
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(a, b) -> float:
    l1 = max(_rel_luminance(a), _rel_luminance(b))
    l2 = min(_rel_luminance(a), _rel_luminance(b))
    return (l1 + 0.05) / (l2 + 0.05)


def check_contrast(slide, slide_index: int, ctx: _Ctx) -> list[AuditIssue]:
    """Контраст текста к подложке < contrast_min_ratio (WCAG 4.5, error).

    Цвет текста — цепочка rPr → defRPr → lstStyle/placeholder/master →
    theme dk1; подложка — solidFill фигуры / style fillRef / фон слайда
    (slide→layout→master→белый). Неразрешимое (градиент, картинка,
    неизвестный трансформ) — честный skip: правило молчит, а не гадает.
    Флагается минимальный ratio среди ранов фигуры.
    """
    issues: list[AuditIssue] = []
    min_ratio = ctx.cfg.contrast_min_ratio
    for shape in _iter_shapes(slide.shapes):
        if not shape.has_text_frame:
            continue
        tf = shape.text_frame
        if not tf.text.strip():
            continue
        if _is_footerish_placeholder(shape) or _is_decorative_audit_body(shape):
            continue
        palette = _theme_palette(slide, ctx)
        bg = effective_fill_rgb(shape, slide, ctx, palette)
        if bg is _UNRESOLVED:
            continue  # фон не сводится к одному цвету — не гадаем
        worst = None
        for paragraph in tf.paragraphs:
            for run in paragraph.runs:
                if not run.text.strip():
                    continue
                fg = _run_text_rgb(run, paragraph, shape, slide, ctx, palette)
                if fg is _UNRESOLVED:
                    continue  # цвет рана не разрешён — пропускаем ран
                ratio = _contrast_ratio(fg, bg)
                if worst is None or ratio < worst:
                    worst = ratio
        if worst is None or worst >= min_ratio:
            continue
        issues.append(
            ctx.issue(
                RULE_CONTRAST,
                "error",
                f"Низкий контраст текста во фрейме '{shape.name}' "
                f"({worst:.1f} < {min_ratio:.1f})",
                slide,
                slide_index,
                [str(shape.shape_id)],
                _shape_bbox(shape, ctx),
                measured_value=round(worst, 2),
                threshold=min_ratio,
                evidence_kind="geometry",
                evidence_detail=(
                    f"min WCAG contrast ratio {worst:.2f} < {min_ratio:.1f} "
                    f"(fg vs resolved solid background)"
                ),
                proposed_actions=["map_color"],
            )
        )
    return issues


def check_bullet_length(slide, slide_index: int, ctx: _Ctx) -> list[AuditIssue]:
    """Перегруженный словами буллет (>max_words_per_bullet, warning).

    Буллет — тот же маркер, что у density.bullet_count: explicit
    buChar/buAutoNum/buBlip в pPr параграфа. Считаются слова всего
    a:p (конкатенация a:t, split по пробелам). Флаг — один issue на
    фигуру с measured = худший параграф и числом нарушителей в detail.
    Гейты: те же служебные placeholder'ы и декоративные тела, что у
    font_floor/contrast, — там «буллет» вряд ли несёт читаемый смысл.
    """
    issues: list[AuditIssue] = []
    limit = ctx.cfg.max_words_per_bullet
    for shape in _iter_shapes(slide.shapes):
        if not shape.has_text_frame:
            continue
        if _is_footerish_placeholder(shape) or _is_decorative_audit_body(shape):
            continue
        worst = 0
        offenders = 0
        for par in shape.text_frame._txBody.findall(f"{{{A}}}p"):
            ppr = par.find(f"{{{A}}}pPr")
            if ppr is None or not any(c.tag in _BULLET_TAGS for c in ppr):
                continue
            text = "".join(t.text or "" for t in par.findall(f".//{{{A}}}t"))
            words = len(text.split())
            if words > limit:
                offenders += 1
                worst = max(worst, words)
        if not offenders:
            continue
        issues.append(
            ctx.issue(
                RULE_BULLET_LENGTH,
                "warning",
                f"Буллет во фрейме '{shape.name}' длиннее {limit} слов "
                f"({worst} слов)",
                slide,
                slide_index,
                [str(shape.shape_id)],
                _shape_bbox(shape, ctx),
                measured_value=worst,
                threshold=limit,
                evidence_kind="geometry",
                evidence_detail=(
                    f"{offenders} bullet paragraph(s) exceed "
                    f"{limit} words (worst {worst})"
                ),
                proposed_actions=["shorten_text"],
            )
        )
    return issues


def check_table_size(slide, slide_index: int, ctx: _Ctx) -> list[AuditIssue]:
    """Перегруженная таблица (>max_table_rows строк или >max_table_cols
    колонок, warning). Размер — фактический по a:tbl, без эвристик."""
    issues: list[AuditIssue] = []
    for shape in _iter_shapes(slide.shapes):
        if not getattr(shape, "has_table", False):
            continue
        table = shape.table
        rows, cols = len(table.rows), len(table.columns)
        if rows <= ctx.cfg.max_table_rows and cols <= ctx.cfg.max_table_cols:
            continue
        issues.append(
            ctx.issue(
                RULE_TABLE_SIZE,
                "warning",
                f"Таблица '{shape.name}' {rows}x{cols} больше "
                f"{ctx.cfg.max_table_rows}x{ctx.cfg.max_table_cols}",
                slide,
                slide_index,
                [str(shape.shape_id)],
                _shape_bbox(shape, ctx),
                measured_value=f"{rows}x{cols}",
                threshold=f"{ctx.cfg.max_table_rows}x{ctx.cfg.max_table_cols}",
                evidence_kind="geometry",
                evidence_detail=f"table {rows} rows x {cols} cols",
                proposed_actions=["split_slide", "shorten_text"],
            )
        )
    return issues


def check_font_family(slide, slide_index: int, ctx: _Ctx) -> list[AuditIssue]:
    """Слайд мешает >max_font_families семейств шрифтов (warning).

    Семейство — typeface из a:latin непустого a:r прогона; theme-refs
    (+mj-lt/+mn-lt и т.п.) — отдельные pseudo-семейства (visually это
    разные шрифты). Наследуемые из master/lstStyle шрифты НЕ считаем —
    детерминированно недоказуемы без полной resolution-цепочки,
    консервативный пропуск. Служебные placeholder'ы/декоративные тела
    пропускаем (футер часто стилистически отличен по дизайну).
    """
    issues: list[AuditIssue] = []
    latin = f"{{{A}}}latin"
    families: set[str] = set()
    flagged_shapes: list[str] = []
    for shape in _iter_shapes(slide.shapes):
        if not shape.has_text_frame:
            continue
        if _is_footerish_placeholder(shape) or _is_decorative_audit_body(shape):
            continue
        shape_families: set[str] = set()
        for par in shape.text_frame._txBody.findall(f"{{{A}}}p"):
            for run in par.findall(f"{{{A}}}r"):
                text_el = run.find(f"{{{A}}}t")
                if text_el is None or not (text_el.text or "").strip():
                    continue
                rpr = run.find(f"{{{A}}}rPr")
                if rpr is None:
                    continue
                lat = rpr.find(latin)
                if lat is not None and lat.get("typeface"):
                    shape_families.add(lat.get("typeface"))
        if shape_families:
            flagged_shapes.append(str(shape.shape_id))
        families |= shape_families
    if len(families) > ctx.cfg.max_font_families:
        issues.append(
            ctx.issue(
                RULE_FONT_FAMILY,
                "warning",
                f"Слайд использует {len(families)} семейств шрифтов "
                f"(максимум {ctx.cfg.max_font_families}): "
                + ", ".join(sorted(families)[:6]),
                slide,
                slide_index,
                flagged_shapes,
                None,
                measured_value=len(families),
                threshold=ctx.cfg.max_font_families,
                evidence_kind="geometry",
                evidence_detail=(
                    f"{len(families)} distinct latin typefaces on slide: "
                    + ", ".join(sorted(families))
                ),
                proposed_actions=["map_font"],
            )
        )
    return issues


def check_chart_series(slide, slide_index: int, ctx: _Ctx) -> list[AuditIssue]:
    """Диаграмма перегружена сериями (>max_chart_series c:ser, warning).

    Считаем фактические <c:ser> во всех plotArea-подграфах chartSpace
    (мульти-plot chart суммирует серии всех плоскостей — визуально они
    конкурируют за внимание в одной диаграмме). Не-парсибельная часть
    честно пропускается, не гадаем.
    """
    issues: list[AuditIssue] = []
    for shape in _iter_shapes(slide.shapes):
        if not getattr(shape, "has_chart", False):
            continue
        try:
            chart_space = shape.chart._chartSpace
        except (AttributeError, ValueError):
            continue
        n_series = len(chart_space.findall(f".//{{{C_NS}}}ser"))
        if n_series <= ctx.cfg.max_chart_series:
            continue
        issues.append(
            ctx.issue(
                RULE_CHART_SERIES,
                "warning",
                f"Диаграмма '{shape.name}' содержит {n_series} серий "
                f"(максимум {ctx.cfg.max_chart_series})",
                slide,
                slide_index,
                [str(shape.shape_id)],
                _shape_bbox(shape, ctx),
                measured_value=n_series,
                threshold=ctx.cfg.max_chart_series,
                evidence_kind="package",
                evidence_detail=f"chartSpace holds {n_series} c:ser series",
                proposed_actions=["split_slide", "reflow"],
            )
        )
    return issues


def check_chart_metadata(slide, slide_index: int, ctx: _Ctx) -> list[AuditIssue]:
    """Диаграмма без читабельных метаданных (error).

    Две честные группы отсутствующего:
      - оси: декартов диаграмма (catAx/valAx/serAx/dateAx с delete!=1)
        без единого c:title оси и без c:dispUnits — читателю неясны
        единицы и смысл измерений;
      - легенда/подписи: нет c:legend и нет действующих c:dLbls
        (хотя бы один show*="1") — серии неотличимы.
    Не-декартов диаграмма (pie/doughnut без осей) проверяется только по
    легенде/подписям — осей у неё нет по построению. Не-парсибельная
    часть — honest skip."""
    issues: list[AuditIssue] = []
    for shape in _iter_shapes(slide.shapes):
        if not getattr(shape, "has_chart", False):
            continue
        try:
            chart_space = shape.chart._chartSpace
        except (AttributeError, ValueError):
            continue
        plot = chart_space.find(f".//{{{C_NS}}}plotArea")
        if plot is None:
            continue
        missing: list[str] = []
        axes = [
            ax
            for tag in ("catAx", "valAx", "serAx", "dateAx")
            for ax in plot.findall(f".//{{{C_NS}}}{tag}")
            if (ax.find(f"{{{C_NS}}}delete") is None
                or ax.find(f"{{{C_NS}}}delete").get("val") != "1")
        ]
        if axes:
            titled = any(
                ax.find(f"{{{C_NS}}}title") is not None
                or ax.find(f"{{{C_NS}}}dispUnits") is not None
                for ax in axes
            )
            if not titled:
                missing.append("нет подписей и единиц осей")
        legend = chart_space.find(f".//{{{C_NS}}}chart/{{{C_NS}}}legend")
        labeled = any(
            any(
                (show.get("val") or "0") == "1"
                for show in dlbls.iter()
                if etree.QName(show).localname.startswith("show")
            )
            for dlbls in plot.findall(f".//{{{C_NS}}}dLbls")
        )
        if legend is None and not labeled:
            missing.append("нет легенды и подписей данных")
        if not missing:
            continue
        issues.append(
            ctx.issue(
                RULE_CHART_METADATA,
                "error",
                f"Диаграмма '{shape.name}': " + "; ".join(missing),
                slide,
                slide_index,
                [str(shape.shape_id)],
                _shape_bbox(shape, ctx),
                measured_value=len(missing),
                threshold=0,
                evidence_kind="package",
                evidence_detail=(
                    f"axes={len(axes)} legend={'yes' if legend is not None else 'no'} "
                    f"dLbls={'yes' if labeled else 'no'}; " + "; ".join(missing)
                ),
                proposed_actions=["edit_chart", "recheck"],
            )
        )
    return issues


def _own_xfrm_emu(shape) -> tuple[int, int, int, int] | None:
    """Явный a:xfrm фигуры (x, y, cx, cy в EMU) или None — тогда позиция
    наследуется из layout/master и фигура соответствует по построению."""
    xfrm = shape._element.find(f"{{{P_NS}}}spPr/{{{A}}}xfrm")
    if xfrm is None:
        return None
    off = xfrm.find(f"{{{A}}}off")
    ext = xfrm.find(f"{{{A}}}ext")
    if off is None or ext is None:
        return None
    return (
        int(off.get("x", 0)),
        int(off.get("y", 0)),
        int(ext.get("cx", 0)),
        int(ext.get("cy", 0)),
    )


def _declared_ph_xfrm(slide, ph_type, idx) -> tuple[int, int, int, int] | None:
    """Задекларированная позиция placeholder'а: layout ph с тем же idx
    (иначе того же type), при отсутствии явного xfrm — master.
    Ни одно звено не несёт позицию → None (честный пропуск)."""
    for host in (slide.slide_layout, slide.slide_layout.slide_master):
        for ph in host.placeholders:
            fmt = ph.placeholder_format
            if fmt.idx == idx or fmt.type == ph_type:
                value = _own_xfrm_emu(ph)
                if value is not None:
                    return value
    return None


def _is_named_logo(shape) -> bool:
    """Не-placeholder фигура, явно поименованная как логотип."""
    try:
        if shape.is_placeholder:
            return False
    except (AttributeError, ValueError):
        return False
    name = (shape.name or "").lower()
    return "logo" in name or "лого" in name


def _declared_named_xfrm(slide, name: str) -> tuple[int, int, int, int] | None:
    """Позиция same-named фигуры на layout (затем master) — «якорь»,
    который шаблон задал для логотипа. Нет тёзки-опоры → None."""
    for host in (slide.slide_layout, slide.slide_layout.slide_master):
        for sh in host.shapes:
            if (sh.name or "") == name:
                value = _own_xfrm_emu(sh)
                if value is not None:
                    return value
    return None


def check_anchor_position(slide, slide_index: int, ctx: _Ctx) -> list[AuditIssue]:
    """Footer/логотип сдвинут с задекларированной позиции (error).

    Сравниваем явный x/y/w/h служебного placeholder'а (ftr/sldNum/dt)
    и явно поименованной лого-фигуры против позиции, задекларированной
    на layout (fallback master): любая из 4 компонент xfrm, отклонённая
    больше anchor_tolerance_pt, — сдвиг.

    Гейты против FP (консервативные):
    - фигура без собственного a:xfrm наследует позицию — соответствует
      по построению, пропускаем;
    - нет ph-тёзки на layout/master с явным xfrm (для ph) или same-named
      фигуры (для лого) — задекларированной опоры нет, честный пропуск;
    - только top-level фигуры слайда: дети групп живут в координатах
      chXfrm, сравнение с slide-space-позицией layout было бы гаданием.
    """
    issues: list[AuditIssue] = []
    tol_emu = ctx.cfg.anchor_tolerance_pt * EMU_PER_PT
    fields = ("x", "y", "cx", "cy")
    for shape in slide.shapes:
        own = _own_xfrm_emu(shape)
        if own is None:
            continue
        if _is_footerish_placeholder(shape):
            fmt = shape.placeholder_format
            declared = _declared_ph_xfrm(slide, fmt.type, fmt.idx)
        elif _is_named_logo(shape):
            declared = _declared_named_xfrm(slide, shape.name)
        else:
            continue
        if declared is None:
            continue
        diffs = [abs(a - b) for a, b in zip(own, declared, strict=True)]
        worst = max(diffs)
        if worst <= tol_emu:
            continue
        moved = ", ".join(
            f"{fields[i]} Δ{d / EMU_PER_PT:.1f}pt"
            for i, d in enumerate(diffs)
            if d > tol_emu
        )
        issues.append(
            ctx.issue(
                RULE_ANCHOR_POSITION,
                "error",
                f"Служебная фигура '{shape.name}' сдвинута с "
                f"задекларированной позиции ({moved})",
                slide,
                slide_index,
                [str(shape.shape_id)],
                _shape_bbox(shape, ctx),
                measured_value=round(worst / EMU_PER_PT, 1),
                threshold=ctx.cfg.anchor_tolerance_pt,
                evidence_kind="geometry",
                evidence_detail=(
                    f"own xfrm={own} vs declared={declared}; "
                    f"deviated: {moved} (tol {ctx.cfg.anchor_tolerance_pt}pt)"
                ),
                proposed_actions=["realign"],
            )
        )
    return issues


# occupancy меряется на растровой сетке normalized-координат — union
# bbox'ов без двойного счёта пересечений, детерминированно.
_OCC_GRID_X = 128
_OCC_GRID_Y = 72


def _mark_cells(cells: bytearray, bbox: dict[str, float]) -> None:
    """Пометить ячейки сетки, перекрытые bbox (clipped в [0,1])."""
    x0 = max(bbox["x"], 0.0)
    y0 = max(bbox["y"], 0.0)
    x1 = min(bbox["x"] + bbox["w"], 1.0)
    y1 = min(bbox["y"] + bbox["h"], 1.0)
    if x1 <= x0 or y1 <= y0:
        return
    for gy in range(int(y0 * _OCC_GRID_Y), min(int(y1 * _OCC_GRID_Y) + 1, _OCC_GRID_Y)):
        row = gy * _OCC_GRID_X
        for gx in range(int(x0 * _OCC_GRID_X), min(int(x1 * _OCC_GRID_X) + 1, _OCC_GRID_X)):
            cells[row + gx] = 1


def _slide_occupancy(slide, ctx: _Ctx) -> tuple[float, float, list[str]]:
    """(occ_total, occ_content, id'ы контентных фигур) слайда — метрики
    check_occupancy без порогов; их же берёт Quality Passport."""
    cells_total = bytearray(_OCC_GRID_X * _OCC_GRID_Y)
    cells_content = bytearray(_OCC_GRID_X * _OCC_GRID_Y)
    flagged_shapes: list[str] = []
    for shape, bbox in _iter_bbox(slide.shapes, ctx, (0.0, 1.0), (0.0, 1.0)):
        if bbox is None or bbox["w"] <= 0 or bbox["h"] <= 0:
            continue
        _mark_cells(cells_total, bbox)
        area = bbox["w"] * bbox["h"]
        if area >= ctx.cfg.overlap_bg_area:
            continue
        if _is_picture(shape) or getattr(shape, "has_table", False) or getattr(
            shape, "has_chart", False
        ):
            is_content = True
        elif shape.has_text_frame and shape.text_frame.text.strip():
            if _is_footerish_placeholder(shape) or _is_decorative_audit_body(shape):
                continue
            is_content = True
        else:
            is_content = False
        if not is_content:
            continue
        _mark_cells(cells_content, bbox)
        flagged_shapes.append(str(shape.shape_id))

    total_cells = _OCC_GRID_X * _OCC_GRID_Y
    occ_total = sum(cells_total) / total_cells
    occ_content = sum(cells_content) / total_cells
    return occ_total, occ_content, flagged_shapes


def average_occupancy(path: str | Path) -> float | None:
    """Средняя по колоде заполненность слайда (occ_total, доля 0..1) —
    для ``metrics.readability.avg_occupancy``; None, если слайдов нет."""
    prs = Presentation(str(path))
    if not len(prs.slides):
        return None
    ctx = _Ctx(prs, "occupancy", 0, default_audit_config())
    totals = [_slide_occupancy(slide, ctx)[0] for slide in prs.slides]
    return round(sum(totals) / len(totals), 4)


def check_occupancy(slide, slide_index: int, ctx: _Ctx) -> list[AuditIssue]:
    """Заполненность слайда вне [occupancy_min, occupancy_max] (warning).

    Две честные метрики на union покрытия по сетке:
    - occ_total: покрытие ВСЕМИ видимыми объектами (вкл. фоновые плашки
      и полноэкранные визуалы). Under-fill только когда реально мало
      чего отрисовано — слайд-разделитель или hero-фото не «пустой».
    - occ_content: покрытие контентными фигурами ПЛОЩАДЬЮ <overlap_bg_area
      (текст не-декоративный/не-служебный, картинки, таблицы, диаграммы).
      Не-текстовые автофигуры/фриформы не считаются — детерминированно
      неотличимы от декора. Фигуры ≥80% слайда исключаем: фоновая плашка
      или доминирующий визал не «перегруз», а паттерн макета.

    Over-fill флагается по occ_content; under-fill — по occ_total.
    """
    occ_total, occ_content, flagged_shapes = _slide_occupancy(slide, ctx)
    issues: list[AuditIssue] = []
    if occ_total < ctx.cfg.occupancy_min:
        issues.append(
            ctx.issue(
                RULE_OCCUPANCY,
                "warning",
                f"Слайд заполнен на {occ_total:.0%} — меньше минимума "
                f"{ctx.cfg.occupancy_min:.0%}",
                slide,
                slide_index,
                flagged_shapes,
                None,
                measured_value=round(occ_total, 4),
                threshold=ctx.cfg.occupancy_min,
                evidence_kind="geometry",
                evidence_detail=(
                    f"occupancy total coverage {occ_total:.1%} "
                    f"< {ctx.cfg.occupancy_min:.0%} floor"
                ),
                proposed_actions=["reflow", "swap_layout"],
            )
        )
    if occ_content > ctx.cfg.occupancy_max:
        issues.append(
            ctx.issue(
                RULE_OCCUPANCY,
                "warning",
                f"Контент занимает {occ_content:.0%} площади слайда — "
                f"больше максимума {ctx.cfg.occupancy_max:.0%}",
                slide,
                slide_index,
                flagged_shapes,
                None,
                measured_value=round(occ_content, 4),
                threshold=ctx.cfg.occupancy_max,
                evidence_kind="geometry",
                evidence_detail=(
                    f"occupancy content coverage {occ_content:.1%} "
                    f"> {ctx.cfg.occupancy_max:.0%} ceiling"
                ),
                proposed_actions=["reflow", "swap_layout"],
            )
        )
    return issues


def _declared_font_scale(slide, ctx) -> frozenset:
    """Шаги типографической шкалы шаблона (pt) — из defRPr@sz, задеклари-
    рованных в layout (lstStyle плейсхолдеров) и master (txStyles).
    Кеш на ctx по layout-part, как _theme_palette."""
    try:
        layout = slide.slide_layout
    except AttributeError:
        return frozenset()
    key = str(layout.part.partname)
    if key in ctx.font_scales:
        return ctx.font_scales[key]
    scale: set[float] = {ctx.default_font_pt}
    for el in (layout.element, getattr(layout.slide_master, "element", None)):
        if el is None:
            continue
        for defrpr in el.findall(f".//{{{A}}}defRPr"):
            if defrpr.get("sz"):
                scale.add(int(defrpr.get("sz")) / 100)
    ctx.font_scales[key] = frozenset(scale)
    return ctx.font_scales[key]


def _canonical_font_step(pt: float) -> float:
    """Ступень 0.5pt: дробные кегли вроде 8.12/6.75/14.06 — следы
    автоподбора (fontScale, «сжать текст») и относятся к ближайшей
    ступени, а не образуют свои."""
    return round(pt * 2) / 2


def _observed_font_scale(ctx) -> frozenset:
    """Наблюдаемая шкала колоды: явные a:rPr@sz, встречающиеся на
    ≥ ``font_scale_min_uses`` ранах.

    Декларации layout/master описывают только то, что шаблон задал
    «по умолчанию»; реальная типографика шаблонных слайдов живёт в
    явных ``sz`` ранов, и её родные кегли (12/9/14/16/18/10…) не имеют
    права считаться нарушением. Порог по частоте отделяет установленные
    ступени от разовых выбросов. Считается один раз на прогон.
    """
    if ctx.observed_font_scale is not None:
        return ctx.observed_font_scale
    census: dict[float, int] = {}
    for slide in ctx.slides:
        for shape in _iter_shapes(slide.shapes):
            if not shape.has_text_frame:
                continue
            if _is_footerish_placeholder(shape) or _is_decorative_audit_body(shape):
                continue
            for run in shape.text_frame._txBody.iter(f"{{{A}}}r"):
                text_el = run.find(f"{{{A}}}t")
                if text_el is None or not (text_el.text or "").strip():
                    continue
                rpr = run.find(f"{{{A}}}rPr")
                if rpr is None or not rpr.get("sz"):
                    continue
                step = _canonical_font_step(int(rpr.get("sz")) / 100)
                census[step] = census.get(step, 0) + 1
    ctx.observed_font_scale = frozenset(
        step for step, n in census.items() if n >= ctx.cfg.font_scale_min_uses
    )
    return ctx.observed_font_scale


def check_font_scale(slide, slide_index: int, ctx: _Ctx) -> list[AuditIssue]:
    """Явный кегль вне шкалы шаблона (warning).

    Проверяются ТОЛЬКО явные a:rPr@sz на непустых ранах — унаследованный
    размер по построению идёт из шкалы и нарушить её не может. Шкала —
    объединение (а) всех defRPr@sz из layout+master слайда (+defaultTextStyle
    lvl1) и (б) наблюдаемой шкалы колоды: размеров, явно заданных на
    ≥ ``font_scale_min_uses`` ранах (дробные следы автоподбора приводятся
    к ступени 0.5pt). Без (б) родные слайды шаблона считались нарушением
    собственной типографики. Кегли ≥ ``font_scale_display_pt`` — дисплейные
    (крупные цифры и обложки), в шкалу текста не входят. Допуск ±font_scale_tolerance относительно
    ближайшего шага. Служебные placeholder'ы/декоративные тела — skip.
    """
    scale = _declared_font_scale(slide, ctx)
    if not scale:
        return []
    scale = scale | _observed_font_scale(ctx)
    tol = ctx.cfg.font_scale_tolerance
    display_pt = ctx.cfg.font_scale_display_pt
    issues: list[AuditIssue] = []
    for shape in _iter_shapes(slide.shapes):
        if not shape.has_text_frame:
            continue
        if _is_footerish_placeholder(shape) or _is_decorative_audit_body(shape):
            continue
        off_scale: list[float] = []
        for par in shape.text_frame._txBody.findall(f"{{{A}}}p"):
            for run in par.findall(f"{{{A}}}r"):
                text_el = run.find(f"{{{A}}}t")
                if text_el is None or not (text_el.text or "").strip():
                    continue
                rpr = run.find(f"{{{A}}}rPr")
                if rpr is None or not rpr.get("sz"):
                    continue
                pt = int(rpr.get("sz")) / 100
                if pt >= display_pt:
                    continue  # дисплейный размер — вне шкалы текста
                if min(abs(pt - s) / s for s in scale) > tol:
                    off_scale.append(pt)
        if not off_scale:
            continue
        worst = max(off_scale, key=lambda pt: min(abs(pt - s) / s for s in scale))
        deviation = min(abs(worst - s) / s for s in scale)
        issues.append(
            ctx.issue(
                RULE_FONT_SCALE,
                "warning",
                f"Кегль {worst:.1f}pt во фрейме '{shape.name}' вне шкалы "
                f"шаблона ({sorted(scale)}pt, допуск ±{tol:.0%})",
                slide,
                slide_index,
                [str(shape.shape_id)],
                _shape_bbox(shape, ctx),
                measured_value=round(deviation, 4),
                threshold=tol,
                evidence_kind="style",
                evidence_detail=(
                    f"explicit sz {sorted(set(off_scale))}pt, nearest scale "
                    f"step deviates {deviation:.1%} > {tol:.0%}; "
                    f"scale {sorted(scale)}pt (declared + observed)"
                ),
                proposed_actions=["map_font", "reflow"],
            )
        )
    return issues


def _srgb_to_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    """sRGB → CIELAB (D65), для CIE76 ΔE — честная приближённая метрика."""

    def lin(c: float) -> float:
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def _delta_e(rgb1: tuple[int, int, int], rgb2: tuple[int, int, int]) -> float:
    """CIE76 ΔE — евклидова дистанция в Lab."""
    l1, a1, b1 = _srgb_to_lab(rgb1)
    l2, a2, b2 = _srgb_to_lab(rgb2)
    return math.sqrt((l1 - l2) ** 2 + (a1 - a2) ** 2 + (b1 - b2) ** 2)


def _is_neutral_rgb(rgb: tuple[int, int, int]) -> bool:
    """Почти-ахроматический цвет — типографический нейтрал, не элемент
    фирменной палитры (гейт против FP на сером теле текста)."""
    _, _, s = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
    return s < 0.08


def _explicit_fill_rgbs(parent_el, palette) -> Iterable[tuple[int, int, int] | object]:
    """Явные (не scheme) цвета solidFill внутри parent_el.

    schemeClr пропускаем — по построению это цвет палитры (вместе с её
    трансформами: tint/shade акцента — санкционированное использование).
    """
    if parent_el is None:
        return
    for sf in parent_el.iter(f"{{{A}}}solidFill"):
        for clr in sf:
            tag = etree.QName(clr).localname
            if tag == "schemeClr":
                continue
            rgb = _resolve_color_element(clr, palette)
            if rgb is not _UNRESOLVED:
                yield rgb


def check_color_palette(slide, slide_index: int, ctx: _Ctx) -> list[AuditIssue]:
    """Явный цвет вне задекларированной палитры темы (warning).

    Палитра — clrScheme темы слайда (_theme_palette). Проверяются ТОЛЬКО
    явные цвета (srgbClr/sysClr/scrgbClr) в: заливке фигуры, контуре a:ln,
    цвете ранов a:rPr. schemeClr не проверяем — это по определению цвет
    палитры. Унаследованные цвета (lstStyle/master) не проверяем — идут
    из темы. ΔE (CIE76) до ближайшего слота > color_tolerance_delta_e →
    нарушение. Нейтральные (sat<8%) цвета — skip: серый текст — типогра-
    фическая конвенция, не палитровый элемент. Служебные/декоративные
    тела — skip. Внутренние цвета таблиц/диаграмм — honest skip (их
    цветовая модель отдельная, не разбираем)."""
    palette = _theme_palette(slide, ctx)
    if not palette:
        return []
    tol = ctx.cfg.color_tolerance_delta_e
    slots = list(palette.values())
    issues: list[AuditIssue] = []
    for shape in _iter_shapes(slide.shapes):
        offenders: dict[tuple[int, int, int], float] = {}
        # заливка и контур фигуры (spPr — у большинства shape-классов)
        sp_pr = shape.element.find(f"{{{P_NS}}}spPr")
        for rgb in _explicit_fill_rgbs(sp_pr, palette):
            if _is_neutral_rgb(rgb):
                continue
            d = min(_delta_e(rgb, slot) for slot in slots)
            if d > tol:
                offenders[rgb] = max(offenders.get(rgb, 0.0), d)
        # явные цвета ранов
        if shape.has_text_frame and not (
            _is_footerish_placeholder(shape) or _is_decorative_audit_body(shape)
        ):
            for rpr in shape.text_frame._txBody.findall(f".//{{{A}}}rPr"):
                run = rpr.getparent()
                text_el = run.find(f"{{{A}}}t") if run is not None else None
                if text_el is None or not (text_el.text or "").strip():
                    continue
                for rgb in _explicit_fill_rgbs(rpr, palette):
                    if _is_neutral_rgb(rgb):
                        continue
                    d = min(_delta_e(rgb, slot) for slot in slots)
                    if d > tol:
                        offenders[rgb] = max(offenders.get(rgb, 0.0), d)
        if not offenders:
            continue
        worst_rgb, worst_d = max(offenders.items(), key=lambda kv: kv[1])
        issues.append(
            ctx.issue(
                RULE_COLOR_PALETTE,
                "warning",
                f"Цвет #{worst_rgb[0]:02X}{worst_rgb[1]:02X}{worst_rgb[2]:02X} "
                f"во фрейме '{shape.name}' вне палитры темы "
                f"(ΔE {worst_d:.1f} > {tol:.0f})",
                slide,
                slide_index,
                [str(shape.shape_id)],
                _shape_bbox(shape, ctx),
                measured_value=round(worst_d, 1),
                threshold=tol,
                evidence_kind="style",
                evidence_detail=(
                    "off-palette explicit colors: "
                    + ", ".join(
                        f"#{r:02X}{g:02X}{b:02X}(ΔE{d:.0f})"
                        for (r, g, b), d in sorted(
                            offenders.items(), key=lambda kv: -kv[1]
                        )[:6]
                    )
                ),
                proposed_actions=["recolor", "palette_substitution"],
            )
        )
    return issues


# Зона «без отступа» от края слайда — edge_margin (3% по оси: ~7.6мм
# по горизонтали / ~5.7мм по вертикали на 16:9), заметно меньше типовой
# безопасной зоны ~5-10%, поэтому ловит только вплотную прижатый контент.
# edge_bleed_span — фигура, занимающая ≥95% оси, считается осознанным
# edge-to-edge (ленточный баннер/разделитель), эта сторона не проверяется.
# Оба порога — из configs/audit.default.yaml через AuditConfig.


def check_edge_margin(slide, slide_index: int, ctx: _Ctx) -> list[AuditIssue]:
    """Контентная фигура прижата к краю слайда без отступа (warning).

    Гейты «намеренности» (консервативные, как в unintended_overlap — сырой
    шаблон не должен шуметь):
    - участвуют только текстонесущие фигуры (has_text_frame + непустой
      текст): картинка или заливка в край — легитимный bleed;
    - площадь <1% слайда — акцент/иконка;
    - площадь ≥80% слайда — фоновая плашка (overlap_bg_area);
    - placeholder: его геометрию задал layout/master — это авторская
      геометрия шаблона, а не нарушение отступа;
    - фигура, протянутая ≥95% по оси, по этой оси — осознанный
      edge-to-edge, сторона не проверяется.

    Стороны проверяются независимо; флагается каждая прижатая (<3%)
    сторона отдельным issue, чтобы repair-флоу адресовал их по одному.
    Геометрия — _iter_bbox: children групп уже в slide-space.
    """
    issues: list[AuditIssue] = []
    for shape, bbox in _iter_bbox(slide.shapes, ctx, (0.0, 1.0), (0.0, 1.0)):
        if not (shape.has_text_frame and shape.text_frame.text.strip()):
            continue
        try:
            if shape.is_placeholder:
                continue
        except AttributeError:
            pass
        if bbox is None or bbox["w"] <= 0 or bbox["h"] <= 0:
            continue
        area = bbox["w"] * bbox["h"]
        if area < ctx.cfg.overlap_min_shape_area or area >= ctx.cfg.overlap_bg_area:
            continue

        sides: list[tuple[str, float]] = []
        if bbox["w"] < ctx.cfg.edge_bleed_span:
            sides.append(("left", bbox["x"]))
            sides.append(("right", 1.0 - bbox["x"] - bbox["w"]))
        if bbox["h"] < ctx.cfg.edge_bleed_span:
            sides.append(("top", bbox["y"]))
            sides.append(("bottom", 1.0 - bbox["y"] - bbox["h"]))
        for side, gap in sides:
            # зазор <0 (микро-вылет) — зона out_of_bounds; здесь считаем
            # прижатым уже [−tol, margin)
            if gap >= ctx.cfg.edge_margin or gap < -ctx.cfg.out_of_bounds_tolerance:
                continue
            issues.append(
                ctx.issue(
                    RULE_EDGE_MARGIN,
                    "warning",
                    f"Фигура '{shape.name}' прижата к {side} краю слайда "
                    f"без отступа ({gap:.1%})",
                    slide,
                    slide_index,
                    [str(shape.shape_id)],
                    bbox,
                    measured_value=round(max(gap, 0.0), 4),
                    threshold=ctx.cfg.edge_margin,
                    evidence_kind="geometry",
                    evidence_detail=(
                        f"{side} edge gap = {gap:.1%} of slide "
                        f"{'width' if side in ('left', 'right') else 'height'} "
                        f"< {ctx.cfg.edge_margin:.0%} margin band {_fmt_bbox(bbox)}"
                    ),
                    proposed_actions=["move_shape", "reflow"],
                )
            )
    return issues


class _Ctx:
    """Per-run context: ids, deck geometry, issue factory."""

    def __init__(
        self, prs, audit_run_id: str, deck_revision: int, cfg: AuditConfig
    ) -> None:
        self.run_id = audit_run_id
        self.deck_revision = deck_revision
        self.cfg = cfg
        self.slide_width = prs.slide_width
        self.slide_height = prs.slide_height
        self.default_font_pt = self._default_font_pt(prs)
        # partname'ы мастеров самой колоды — для template.layout_origin
        self.master_partnames = {
            str(m.part.partname) for m in prs.slide_masters
        }
        # попарное сравнение слайдов — считается один раз на дек,
        # правило читает только готовую мапу idx → idx
        self.slide_duplicates = _find_slide_duplicates(
            prs.slides, cfg.duplicate_similarity_threshold
        )
        # палитры тем по master-part — для accessibility.contrast,
        # считается лениво один раз на master
        self.theme_palettes: dict[str, dict[str, tuple[int, int, int]]] = {}
        # задекларированные шкалы кеглей по layout-part — для
        # template.font_scale, лениво один раз на layout
        self.font_scales: dict[str, frozenset] = {}
        # наблюдаемая шкала колоды (явные sz на ≥N ранах) — лениво, один раз
        self.slides = list(prs.slides)
        self.observed_font_scale: frozenset | None = None
        # дополнительные WHOLE-ярус паттерны из конфига, скомпилированные
        # один раз на прогон; битый паттерн в yaml не роняет аудит
        self.placeholder_extra_res = []
        for pattern in cfg.placeholder_patterns:
            try:
                self.placeholder_extra_res.append(
                    re.compile(pattern, re.IGNORECASE)
                )
            except re.error:
                continue
        self._seq = 0

    @staticmethod
    def _default_font_pt(prs) -> float:
        """lvl1 размер из presentation.xml defaultTextStyle (fallback 18pt)."""
        el = prs.part._element.find(f"{{{P_NS}}}defaultTextStyle/{{{A}}}lvl1pPr/{{{A}}}defRPr")
        if el is not None and el.get("sz"):
            return int(el.get("sz")) / 100
        return DEFAULT_FONT_PT

    def issue(
        self,
        rule_code: str,
        severity: str,
        message: str,
        slide,
        slide_index: int,
        shape_ids: list[str],
        bbox: dict[str, float] | None,
        *,
        measured_value,
        threshold,
        evidence_kind: str,
        evidence_detail: str,
        proposed_actions: list[str],
    ) -> AuditIssue:
        self._seq += 1
        return AuditIssue(
            id=_issue_id(self.run_id, rule_code, slide_index, self._seq),
            audit_run_id=self.run_id,
            deck_revision=self.deck_revision,
            rule_code=rule_code,
            severity=severity,
            message=message,
            slide_id=str(slide.slide_id),
            slide_index=slide_index,
            shape_ids=shape_ids,
            bbox=bbox,
            measured_value=measured_value,
            threshold=threshold,
            evidence=[
                {
                    "kind": evidence_kind,
                    "ref": f"slide[{slide_index}]",
                    "detail": evidence_detail,
                }
            ],
            proposed_actions=proposed_actions,
        )


def check_slide_clip(slide, slide_index: int, ctx: _Ctx) -> list[AuditIssue]:
    """Оценочный экстент текста уходит за край слайда (error).

    Отличие от смежных правил: `text.overflow` — текст не влезает в
    СВОЮ рамку (внутрифреймовая метрика); `layout.out_of_bounds` —
    САМА рамка за границами слайда. Здесь рамка внутри, но текст
    (по той же font-метрике, что overflow) вылетает за край —
    читатель теряет контент у края.

    Направление вылета по `a:bodyPr@anchor` (t/ctr/b); для `wrap="none"`
    — горизонтально по `algn` первого параграфа (l/ctr/r). Фигура с
    рамкой за краем пропускается — та же находка уже покрыта
    out_of_bounds (правила disjoint). Auto-size фигуры текст не
    выпускают (как в overflow). Только top-level фигуры (дети групп
    живут в chXfrm-координатах — честный пропуск).
    """
    issues: list[AuditIssue] = []
    tol_x = ctx.cfg.out_of_bounds_tolerance * ctx.slide_width
    tol_y = ctx.cfg.out_of_bounds_tolerance * ctx.slide_height
    for shape in slide.shapes:
        if not shape.has_text_frame:
            continue
        tf = shape.text_frame
        if not tf.text.strip():
            continue
        if tf.auto_size in (
            MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE,
            MSO_AUTO_SIZE.SHAPE_TO_FIT_TEXT,
        ):
            continue
        left, top, width, height = (
            shape.left, shape.top, shape.width, shape.height
        )
        if not all(v is not None for v in (left, top, width, height)):
            continue
        if not width or not height:
            continue
        # рамка сама за краем — зона out_of_bounds, не дублируем
        if (
            left < -tol_x
            or top < -tol_y
            or left + width > ctx.slide_width + tol_x
            or top + height > ctx.slide_height + tol_y
        ):
            continue
        usable_w = int(width) - int(tf.margin_left) - int(tf.margin_right)
        usable_h = int(height) - int(tf.margin_top) - int(tf.margin_bottom)
        if usable_w <= 0 or usable_h <= 0:
            continue
        body_pr = tf._txBody.find(f"{{{A}}}bodyPr")
        anchor = body_pr.get("anchor", "t") if body_pr is not None else "t"

        clip_x = 0
        clip_y = 0
        if tf.word_wrap is False:  # wrap="none" — горизонтальный вылет
            widest, _ = _max_line_width_emu(shape, tf, slide, ctx)
            if widest > usable_w:
                pPr = tf._txBody.find(f"{{{A}}}p/{{{A}}}pPr")
                algn = pPr.get("algn", "l") if pPr is not None else "l"
                if algn == "r":
                    clip_x = -(left + width - int(tf.margin_right) - widest)
                elif algn == "ctr":
                    center = left + width / 2
                    clip_x = max(
                        center + widest / 2 - ctx.slide_width,
                        -(center - widest / 2),
                    )
                else:  # l, just, dist — вылет вправо
                    clip_x = left + int(tf.margin_left) + widest - ctx.slide_width
        else:
            required_h, _ = _estimate_text_height_emu(
                shape, tf, slide, ctx, usable_w
            )
            if required_h > usable_h:
                if anchor == "b":
                    clip_y = -(top + height - int(tf.margin_bottom) - required_h)
                elif anchor == "ctr":
                    center = top + height / 2
                    clip_y = max(
                        center + required_h / 2 - ctx.slide_height,
                        -(center - required_h / 2),
                    )
                else:  # t, just, dist — вылет вниз
                    clip_y = (
                        top + int(tf.margin_top) + required_h - ctx.slide_height
                    )
        if clip_y > tol_y or clip_x > tol_x:
            clip = max(
                clip_y / ctx.slide_height if clip_y > 0 else 0.0,
                clip_x / ctx.slide_width if clip_x > 0 else 0.0,
            )
            issues.append(
                ctx.issue(
                    RULE_SLIDE_CLIP,
                    "error",
                    f"Текст фигуры '{shape.name}' обрезается краем слайда "
                    f"(экстент за границей ≈{max(clip_x, clip_y) / EMU_PER_PT:.1f}pt)",
                    slide,
                    slide_index,
                    [str(shape.shape_id)],
                    _shape_bbox(shape, ctx),
                    measured_value=round(clip, 4),
                    threshold=ctx.cfg.out_of_bounds_tolerance,
                    evidence_kind="geometry",
                    evidence_detail=(
                        f"anchor={anchor} wrap={tf.word_wrap}; "
                        f"text extent beyond slide edge "
                        f"x={clip_x / EMU_PER_PT:.1f}pt y={clip_y / EMU_PER_PT:.1f}pt"
                    ),
                    proposed_actions=["shorten_text", "resize_shape", "move_shape"],
                )
            )
    return issues


def check_layout_origin(slide, slide_index: int, ctx: _Ctx) -> list[AuditIssue]:
    """Слайд использует layout вне набора мастеров самой колоды (warning).

    Защитное правило (defense-in-depth): в текущей архитектуре DeckDNA
    слайды клонируются из exemplar-шаблона, layout-цепочка резолвится
    внутри того же пакета — правило по построению вечно-зелёное на
    честных колодах. Срабатывает, если master layout-цепочки слайда не
    входит в slide_masters дека (внешний/вставленный layout) или
    цепочка не резолвится (битая rel).
    """
    try:
        partname = str(slide.slide_layout.slide_master.part.partname)
    except (AttributeError, KeyError, ValueError):
        partname = None
    if partname is not None and partname in ctx.master_partnames:
        return []
    return [
        ctx.issue(
            RULE_LAYOUT_ORIGIN,
            "warning",
            "Слайд использует layout не из набора мастеров шаблона"
            + ("" if partname else " (цепочка layout→master не резолвится)"),
            slide,
            slide_index,
            [],
            None,
            measured_value=1,
            threshold=0,
            evidence_kind="package",
            evidence_detail=f"layout master partname={partname}; "
            f"deck masters={sorted(ctx.master_partnames)}",
            proposed_actions=["swap_layout", "recheck"],
        )
    ]


RULES = (
    check_text_overflow,
    check_empty_slide,
    check_aspect_ratio,
    check_raster_only,
    check_out_of_bounds,
    check_duplicate_slide,
    check_placeholder_text,
    check_bullet_count,
    check_unintended_overlap,
    check_edge_margin,
    check_font_floor,
    check_contrast,
    check_bullet_length,
    check_table_size,
    check_font_family,
    check_chart_series,
    check_occupancy,
    check_font_scale,
    check_color_palette,
    check_chart_metadata,
    check_anchor_position,
    check_slide_clip,
    check_layout_origin,
)


def _package_errors(path: str | Path) -> list[str]:
    """Структурные дефекты OPC-пакета до открытия python-pptx.

    Пакетный sanity (не XSD-валидация): файл — читаемый zip (CRC),
    присутствуют обязательные OPC-части ([Content_Types].xml,
    ppt/presentation.xml), каждая slide-часть парсится как XML.
    """
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, IsADirectoryError, FileNotFoundError, OSError):
        return ["not a readable zip/OPC package"]
    errors: list[str] = []
    bad = zf.testzip()
    if bad is not None:
        errors.append(f"corrupt zip member {bad}")
    names = set(zf.namelist())
    for required in ("[Content_Types].xml", "ppt/presentation.xml"):
        if required not in names:
            errors.append(f"missing required part {required}")
    slide_parts = [
        n
        for n in names
        if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)
    ]
    if "ppt/presentation.xml" in names and not slide_parts:
        errors.append("no ppt/slides/slideN.xml parts")
    for n in slide_parts:
        try:
            etree.fromstring(zf.read(n))
        except etree.XMLSyntaxError:
            errors.append(f"unparseable slide part {n}")
    # слайд без rel на slideLayout — структурный дефект: python-pptx
    # падает при обходе (placeholder-наследование), аудит такого слайда
    # невозможен — это зона blocker'а, а не per-rule issue
    rel_ns = "{http://schemas.openxmlformats.org/package/2006/relationships}"
    for n in slide_parts:
        stem = n.rsplit("/", 1)[-1]
        rels_name = f"ppt/slides/_rels/{stem}.rels"
        if rels_name not in names:
            errors.append(f"slide part {n} has no .rels")
            continue
        try:
            rels_root = etree.fromstring(zf.read(rels_name))
        except etree.XMLSyntaxError:
            errors.append(f"unparseable rels part {rels_name}")
            continue
        layout_targets = [
            r.get("Target")
            for r in rels_root.findall(f"{rel_ns}Relationship")
            if (r.get("Type") or "").endswith("/slideLayout")
        ]
        if not layout_targets:
            errors.append(f"slide part {n} lacks slideLayout relationship")
            continue
        for target in layout_targets:
            # target относителен ppt/slides/ → нормализуем ../
            resolved = n.rsplit("/", 1)[0] + "/" + target
            parts = []
            for seg in resolved.split("/"):
                if seg == "..":
                    if parts:
                        parts.pop()
                elif seg and seg != ".":
                    parts.append(seg)
            if "/".join(parts) not in names:
                errors.append(
                    f"slide part {n} layout rel points to missing {target}"
                )
    return errors


def _dangling_rel_targets(path: str | Path) -> list[str]:
    """Internal rel targets pointing at parts that do not exist.

    Неблокирующая проверка: python-pptx открывает такие пакеты (blip/
    theme разрешаются лениво), поэтому аудит продолжается, а дефект
    сигналится отдельным integrity.package error-issue.
    """
    rel_ns = "{http://schemas.openxmlformats.org/package/2006/relationships}"
    dangling: list[str] = []
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            for rels_name in sorted(names):
                if not rels_name.endswith(".rels"):
                    continue
                try:
                    rels_root = etree.fromstring(zf.read(rels_name))
                except etree.XMLSyntaxError:
                    continue  # нечитаемый rels — уже ловит _package_errors
                owner = rels_name.replace("_rels/", "", 1)
                if owner.endswith(".rels"):
                    owner = owner[: -len(".rels")]
                for rel in rels_root.findall(f"{rel_ns}Relationship"):
                    if rel.get("TargetMode") == "External":
                        continue
                    target = resolve_target(owner, rel.get("Target") or "")
                    if target not in names:
                        dangling.append(
                            f"{owner or '<root>'} -> "
                            f"{(rel.get('Type') or '?').rsplit('/', 1)[-1]} "
                            f"{target}"
                        )
    except (zipfile.BadZipFile, OSError):
        return []
    return dangling


def _dangling_rel_issue(
    run_id: str, deck_revision: int, dangling: list[str]
) -> AuditIssue:
    """Неблокирующее integrity.package issue для висячих rel-таргетов."""
    shown = "; ".join(dangling[:10])
    if len(dangling) > 10:
        shown += f"; …и ещё {len(dangling) - 10}"
    return AuditIssue(
        id=_issue_id(run_id, RULE_PACKAGE, -1, 2),
        audit_run_id=run_id,
        deck_revision=deck_revision,
        rule_code=RULE_PACKAGE,
        severity="error",
        message=(
            "Внутренние relationships указывают на отсутствующие "
            f"парты: {shown}"
        ),
        slide_id=None,
        slide_index=None,
        shape_ids=[],
        bbox=None,
        measured_value=len(dangling),
        threshold=0,
        evidence=[
            {
                "kind": "package",
                "ref": "package",
                "detail": shown,
            }
        ],
        proposed_actions=["regenerate"],
    )


def _package_blocker_issue(
    run_id: str, deck_revision: int, errors: list[str]
) -> AuditIssue:
    """Blocker-issue «файл не открывается» — без слайда и bbox."""
    return AuditIssue(
        id=_issue_id(run_id, RULE_PACKAGE, -1, 1),
        audit_run_id=run_id,
        deck_revision=deck_revision,
        rule_code=RULE_PACKAGE,
        severity="blocker",
        message="Файл не открывается как валидный pptx-пакет: " + "; ".join(errors),
        slide_id=None,
        slide_index=None,
        shape_ids=[],
        bbox=None,
        measured_value=len(errors),
        threshold=0,
        evidence=[
            {
                "kind": "package",
                "ref": "package",
                "detail": "; ".join(errors),
            }
        ],
        proposed_actions=["regenerate"],
    )


def _finalize_repairable(issues: list[AuditIssue]) -> list[AuditIssue]:
    """repairable — не «True по умолчанию», а факт из каталога правил:
    правило чинится, только если у repair-планировщика есть для него
    обработчик (contract B5)."""
    for issue in issues:
        issue.repairable = issue.repairable and is_repairable(issue.rule_code)
    return issues


def audit_deck(
    path: str | Path,
    *,
    deck_revision: int = 0,
    audit_run_id: str | None = None,
    config: AuditConfig | None = None,
) -> list[AuditIssue]:
    """Прогон всех базовых детерминированных правил по .pptx-файлу.

    Пороги — из ``config`` (по умолчанию отгружаемый
    ``configs/audit.default.yaml``, загружается один раз на процесс).
    integrity.package — единственное правило до открытия: битый пакет
    даёт blocker-issue вместо exception."""
    run_id = audit_run_id or uuid.uuid4().hex
    errors = _package_errors(path)
    if errors:
        return _finalize_repairable(
            [_package_blocker_issue(run_id, deck_revision, errors)]
        )
    try:
        prs = Presentation(str(path))
    except Exception as exc:  # пакет открылся, python-pptx — нет
        return _finalize_repairable(
            [
                _package_blocker_issue(
                    run_id, deck_revision, [f"python-pptx failed to open: {exc}"]
                )
            ]
        )
    ctx = _Ctx(
        prs,
        run_id,
        deck_revision,
        config or default_audit_config(),
    )
    issues: list[AuditIssue] = []
    for slide_index, slide in enumerate(prs.slides):
        for rule in RULES:
            issues.extend(rule(slide, slide_index, ctx))
    dangling = _dangling_rel_targets(path)
    if dangling:
        issues.append(_dangling_rel_issue(run_id, deck_revision, dangling))
    return _finalize_repairable(issues)
