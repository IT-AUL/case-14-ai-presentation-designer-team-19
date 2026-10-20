"""Старая презентация как контент: ``.pptx``/``.potx`` → ContentPack (ADR-032 §2).

Сценарий жюри «переверстай нашу старую презентацию в новый шаблон»: контент
приходит не текстом, а колодой. Разбор по слайдам:

* слайд → раздел; заголовок слайда (плейсхолдер заголовка, иначе верхний
  крупный текст) → заголовок раздела, ``source_ref.page`` = номер слайда;
* текстовые фигуры (в том числе внутри групп) по порядку чтения: несколько
  абзацев → список, один → абзац; номер слайда, дата и колонтитул не текст;
* нативные таблицы → ``tables`` + ``table_ref``;
* нативные графики → ``charts`` (категории и серии из chart XML) + ``chart_ref``;
* картинки → ``assets`` + ``image_ref`` (байты остаются в пакете:
  ``<artifact>#ppt/media/imageN.png``, как у docx); мелкие значки и логотипы
  (меньше ``_MIN_PICTURE_SHARE`` площади слайда) и повторы одной картинки на
  нескольких слайдах не контент;
* заметки докладчика → блок ``note``.

Первый слайд с заголовком — обложка: его заголовок становится
``title_hint``, а не разделом, если на нём больше ничего нет.
"""

from __future__ import annotations

from io import BytesIO

from deckdna.contracts.content_pack import (
    Asset,
    Block,
    Chart,
    ChartSeries,
    ContentPack,
    Kind,
    Kind1,
    Section,
    SourceRef,
    Table,
)
from deckdna.errors import DeckDNAError

_MIN_PICTURE_SHARE = 0.03
_SERVICE_PH = {"sldNum", "dt", "ftr", "hdr"}
_TITLE_PH = {"title", "ctrTitle"}
_SUBTITLE_PH = {"subTitle"}


# ``source_ref.range`` блоков из презентации: содержимое слайда или заметки
# докладчика. Заметки не становятся текстом слайда — планировщик кладёт их в
# заметки (``SlidePlan.speaker_note``), а пересборка структуры моделью
# (``restructure``) колоду, уже разбитую на слайды, не перекраивает.
SLIDE_RANGE = "slide"
NOTES_RANGE = "speaker_notes"


def _src(
    artifact_id: str, page: int, heading: str | None, range_: str = SLIDE_RANGE
) -> SourceRef:
    return SourceRef(
        artifact_id=artifact_id, page=page, range=range_,
        heading_path=[heading] if heading else None,
    )


def _ph_type(shape) -> str | None:
    if not getattr(shape, "is_placeholder", False):
        return None
    ph = shape._element.find(
        ".//{http://schemas.openxmlformats.org/presentationml/2006/main}ph"
    )
    if ph is None:
        return None
    return ph.get("type") or "body"


