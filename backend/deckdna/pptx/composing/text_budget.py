"""Сколько текста реально влезает в слайд шаблона — до того, как его писать.

Модель, переписывающая текст под эталон (``layout_fit``), раньше получала
только косвенный сигнал — среднюю длину демо-подписей шаблона. Итог живых
прогонов: заголовки-выводы на три-четыре строки, ужатые до 8 pt карточки и
переносы посреди фразы. Здесь бюджет считается из геометрии, той же
метрикой, что у аудита (``_wrap_metrics``):

* ``title_max_chars`` — сколько символов заголовка помещается по ширине
  рамки заголовка при его собственном кегле в две строки (кегль не
  уменьшается — заголовок остаётся заголовком);
* ``bullet_max_chars`` — сколько символов одного пункта помещается в
  типичный слот при читаемом кегле (``READABLE_BODY_PT``) в 85 % высоты;
  для карточек «подпись + текст» — сумма обоих слотов карточки.

Оценка консервативна (метрики DejaVu шире большинства шрифтов шаблонов);
reflow после заливки обычно только добавляет места.
"""

from __future__ import annotations

from statistics import median

from lxml import etree

from deckdna.audit.basic import LINE_HEIGHT_FACTOR, _wrap_metrics
from deckdna.errors import DeckDNAError
from deckdna.pptx.composing.text_replace import (
    _body_font_size_pt,
    _body_is_decorative,
    _is_title_run,
    _required_height_emu,
    _shape_id,
    _tx_body_of,
    _usable_box_emu,
)

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
EMU_PER_PT = 12700

READABLE_BODY_PT = 12.0
TITLE_MAX_LINES = 2
_HEIGHT_RESERVE = 0.85
_MIN_BULLET_CHARS = 40
_MAX_BULLET_CHARS = 220
_MIN_TITLE_CHARS = 24
_MAX_TITLE_CHARS = 90
# типичная деловая русская фраза: средняя длина слова ~7 букв
_SAMPLE = (
    "Участники ролевой игры обсуждают решения фракций и получают опыт "
    "переговоров при ограниченном времени и неполной информации о целях "
    "других команд проекта для развития навыков системного мышления "
) * 4


def _lines_for(n_chars: int, size_pt: float, width: int) -> int:
    text = _SAMPLE[:n_chars].rstrip()
    lines, widest = _wrap_metrics(text, size_pt, width)
    return lines if widest <= width else 10**6


def body_chars_that_fit(tx_body: etree._Element, width: int, height: int, size_pt: float) -> int:
    """Как ``chars_that_fit``, но с интервалами и отступами абзацев самой
    рамки (``lnSpc`` 140% у карточек VK Tech съедает 12% высоты)."""
    if width <= 0 or height <= 0:
        return 0
    lo, hi = 0, len(_SAMPLE)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        need, widest = _required_height_emu(tx_body, _SAMPLE[:mid].rstrip(), size_pt, width)
        if need <= height and widest <= width:
            lo = mid
        else:
            hi = mid - 1
    return lo