def _walk(shapes):
    """Фигуры в порядке чтения (сверху вниз, слева направо), группы раскрыты."""
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    flat = []
    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            flat.extend(_walk(shape.shapes))
        else:
            flat.append(shape)
    return sorted(flat, key=lambda s: ((s.top or 0) // 91440, s.left or 0))


def _paragraphs(shape) -> list[str]:
    return [
        " ".join(p.text.split())
        for p in shape.text_frame.paragraphs
        if p.text and p.text.strip()
    ]


def _cell(text: str) -> str | float | None:
    value = text.strip()
    if not value:
        return None
    try:
        return float(value.replace(" ", "").replace(",", "."))
    except ValueError:
        return value


def _chart_of(shape, chart_id: str, src: SourceRef) -> Chart | None:
    try:
        chart = shape.chart
        plot = chart.plots[0]
        categories = [str(c) for c in plot.categories]
        series = [
            ChartSeries(
                name=s.name or None,
                values=[float(v) if v is not None else 0.0 for v in s.values],
            )
            for s in plot.series
        ]
    except Exception:  # noqa: BLE001 — нестандартный график: не данные
        return None
    series = [s for s in series if len(s.values) == len(categories)]
    if not categories or not series:
        return None
    return Chart(id=chart_id, categories=categories, series=series, source_ref=src)


def parse_pptx(
    data: bytes, artifact_id: str = "input.pptx", language: str | None = None
) -> ContentPack:
    """PPTX/POTX → ContentPack (см. модульный docstring)."""
    from pptx import Presentation

    from deckdna.ingestion.content_parsers import SCHEMA_VERSION, _pack_id, detect_language
    from deckdna.pptx.opc.normalize import normalize_template_bytes

    try:
        prs = Presentation(BytesIO(normalize_template_bytes(data)))
    except Exception as exc:  # noqa: BLE001 — битый пакет: typed отказ
        raise DeckDNAError(
            code="invalid_input",
            message=f"{artifact_id}: not a readable PowerPoint file",
            stage="content_ingestion",
            details={"artifact_id": artifact_id},
        ) from exc
    slide_area = int(prs.slide_width) * int(prs.slide_height)
    sections: list[Section] = []
    tables: list[Table] = []
    charts: list[Chart] = []
    assets: list[Asset] = []
    seen_media: set[str] = set()
    text_parts: list[str] = []
    title_hint: str | None = None

    for number, slide in enumerate(prs.slides, start=1):
        shapes = _walk(slide.shapes)
        heading = ""
        for shape in shapes:
            if (
                _ph_type(shape) in _TITLE_PH
                and shape.has_text_frame
                and shape.text_frame.text.strip()
            ):
                heading = " ".join(shape.text_frame.text.split())
                break
        blocks: list[Block] = []
        subtitle: str | None = None
        for shape in shapes:
            kind = _ph_type(shape)
            if kind in _SERVICE_PH:
                continue
            src = _src(artifact_id, number, heading or None)
            if getattr(shape, "has_table", False) and shape.has_table:
                rows = [[_cell(c.text) for c in row.cells] for row in shape.table.rows]
                if not rows:
                    continue
                table_id = f"tbl-{len(tables)}"
                tables.append(Table(
                    id=table_id,
                    headers=[str(h) if h is not None else "" for h in rows[0]],
                    rows=rows[1:],
                    source_ref=src,
                ))
                blocks.append(Block(kind=Kind.table_ref, text=table_id, source_ref=src))
                text_parts.extend(str(h) for h in rows[0] if h)
                continue
            if getattr(shape, "has_chart", False) and shape.has_chart:
                chart = _chart_of(shape, f"chart-{len(charts)}", src)
                if chart is not None:
                    charts.append(chart)
                    blocks.append(Block(kind=Kind.chart_ref, text=chart.id, source_ref=src))
                continue
            if shape.shape_type == 13 or (kind in ("pic",) and hasattr(shape, "image")):
                try:
                    image = shape.image
                    rid = shape._element.blip_rId  # noqa: SLF001
                    part = str(shape.part.related_part(rid).partname).lstrip("/")
                except Exception:  # noqa: BLE001, S112 — внешняя/битая картинка
                    continue
                area = int(shape.width or 0) * int(shape.height or 0)
                if area < _MIN_PICTURE_SHARE * slide_area or part in seen_media:
                    continue
                seen_media.add(part)
                ref = f"{artifact_id}#{part}"
                try:
                    width_px, height_px = image.size
                except Exception:  # noqa: BLE001
                    width_px = height_px = None
                alt = shape._element.find(  # noqa: SLF001
                    ".//{http://schemas.openxmlformats.org/presentationml/2006/main}cNvPr"
                )
                assets.append(Asset(
                    id=f"img-{len(assets)}",
                    kind=Kind1.image,
                    artifact_id=ref,
                    alt=(alt.get("descr") if alt is not None else None) or None,
                    width_px=width_px,
                    height_px=height_px,
                ))
                blocks.append(Block(kind=Kind.image_ref, text=ref, source_ref=src))
                continue
            if not shape.has_text_frame or kind in _TITLE_PH:
                continue
            paragraphs = _paragraphs(shape)
            if not paragraphs:
                continue
            if kind in _SUBTITLE_PH and subtitle is None:
                subtitle = " ".join(paragraphs)
            text_parts.extend(paragraphs)
            if len(paragraphs) == 1:
                blocks.append(Block(kind=Kind.paragraph, text=paragraphs[0], source_ref=src))
            else:
                blocks.append(Block(kind=Kind.list, items=paragraphs, source_ref=src))
        if slide.has_notes_slide:
            note = " ".join(slide.notes_slide.notes_text_frame.text.split()) if (
                slide.notes_slide.notes_text_frame is not None
            ) else ""
            if note:
                blocks.append(Block(
                    kind=Kind.note, text=note,
                    source_ref=_src(artifact_id, number, heading or None, NOTES_RANGE),
                ))
        if heading:
            text_parts.append(heading)
        # обложка: заголовок и разве что подзаголовок — название колоды
        content_blocks = [
            b for b in blocks
            if b.kind != Kind.note and not (subtitle and b.text == subtitle)
        ]
        if title_hint is None and heading and not content_blocks and number == 1:
            title_hint = heading
            continue
        if not blocks and not heading:
            continue
        sections.append(Section(
            id=f"sec-{len(sections)}",
            heading=heading or f"Слайд {number}",
            level=1,
            blocks=blocks,
        ))

    payload = "\n".join(text_parts)
    return ContentPack(
        schema_version=SCHEMA_VERSION,
        id=_pack_id(payload or artifact_id),
        language=language or detect_language(payload),
        title_hint=title_hint,
        sections=sections,
        tables=tables,
        charts=charts,
        assets=assets,
        warnings=[],
    )


def is_speaker_note(block: Block) -> bool:
    return block.kind == Kind.note and block.source_ref.range == NOTES_RANGE


def is_slide_deck(pack: ContentPack) -> bool:
    """Пак из презентации: разделы — это уже слайды автора."""
    blocks = [b for s in pack.sections for b in s.blocks]
    return bool(blocks) and all(b.source_ref.range in (SLIDE_RANGE, NOTES_RANGE) for b in blocks)


__all__ = ["NOTES_RANGE", "SLIDE_RANGE", "is_slide_deck", "is_speaker_note", "parse_pptx"]