def chars_that_fit(width: int, height: int, size_pt: float, max_lines: int | None = None) -> int:
    """Максимум символов типичной фразы, помещающихся в рамку *width*×*height*."""
    if width <= 0 or height <= 0 or size_pt <= 0:
        return 0
    line_h = size_pt * LINE_HEIGHT_FACTOR * EMU_PER_PT
    lines_allowed = int(height // line_h)
    if max_lines is not None:
        lines_allowed = min(lines_allowed, max_lines)
    if lines_allowed < 1:
        return 0
    lo, hi = 0, len(_SAMPLE)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _lines_for(mid, size_pt, width) <= lines_allowed:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _box(tx_body: etree._Element, hints: dict[str, dict]) -> tuple[int, int] | None:
    box = _usable_box_emu(tx_body)
    if box is not None and min(box) > 0:
        return box
    hint = hints.get(_shape_id(tx_body) or "", {})
    if "w" in hint and "h" in hint:
        return int(hint["w"]), int(hint["h"])
    return None


def _size(tx_body: etree._Element, hints: dict[str, dict]) -> float | None:
    return _body_font_size_pt(tx_body) or hints.get(_shape_id(tx_body) or "", {}).get("sz")


def slide_text_budget(
    slide_xml: bytes,
    hints: dict[str, dict] | None = None,
    slots_per_card: int = 1,
) -> dict:
    """``{"title_max_chars", "bullet_max_chars", "card_headings"}`` для эталона.

    *hints* — метрики фигур с наследованием плейсхолдеров
    (``minimal._exemplar_shape_metrics``) для рамок без своих ``xfrm``/``sz``.
    """
    hints = hints or {}
    root = etree.fromstring(slide_xml)
    title_body: etree._Element | None = None
    slot_bodies: dict[int, etree._Element] = {}
    for el in root.iter(f"{{{A}}}t"):
        if not (el.text or "").strip():
            continue
        if any(etree.QName(a).localname == "tbl" for a in el.iterancestors()):
            continue
        tx_body = _tx_body_of(el)
        if tx_body is None or _body_is_decorative(tx_body):
            continue
        if _is_title_run(el):
            title_body = title_body if title_body is not None else tx_body
            continue
        slot_bodies.setdefault(id(tx_body), tx_body)

    title_max: int | None = None
    if title_body is not None and (box := _box(title_body, hints)):
        size = _size(title_body, hints) or 28.0
        # две строки при своём кегле, даже если рамка нарисована под одну:
        # шаблоны подписывают заголовок «в одну или в две строчки», а рамка
        # без автоподбора растёт вниз, в поле над контентом
        title_max = chars_that_fit(box[0], 10**9, size, TITLE_MAX_LINES)
        title_max = max(_MIN_TITLE_CHARS, min(_MAX_TITLE_CHARS, title_max))

    budgets: list[int] = []
    for tx_body in slot_bodies.values():
        box = _box(tx_body, hints)
        if box is None:
            continue
        size = max(READABLE_BODY_PT, _size(tx_body, hints) or READABLE_BODY_PT)
        size = min(size, 18.0)
        budgets.append(body_chars_that_fit(tx_body, box[0], int(box[1] * _HEIGHT_RESERVE), size))
    bullet_max: int | None = None
    if budgets:
        budgets.sort()
        if slots_per_card == 2 and len(budgets) >= 2:
            # подпись карточки (меньший слот) + её текст (больший)
            half = len(budgets) // 2
            bullet_max = int(median(budgets[:half]) + median(budgets[half:]))
        else:
            bullet_max = int(median(budgets))
        bullet_max = max(_MIN_BULLET_CHARS, min(_MAX_BULLET_CHARS, bullet_max))
    return {
        "title_max_chars": title_max,
        "bullet_max_chars": bullet_max,
        # у карточки строка заголовка и строка текста: пункт пишется как
        # «Заголовок: фраза», иначе строка заголовка пустует
        "card_headings": slots_per_card == 2,
    }


_ART_MIN_AREA = 0.5  # картинка макета/мастера не меньше половины слайда
LAYOUT_ART_THRESHOLD = 0.04  # доля контурных пикселей в зоне контента


def layout_art_ratio(slide, sw: int, sh: int, cache: dict | None = None) -> float:
    """Доля зоны контента с контурами графики, «запечённой» в фоновую
    картинку макета/мастера (подложки карточек, номера «01–04», столбики).

    Такую графику нельзя перестроить: пустая карточка на картинке остаётся
    пустой, клонированная — ляжет мимо. Мерится плотностью контуров, а не
    яркостью: плавный градиент фона контуров не даёт, светлые карточки на
    светлом фоне — дают. Плоский фон-картинка ≈ 0."""
    from io import BytesIO

    from PIL import Image, ImageFilter

    cache = {} if cache is None else cache
    ratio = 0.0
    try:
        owners = (slide.slide_layout, slide.slide_layout.slide_master)
    except KeyError as exc:
        # A slide/layout with no resolvable slideLayout/slideMaster
        # relationship is a corrupt OPC package, not a text-budget
        # concern -- python-pptx's own KeyError here read as an unrelated
        # internal_error otherwise (found live, 29.09: a template with its
        # slideLayout rels stripped crashed deep in this call instead of
        # surfacing package_corrupt).
        raise DeckDNAError(
            code="package_corrupt",
            message=f"slide has no resolvable slideLayout/slideMaster relationship: {exc}",
            stage="composing.deck",
        ) from exc
    for owner in owners:
        key = str(owner.part.partname)
        if key not in cache:
            value = 0.0
            for shape in owner.shapes:
                if shape.shape_type != 13 or not shape.width or not shape.height:
                    continue  # только картинки (MSO_SHAPE_TYPE.PICTURE)
                if shape.width * shape.height < _ART_MIN_AREA * sw * sh:
                    continue
                try:
                    img = Image.open(BytesIO(shape.image.blob)).convert("L").resize((320, 180))
                except Exception:  # noqa: BLE001, S112 — битая/векторная картинка
                    continue
                # зона контента: ниже заголовка, внутри полей
                edges = img.filter(ImageFilter.FIND_EDGES).crop((10, 45, 310, 165))
                hist = edges.histogram()
                value = max(value, sum(hist[9:]) / max(1, sum(hist)))
            cache[key] = value
        ratio = max(ratio, cache[key])
    return ratio


def template_budgets(template_path) -> dict[str, dict]:
    """Бюджеты всех слайдов шаблона: ``{slide_part: slide_text_budget(...)}``.

    Один проход python-pptx на шаблон — ради метрик плейсхолдеров
    (наследование кегля и рамки от макета/мастера)."""
    from pptx import Presentation

    from deckdna.pptx.composing.card_reflow import card_slots
    from deckdna.pptx.composing.minimal import _exemplar_shape_metrics

    prs = Presentation(str(template_path))
    sw, sh = int(prs.slide_width), int(prs.slide_height)
    out: dict[str, dict] = {}
    art_cache: dict[str, float] = {}
    for slide in prs.slides:
        part = str(slide.part.partname).lstrip("/")
        xml = slide.part.blob
        try:
            _, per_card = card_slots(xml, sw, sh)
        except Exception:  # noqa: BLE001 — нестандартная разметка = без карточек
            per_card = 0
        budget = slide_text_budget(xml, _exemplar_shape_metrics(prs, part), per_card)
        # графика макета под контентом: слайд годится, только если пунктов
        # ровно столько, сколько мест (layout_fit._fits_rigid)
        budget["layout_art"] = layout_art_ratio(slide, sw, sh, art_cache) >= LAYOUT_ART_THRESHOLD
        out[part] = budget
    return out
