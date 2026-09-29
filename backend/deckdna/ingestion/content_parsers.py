"""Content Ingestion v0 — нормализация входа в ContentPack.

Поддерживаемые форматы (минимальный срез):
- JSON — почти 1-в-1 на contracts/content_pack.py, валидация контрактом;
- Markdown — заголовки по уровням -> sections (+ heading_path), параграфы/
  списки/цитаты/код/картинки -> blocks, GFM-таблицы -> tables
  (структура Table: headers + rows, числа — float, пустые ячейки — None);
- TXT — одна секция с единственным paragraph-блоком;
- PDF — страница -> секция («Page N»), абзацы -> paragraph-блоки,
  pdfplumber-таблицы -> tables + table_ref (структура как у markdown);
- XLSX — лист с данными -> секция + Table + table_ref (пустые листы и
  листы без данных пропускаются).

Изображения: markdown (`![alt](src)`) и docx (image-relationships)
извлекаются в `assets` — метаданные (alt, px-размеры, source_url) и
детерминированная ссылка на источник байтов (src / `doc.docx#word/media/…`);
сами байты в ContentPack не хранятся — asset-стора в контракте пока нет.

Построение EvidenceGraph — следующая итерация; блоки уже несут
source_ref, поэтому к ним можно будет привязать узлы графа.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import ValidationError

from deckdna.contracts.content_pack import (
    Asset,
    Block,
    Chart,
    ContentPack,
    Diagram,
    Kind,
    Kind1,
    Section,
    SourceRef,
    Table,
    Warning,
)
from deckdna.errors import DeckDNAError
from deckdna.ingestion.chart_block import parse_chart_block
from deckdna.ingestion.diagram_block import parse_diagram_block
from deckdna.planning.protected_content import display_math

PARSER_VERSION = "content-ingestion/0.1.0"
SCHEMA_VERSION = "1.0"

_CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")
_FOOTNOTE_MARKER_RE = re.compile(r"\[\^[^\]]+\]")


def _display_inline(token, *, reference: bool = False) -> str:
    """Slide-ready visible text from a Markdown inline token.

    The source remains in ContentPack provenance. Markdown link syntax,
    emphasis and unresolvable footnote markers are not presentation copy.
    """
    raw = _FOOTNOTE_MARKER_RE.sub("", token.content or "").strip()
    if reference:
        match = re.match(r"\[([^\]]+)\]\((https?://[^)]+)\)", raw)
        if match:
            host = urlparse(match.group(2)).hostname or ""
            host = host.removeprefix("www.")
            title = re.sub(r"\s+", " ", match.group(1)).strip()
            if len(title) > 65:
                title = title[:62].rsplit(" ", 1)[0].rstrip(" ,;:—-") + "…"
            return f"{title} — {host}" if host else title
    parts = []
    for child in token.children or []:
        if child.type in ("text", "code_inline"):
            parts.append(_FOOTNOTE_MARKER_RE.sub("", child.content))
        elif child.type in ("softbreak", "hardbreak"):
            parts.append(" ")
    text = re.sub(r"\s+", " ", "".join(parts) if parts else raw).strip()
    # An inline `\sigma` etc outside a protected \[...\]/$$...$$ block
    # (loose notation in running prose) still reads as a raw TeX command
    # otherwise -- display_math() is a no-op on text with no backslash.
    return display_math(text) if "\\" in text else text

# язык колоды по ТЗ русский; детект — грубая эвристика v0
def detect_language(text: str) -> str:
    return "ru" if _CYRILLIC_RE.search(text) else "en"


def _pack_id(payload: str) -> str:
    return f"pack-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:8]}"


def _src(artifact_id: str, heading_path: list[str] | None = None) -> SourceRef:
    return SourceRef(artifact_id=artifact_id, heading_path=heading_path or None)


def parse_json_content(
    data: str | bytes | dict[str, Any],
    artifact_id: str = "input.json",
) -> ContentPack:
    """JSON, совместимый с content-pack.schema.json, -> валидированный
    ContentPack. Невалидный вход — typed `invalid_input`, не exception."""
    if isinstance(data, (str, bytes)):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as exc:
            raise DeckDNAError(
                code="invalid_input",
                message=f"{artifact_id}: not valid JSON ({exc.msg})",
                stage="content_ingestion",
                details={"artifact_id": artifact_id},
            ) from exc
    try:
        return ContentPack.model_validate(data)
    except ValidationError as exc:
        raise DeckDNAError(
            code="invalid_input",
            message=f"{artifact_id}: does not match content-pack schema",
            stage="content_ingestion",
            details={"artifact_id": artifact_id, "errors": exc.errors()[:10]},
        ) from exc


class _MarkdownParser:
    """Потоковый разбор markdown-it токенов в Section/Block."""

    def __init__(self, artifact_id: str) -> None:
        self.artifact_id = artifact_id
        self.sections: list[Section] = []
        self.blocks: list[Block] = []
        self.heading_stack: list[tuple[int, str]] = []
        self.title_hint: str | None = None
        self.warnings: list[Warning] = []
        self.tables: list[Table] = []
        self.charts: list[Chart] = []
        self.diagrams: list[Diagram] = []
        self.assets: list[Asset] = []
        self._sec_idx = 0
        self._tbl_idx = 0
        self._chart_idx = 0
        self._diag_idx = 0
        self._img_idx = 0
        self._pending_heading = ""
        self._pending_level: int | None = None

    def _flush(self) -> None:
        """Закрывает текущую секцию. Пустая преамбула (ни заголовка, ни
        блоков) секцией не становится; секция-заголовок без блоков — валидна."""
        if not self.blocks and not self._pending_heading:
            return
        self.sections.append(
            Section(
                id=f"sec-{self._sec_idx}",
                heading=self._pending_heading,
                level=self._pending_level,
                blocks=self.blocks,
            )
        )
        self._sec_idx += 1
        self.blocks = []

    def _heading_path(self) -> list[str]:
        return [text for _, text in self.heading_stack]

    def parse(self, tokens) -> None:
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok.type == "heading_open":
                level = int(tok.tag[1])
                text = tokens[i + 1].content.strip() if i + 1 < len(tokens) else ""
                if self.title_hint is None:
                    self.title_hint = text
                while self.heading_stack and self.heading_stack[-1][0] >= level:
                    self.heading_stack.pop()
                self.heading_stack.append((level, text))
                self._flush()  # заголовок закрывает предыдущую секцию
                self._pending_heading = text
                self._pending_level = level
                i += 3
                continue
            if tok.type == "paragraph_open" and i + 1 < len(tokens):
                inline = tokens[i + 1]
                image_kinds = {"image"}
                child_types = {c.type for c in (inline.children or [])}
                if child_types & image_kinds:
                    for child in inline.children or []:
                        if child.type == "image":
                            attrs = child.attrs or {}
                            src = attrs.get("src") or ""
                            alt = child.content or attrs.get("alt")
                            self.blocks.append(
                                Block(
                                    kind=Kind.image_ref,
                                    text=alt,
                                    source_ref=_src(self.artifact_id, self._heading_path()),
                                )
                            )
                            self.assets.append(
                                Asset(
                                    id=f"img-{self._img_idx}",
                                    kind=Kind1.image,
                                    artifact_id=src or self.artifact_id,
                                    alt=alt or None,
                                    source_url=(
                                        src if src.startswith(("http://", "https://")) else None
                                    ),
                                )
                            )
                            self._img_idx += 1
                elif _display_inline(inline):
                    self.blocks.append(
                        Block(
                            kind=Kind.paragraph,
                            text=_display_inline(inline),
                            source_ref=_src(self.artifact_id, self._heading_path()),
                        )
                    )
                i += 3
                continue
            if tok.type in ("bullet_list_open", "ordered_list_open"):
                items: list[str] = []
                i += 1
                while i < len(tokens) and tokens[i].type != (
                    "bullet_list_close" if tok.type == "bullet_list_open" else "ordered_list_close"
                ):
                    if tokens[i].type == "inline" and _display_inline(tokens[i]):
                        items.append(_display_inline(
                            tokens[i],
                            reference=self._pending_heading.casefold() in {
                                "references", "источники", "литература"
                            },
                        ))
                    i += 1
                if items:
                    self.blocks.append(
                        Block(
                            kind=Kind.list,
                            items=items,
                            source_ref=_src(self.artifact_id, self._heading_path()),
                        )
                    )
                i += 1
                continue
            if tok.type == "blockquote_open":
                texts: list[str] = []
                i += 1
                while i < len(tokens) and tokens[i].type != "blockquote_close":
                    if tokens[i].type == "inline" and _display_inline(tokens[i]):
                        texts.append(_display_inline(tokens[i]))
                    i += 1
                if texts:
                    self.blocks.append(
                        Block(
                            kind=Kind.quote,
                            text="\n".join(texts),
                            source_ref=_src(self.artifact_id, self._heading_path()),
                        )
                    )
                i += 1
                continue
            if tok.type == "table_open":
                headers: list[str] = []
                rows: list[list[str | float | None]] = []
                in_thead = False
                cur_row: list[str | float | None] = []
                i += 1
                while i < len(tokens) and tokens[i].type != "table_close":
                    t = tokens[i]
                    if t.type == "thead_open":
                        in_thead = True
                    elif t.type == "thead_close":
                        in_thead = False
                    elif t.type == "tr_open" and not in_thead:
                        cur_row = []
                    elif t.type == "tr_close" and not in_thead:
                        rows.append(cur_row)
                    elif t.type == "inline":
                        if in_thead:
                            headers.append(_display_inline(t))
                        else:
                            cur_row.append(_table_cell(_display_inline(t)))
                    i += 1
                table_id = f"tbl-{self._tbl_idx}"
                self.tables.append(
                    Table(
                        id=table_id,
                        headers=headers,
                        rows=rows,
                        source_ref=_src(self.artifact_id, self._heading_path()),
                    )
                )
                # Ссылка на таблицу остаётся в потоке блоков — иначе
                # извлечённая Table никогда не доходит до плана
                # (story_director маппит table_ref-блоки в Kind.table).
                self.blocks.append(
                    Block(
                        kind=Kind.table_ref,
                        text=table_id,
                        source_ref=_src(self.artifact_id, self._heading_path()),
                    )
                )
                self._tbl_idx += 1
                i += 1
                continue
            if tok.type in ("fence", "code_block"):
                # ```chart fence — структурный Chart (parse_chart_block),
                # не code-блок; иначе ContentPack.charts остаётся пустым
                # и chart-контент тихо теряется (та же дыра, что у
                # table_ref до её эмиссии).
                fenced = (
                    "```" + (tok.info or "") + "\n" + tok.content + "```"
                    if tok.type == "fence"
                    else tok.content
                )
                chart = parse_chart_block(
                    fenced, _src(self.artifact_id, self._heading_path())
                )
                diagram = (
                    None
                    if chart is not None
                    else parse_diagram_block(
                        fenced, _src(self.artifact_id, self._heading_path())
                    )
                )
                if chart is not None:
                    if chart.id == "chart-1":  # дефолт парсера — подставить сиквенс
                        chart.id = f"ch-{self._chart_idx}"
                    self.charts.append(chart)
                    # Ссылка на chart остаётся в потоке блоков — иначе
                    # извлечённый Chart не доходит до плана
                    # (story_director маппит chart_ref-блоки в Kind.chart).
                    self.blocks.append(
                        Block(
                            kind=Kind.chart_ref,
                            text=chart.id,
                            source_ref=_src(
                                self.artifact_id, self._heading_path()
                            ),
                        )
                    )
                    self._chart_idx += 1
                elif diagram is not None:
                    if diagram.id == "diagram-1":  # дефолт парсера — сиквенс
                        diagram.id = f"dg-{self._diag_idx}"
                    self.diagrams.append(diagram)
                    # Тот же мост что chart_ref/table_ref: без ref-блока
                    # Diagram не доходит до плана (Kind.diagram unit).
                    self.blocks.append(
                        Block(
                            kind=Kind.diagram_ref,
                            text=diagram.id,
                            source_ref=_src(
                                self.artifact_id, self._heading_path()
                            ),
                        )
                    )
                    self._diag_idx += 1
                elif tok.content.strip():
                    self.blocks.append(
                        Block(
                            kind=Kind.code,
                            text=(
                                display_math(tok.content)
                                if tok.info.strip() in {"math", "latex", "tex"}
                                else tok.content.strip()
                            ),
                            source_ref=_src(self.artifact_id, self._heading_path()),
                        )
                    )
                i += 1
                continue
            i += 1
        self._flush()


def _table_cell(text: str) -> str | float | None:
    """Ячейка таблицы: пустая -> None, чистое число -> float, остальное -> str."""
    cell = text.strip()
    if not cell:
        return None
    try:
        value = float(cell)
    except ValueError:
        return cell
    return value if math.isfinite(value) else cell


def parse_markdown(
    text: str,
    artifact_id: str = "input.md",
    language: str | None = None,
) -> ContentPack:
    """Markdown -> ContentPack: заголовки → sections (heading_path
    сохраняется в source_ref каждого блока), параграфы/списки/цитаты/
    код/картинки → blocks соответствующих kind."""
    from markdown_it import MarkdownIt

    parser = _MarkdownParser(artifact_id)
    # "default" preset включает table/strikethrough; commonmark (без аргумента)
    # таблицы не распознаёт и отдавал бы их обычным текстом.
    # Protect display equations before Markdown escapes consume \[ / \]
    # or emphasis consumes variable underscores. Keep them as code blocks:
    # the existing contract carries their exact visible mathematical text.
    protected = re.sub(
        r"(?ms)^\s*(?:\\\[|\$\$)\s*\n?(.*?)\n?\s*(?:\\\]|\$\$)\s*$",
        lambda m: "\n```math\n" + m.group(1).strip() + "\n```\n",
        text,
    )
    parser.parse(MarkdownIt("default").parse(protected))
    return ContentPack(
        schema_version=SCHEMA_VERSION,
        id=_pack_id(text),
        language=language or detect_language(text),
        title_hint=parser.title_hint,
        sections=parser.sections,
        tables=parser.tables,
        charts=parser.charts,
        diagrams=parser.diagrams,
        assets=parser.assets,
        warnings=parser.warnings,
    )


_DOCX_HEADING_RE = re.compile(r"heading\s*(\d+)", re.IGNORECASE)


def parse_docx(
    data: bytes,
    artifact_id: str = "input.docx",
    language: str | None = None,
) -> ContentPack:
    """DOCX -> ContentPack: Heading-стили -> sections (+ heading_path),
    абзацы -> paragraph-блоки, List-стили/numPr -> list-блоки,
    docx-таблицы -> tables (та же структура Table, что в markdown)."""
    import docx
    from docx.oxml.ns import qn
    from docx.table import Table as _DocxTable
    from docx.text.paragraph import Paragraph as _DocxParagraph

    document = docx.Document(BytesIO(data))

    # rId -> artifact_id ассета (та же детерминированная ссылка, что в
    # Asset.artifact_id ниже: `doc.docx#word/media/imageN.png` или URL
    # для внешних rels). Строится до обхода блоков — inline-картинки
    # параграфов ссылаются на неё через image_ref-блоки.
    rid_to_ref: dict[str, str] = {}
    for r_id, rel in sorted(document.part.rels.items()):
        if "image" not in rel.reltype:
            continue
        rid_to_ref[r_id] = (
            rel.target_ref
            if rel.is_external
            else f"{artifact_id}#{str(rel.target_part.partname).lstrip('/')}"
        )

    sections: list[Section] = []
    blocks: list[Block] = []
    tables: list[Table] = []
    assets: list[Asset] = []
    heading_stack: list[tuple[int, str]] = []
    title_hint: str | None = None
    pending_heading = ""
    pending_level: int | None = None
    sec_idx = 0
    tbl_idx = 0
    list_buf: list[str] = []
    text_parts: list[str] = []  # для language-детекта и pack-id

    def heading_path() -> list[str]:
        return [text for _, text in heading_stack]

    def flush_list() -> None:
        nonlocal list_buf
        if list_buf:
            blocks.append(
                Block(
                    kind=Kind.list,
                    items=list_buf,
                    source_ref=_src(artifact_id, heading_path()),
                )
            )
            list_buf = []

    def flush_section() -> None:
        nonlocal blocks, sec_idx
        flush_list()
        if not blocks and not pending_heading:
            return
        sections.append(
            Section(
                id=f"sec-{sec_idx}",
                heading=pending_heading,
                level=pending_level,
                blocks=blocks,
            )
        )
        sec_idx += 1
        blocks = []

    for element in document.iter_inner_content():
        if isinstance(element, _DocxParagraph):
            text = element.text.strip()
            style_name = element.style.name if element.style is not None else ""
            heading_match = _DOCX_HEADING_RE.match(style_name)
            if heading_match:
                level = int(heading_match.group(1))
                if title_hint is None:
                    title_hint = text
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, text))
                flush_section()
                pending_heading = text
                pending_level = level
                continue
            is_list = style_name.startswith("List") or (
                element._p.pPr is not None and element._p.pPr.numPr is not None
            )
            if is_list:
                if text:
                    list_buf.append(text)
                    text_parts.append(text)
                continue
            flush_list()
            if text:
                blocks.append(
                    Block(
                        kind=Kind.paragraph,
                        text=text,
                        source_ref=_src(artifact_id, heading_path()),
                    )
                )
                text_parts.append(text)
            # Inline-картинки параграфа -> image_ref-блоки в потоке
            # (тот же мост что table_ref/chart_ref: без блока Asset не
            # доходит до плана — story_director маппит в Kind.image).
            for blip in element._p.iter(qn("a:blip")):
                ref = rid_to_ref.get(blip.get(qn("r:embed")))
                if ref:
                    blocks.append(
                        Block(
                            kind=Kind.image_ref,
                            text=ref,
                            source_ref=_src(artifact_id, heading_path()),
                        )
                    )
        elif isinstance(element, _DocxTable):
            flush_list()
            rows = [
                [_table_cell(cell.text) for cell in row.cells]
                for row in element.rows
            ]
            headers = [str(h) if h is not None else "" for h in (rows[0] if rows else [])]
            tables.append(
                Table(
                    id=f"tbl-{tbl_idx}",
                    headers=headers,
                    rows=rows[1:],
                    source_ref=_src(artifact_id, heading_path()),
                )
            )
            tbl_idx += 1
            text_parts.extend(headers)
    flush_section()

    # Изображения: все image-relationships документа -> Asset. Байты
    # живут внутри .docx-пакета — artifact_id ссылается на part
    # (`doc.docx#word/media/image1.png`); alt — из docPr descr inline
    # shapes, pixel-размеры — из заголовков самого растра.
    rid_to_alt: dict[str, str] = {}
    for shape in document.inline_shapes:
        try:
            blip = shape._inline.graphic.graphicData.pic.blipFill.blip  # noqa: SLF001
            descr = shape._inline.docPr.get("descr")  # noqa: SLF001
        except AttributeError:
            continue
        if blip is not None and blip.embed and descr:
            rid_to_alt[blip.embed] = descr
    img_idx = 0
    for r_id, rel in sorted(document.part.rels.items()):
        if "image" not in rel.reltype:
            continue
        artifact_ref = rid_to_ref[r_id]
        if rel.is_external:
            width_px = height_px = None
        else:
            part = rel.target_part
            try:
                width_px, height_px = part.image.px_width, part.image.px_height
            except Exception:  # неизвестный формат растра — честно без размеров
                width_px = height_px = None
        assets.append(
            Asset(
                id=f"img-{img_idx}",
                kind=Kind1.image,
                artifact_id=artifact_ref,
                alt=rid_to_alt.get(r_id) or None,
                width_px=width_px,
                height_px=height_px,
            )
        )
        img_idx += 1

    payload = "\n".join(text_parts)
    return ContentPack(
        schema_version=SCHEMA_VERSION,
        id=_pack_id(payload),
        language=language or detect_language(payload),
        title_hint=title_hint,
        sections=sections,
        tables=tables,
        assets=assets,
        warnings=[],
    )


_PDF_SLIDE_PREFIX = re.compile(r"^(?:слайд|slide)\s*\d+\s*[.:)—–-]?\s*", re.I)
_PDF_SENTENCE_END = re.compile(r"[.!?…:;»\")]$")
_PDF_LABEL = re.compile(r"^[^\W\d][\w\s-]{1,40}?:\s+\S")
_PDF_NUMBERED = re.compile(r"^\s*(?:\d+[.)]|[•●▪–—-])\s+")


def _pdf_lines(
    data: bytes, skip: dict[int, list[tuple[float, float, float, float]]] | None = None
) -> list[tuple[int, float, float, str]]:
    """(страница, кегль, y, текст) всех строк PDF в порядке чтения.

    В PDF блоки лежат не по порядку чтения (заголовки страницы часто в
    конце потока), поэтому строки сортируются по положению на странице."""
    import pymupdf

    out: list[tuple[int, float, float, str]] = []
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        for page_num, page in enumerate(doc, start=1):
            rows = []
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    spans = line.get("spans") or []
                    text = "".join(sp.get("text", "") for sp in spans).strip()
                    if not text:
                        continue
                    size = max(sp.get("size", 0.0) for sp in spans)
                    # Заголовок тем же кеглем, но целиком жирный («Проблема»
                    # в экспорте Google Docs): короткая строка без точки в
                    # конце считается на ступень крупнее — дальше её ловит
                    # общее правило «крупнее основного текста».
                    bold = all(
                        (sp.get("flags", 0) & 16) or "bold" in sp.get("font", "").lower()
                        for sp in spans
                        if sp.get("text", "").strip()
                    )
                    if bold and len(text) <= 80 and not text.endswith((".", ",", ";")):
                        size *= 1.12
                    x0, y0, x1, y1 = line["bbox"]
                    cy = (y0 + y1) / 2
                    # строки внутри таблиц уже есть в Table — не дублируем
                    if any(
                        bx0 - 1 <= x0 and x1 <= bx1 + 1 and by0 - 1 <= cy <= by1 + 1
                        for bx0, by0, bx1, by1 in (skip or {}).get(page_num, [])
                    ):
                        continue
                    rows.append((round(y0, 1), x0, size, text))
            rows.sort()
            out.extend((page_num, size, y, text) for y, _x, size, text in rows)
    finally:
        doc.close()
    return out


def _pdf_structure(lines: list[tuple[int, float, float, str]]):
    """Строки → [(заголовок, уровень, [(kind, text)])] по кеглю.

    Основной кегль — самый частый; строка заметно крупнее (≥1.08×) —
    заголовок (самый крупный — уровень 1). Переносы строк склеиваются:
    строка без знака конца предложения, за которой идёт строка со
    строчной буквы, — одно предложение. «Слайд N. Тема» → «Тема»."""
    from collections import Counter

    if not lines:
        return []
    body = Counter(round(sz, 1) for _, sz, _, _ in lines).most_common(1)[0][0]
    top = max(sz for _, sz, _, _ in lines)
    merged: list[list] = []  # [page, size, y, text, is_heading]
    for page, size, y, text in lines:
        heading = size >= body * 1.08
        prev = merged[-1] if merged else None
        if prev is not None and prev[0] == page and prev[4] == heading:
            same_size = abs(prev[1] - size) < 0.5
            wraps = (
                heading and same_size and y - prev[2] < size * 2.2
            ) or (
                not heading
                and not _PDF_SENTENCE_END.search(prev[3])
                and text[:1].islower()
            )
            if wraps:
                prev[3] = f"{prev[3]} {text}"
                prev[2] = y
                continue
        merged.append([page, size, y, text, heading])

    sections: list[tuple[str, int, list[tuple[str, str]]]] = []
    current: tuple[str, int, list[tuple[str, str]]] | None = None
    first_page: list[int] = []
    for page, size, _y, text, heading in merged:
        if heading:
            title = _PDF_SLIDE_PREFIX.sub("", text).strip()
            if current is not None and not current[2] and title and size >= top - 0.1:
                # «Слайд 1. Тема» + крупный заголовок под ним — один заголовок
                current = (title, 1, current[2])
                sections[-1] = current
                continue
            if current is not None and not current[2] and current[0] == (title or text):
                continue  # тот же заголовок повторён (колонтитул/обложка)
            current = (title or text, 1 if size >= top - 0.1 else 2, [])
            sections.append(current)
            first_page.append(page)
            continue
        if current is None:
            current = ("", 2, [])
            sections.append(current)
            first_page.append(page)
        item = _PDF_NUMBERED.sub("", text).strip()
        kind = "paragraph" if (_PDF_LABEL.match(text) or text.endswith(":")) else "item"
        current[2].append((kind, item))
    return [(t, lvl, items, pg) for (t, lvl, items), pg in zip(sections, first_page, strict=True)]


_MAX_TABLE_CELL_CHARS = 200


def _is_real_table(raw: list[list] | None) -> bool:
    """Таблица по форме данных, а не по линиям на странице: ≥2 колонок с
    данными, ≥2 строк и короткие ячейки. pdfplumber принимает за таблицу
    любую рамку — у экспорта Google Docs вся страница оказывалась одной
    «ячейкой» с абзацами, и текст документа пропадал из разбора."""
    if not raw or len(raw) < 2:
        return False
    cells = [str(c).strip() for row in raw for c in row if c is not None and str(c).strip()]
    if not cells:
        return False
    widths = [sum(1 for c in row if c is not None and str(c).strip()) for row in raw]
    if max(widths) < 2:
        return False
    return max(len(c) for c in cells) <= _MAX_TABLE_CELL_CHARS


def _tidy_raw_table(raw: list[list]) -> list[list]:
    """Переносы строк внутри ячейки — это перенос PDF, а не абзац; колонка,
    пустая во всех строках (артефакт разметки линий), удаляется."""
    cells = [
        [re.sub(r"\s*\n\s*", " ", str(c)).strip() if c is not None else None for c in row]
        for row in raw
    ]
    width = max(len(r) for r in cells)
    keep = [
        j for j in range(width)
        if any(j < len(r) and r[j] for r in cells)
    ]
    return [[r[j] if j < len(r) else None for j in keep] for r in cells]


_ACRONYM = re.compile(r"^[A-ZА-ЯЁ0-9]{2,5}$")


def _sentence_case(heading: str, proper: set[str], acronyms: frozenset[str] = frozenset()) -> str:
    """«ВАРИАНТ 1. ПИРАМИДА МИНТО» → «Вариант 1. Пирамида Минто».

    Только для заголовков капсом; аббревиатуры (*acronyms* — слова, которые и
    в тексте документа написаны заглавными: SCQA, VK) и слова,
    которые в тексте документа пишутся с заглавной посреди фразы (имена
    собственные — «Минто»), сохраняют заглавную."""
    letters = [c for c in heading if c.isalpha()]
    if len(letters) < 4 or sum(c.isupper() for c in letters) < 0.8 * len(letters):
        return heading
    out: list[str] = []
    start = True
    for token in re.split(r"(\s+)", heading.lower()):
        if not token.strip():
            out.append(token)
            continue
        core = re.sub(r"\W", "", token)
        original = next(
            (w for w in heading.split() if re.sub(r"\W", "", w).lower() == core), ""
        )
        if _ACRONYM.match(re.sub(r"\W", "", original)) and core.upper() in acronyms:
            token = token.upper()
        elif start or core in proper:
            token = token[:1].upper() + token[1:]
        out.append(token)
        start = token.rstrip().endswith((".", "!", "?", ":"))
    return "".join(out)


def _tidy_pdf_sections(
    sections: list[Section], title_hint: str | None, body_text: str
) -> tuple[list[Section], str | None]:
    """Разделы PDF-«лонгрида» после разбора по кеглю.

    Живой пример (29.09, методичка «Публичные выступления»): три пустых
    заголовка с обложки, заголовки капсом, подзаголовки «Назначение» /
    «Структура», повторённые в каждом варианте, и плашка «ОШИБКА» —
    каждый становился отдельным разделом, а значит слайдом без контекста.

    * пустые разделы до первого содержательного — обложка: самый длинный
      из них становится названием документа, в разделы они не идут;
    * заголовки капсом — в обычный регистр (``_sentence_case``);
    * повторяющийся заголовок или короткая плашка капсом (≤2 слова)
      вливается в предыдущий раздел как «Подзаголовок: текст»."""
    proper = {
        w.lower()
        for w in re.findall(r"(?<=[a-zа-яё,] )([A-ZА-ЯЁ][a-zа-яё]{2,})", body_text)
    }
    # аббревиатура — капс-слово, встречающееся капсом посреди обычной фразы
    acronyms = frozenset(
        re.findall(r"(?<=[a-zа-яё,] )([A-ZА-ЯЁ0-9]{2,5})(?=[\s,.;:)])", body_text)
    )
    cover: list[str] = []
    rest = list(sections)
    while rest and not rest[0].blocks:
        cover.append(rest.pop(0).heading)
    if cover:
        title_hint = max(cover, key=len)
    title_hint = _sentence_case(title_hint, proper, acronyms) if title_hint else title_hint

    counts: dict[str, int] = {}
    for s in rest:
        key = s.heading.strip().casefold()
        counts[key] = counts.get(key, 0) + 1

    out: list[Section] = []
    for s in rest:
        heading = s.heading.strip()
        words = heading.split()
        is_label = bool(out) and heading and (
            counts[heading.casefold()] > 1
            or (len(words) <= 2 and heading.isupper() and len(heading) <= 24)
        )
        if is_label and s.blocks:
            label = _sentence_case(heading, proper, acronyms).rstrip(":.")
            blocks = list(s.blocks)
            first = blocks[0]
            if first.kind == Kind.paragraph and first.text:
                blocks[0] = first.model_copy(update={"text": f"{label}: {first.text}"})
            else:
                blocks.insert(
                    0, Block(kind=Kind.paragraph, text=f"{label}:", source_ref=first.source_ref)
                )
            parent = out[-1]
            out[-1] = parent.model_copy(update={"blocks": parent.blocks + blocks})
            continue
        if not s.blocks and not heading:
            continue
        out.append(
            s.model_copy(
                update={
                    "id": f"sec-{len(out)}",
                    "heading": _sentence_case(heading, proper, acronyms),
                }
            )
        )
    return out, title_hint


def parse_pdf(
    data: bytes,
    artifact_id: str = "input.pdf",
    language: str | None = None,
) -> ContentPack:
    """PDF -> ContentPack с настоящей структурой документа.

    Заголовки — по кеглю строк (крупнее основного текста), «Слайд N. …»
    понимается как заголовок раздела; абзацы склеиваются из переносов;
    подряд идущие законченные строки — список, строки-выноски («Ключевая
    идея: …», «Пример: …») и вводные «…:» — абзацы. Таблицы —
    pdfplumber.extract_tables() -> tables + table_ref. Документ без
    выделенных заголовков разбивается по страницам («Page N»), как раньше."""
    import pdfplumber

    tables: list[Table] = []
    table_blocks: dict[int, list[Block]] = {}
    table_boxes: dict[int, list[tuple[float, float, float, float]]] = {}
    text_parts: list[str] = []
    tbl_idx = 0
    with pdfplumber.open(BytesIO(data)) as pdf:
        page_texts = [(page.extract_text() or "") for page in pdf.pages]
        for page_num, page in enumerate(pdf.pages, start=1):
            table_boxes[page_num] = []
            for found in page.find_tables():
                raw_table = found.extract()
                if not _is_real_table(raw_table):
                    # рамка страницы/врезки, распознанная как таблица: её
                    # текст остаётся в потоке строк, а не прячется в ячейку
                    continue
                table_boxes[page_num].append(found.bbox)
                raw_table = _tidy_raw_table(raw_table)
                headers = [str(h).strip() if h else "" for h in raw_table[0]]
                rows = [[_table_cell(cell or "") for cell in row] for row in raw_table[1:]]
                if tables and tables[-1].headers == headers and rows:
                    # та же шапка сразу после — продолжение таблицы, разрезанной
                    # границей страницы (или повтор шапки), а не новая таблица
                    tables[-1].rows.extend(rows)
                    continue
                table_id = f"tbl-{tbl_idx}"
                heading_path = [f"Page {page_num}"]
                tables.append(
                    Table(
                        id=table_id,
                        headers=headers,
                        rows=rows,
                        source_ref=_src(artifact_id, heading_path),
                    )
                )
                # table_ref-блок — иначе извлечённая Table не доходит
                # до плана (story_director маппит их в Kind.table).
                table_blocks.setdefault(page_num, []).append(
                    Block(
                        kind=Kind.table_ref,
                        text=table_id,
                        source_ref=_src(artifact_id, heading_path),
                    )
                )
                tbl_idx += 1
                text_parts.extend(headers)

    try:
        lines = _pdf_lines(data, table_boxes)
    except Exception:  # noqa: BLE001 — битая разметка → разбивка по страницам
        lines = []
    structure = _pdf_structure(lines)
    has_headings = any(title for title, _lvl, _blocks, _pg in structure)

    sections: list[Section] = []
    title_hint: str | None = None
    if has_headings:
        # таблица — в раздел, начавшийся на её странице или раньше
        attach: dict[int, list[Block]] = {}
        for page in sorted(table_blocks):
            idx = max(
                (i for i, st in enumerate(structure) if st[3] <= page), default=0
            )
            attach.setdefault(idx, []).extend(table_blocks[page])
        for s_idx, (title, level, items, _pg) in enumerate(structure):
            heading_path = [title] if title else []
            blocks: list[Block] = []
            pending: list[str] = []

            def flush(blocks=blocks, pending=pending, heading_path=heading_path):
                if len(pending) == 1:
                    blocks.append(
                        Block(
                            kind=Kind.paragraph,
                            text=pending[0],
                            source_ref=_src(artifact_id, heading_path),
                        )
                    )
                elif pending:
                    blocks.append(
                        Block(
                            kind=Kind.list,
                            items=list(pending),
                            source_ref=_src(artifact_id, heading_path),
                        )
                    )
                pending.clear()

            for kind, text in items:
                text_parts.append(text)
                if kind == "item":
                    pending.append(text)
                else:
                    flush()
                    blocks.append(
                        Block(
                            kind=Kind.paragraph,
                            text=text,
                            source_ref=_src(artifact_id, heading_path),
                        )
                    )
            flush()
            blocks.extend(attach.get(s_idx, []))
            if level == 1 and title_hint is None and title:
                title_hint = title
            if blocks or title:
                sections.append(
                    Section(
                        id=f"sec-{len(sections)}",
                        heading=title,
                        level=level,
                        blocks=blocks,
                    )
                )
    else:
        for page_num, page_text in enumerate(page_texts, start=1):
            heading_path = [f"Page {page_num}"]
            blocks = []
            for chunk in re.split(r"\n\s*\n", page_text):
                chunk = " ".join(line.strip() for line in chunk.splitlines()).strip()
                if not chunk:
                    continue
                if title_hint is None:
                    title_hint = chunk
                blocks.append(
                    Block(
                        kind=Kind.paragraph,
                        text=chunk,
                        source_ref=_src(artifact_id, heading_path),
                    )
                )
                text_parts.append(chunk)
            blocks.extend(table_blocks.get(page_num, []))
            if blocks:
                sections.append(
                    Section(
                        id=f"sec-{len(sections)}",
                        heading=f"Page {page_num}",
                        level=None,
                        blocks=blocks,
                    )
                )

    if has_headings:
        sections, title_hint = _tidy_pdf_sections(sections, title_hint, "\n".join(text_parts))
    if not sections:
        sections.append(Section(id="sec-0", heading="", level=None, blocks=[]))

    payload = "\n".join(text_parts)
    return ContentPack(
        schema_version=SCHEMA_VERSION,
        id=_pack_id(payload),
        language=language or detect_language(payload),
        title_hint=title_hint,
        sections=sections,
        tables=tables,
        assets=[],
        warnings=[],
    )


def parse_xlsx(
    data: bytes,
    artifact_id: str = "input.xlsx",
    language: str | None = None,
) -> ContentPack:
    """XLSX -> ContentPack: лист с данными -> секция + Table (headers из
    первой непустой строки, rows — остальные, `_table_cell` на каждой
    ячейке) + table_ref. Пустые листы и листы из одной строки (только
    заголовки, без данных) пропускаются."""
    from openpyxl import load_workbook

    try:
        wb = load_workbook(BytesIO(data), data_only=True, read_only=True)
    except Exception as exc:
        raise DeckDNAError(
            code="invalid_input",
            message=f"{artifact_id}: not a readable xlsx ({type(exc).__name__})",
            stage="content_ingestion",
            details={"artifact_id": artifact_id},
        ) from exc

    sections: list[Section] = []
    tables: list[Table] = []
    title_hint: str | None = wb.properties.title or None
    tbl_idx = 0
    text_parts: list[str] = []

    for ws in wb.worksheets:
        grid = [list(row) for row in ws.iter_rows(values_only=True)]
        while grid and all(v is None or str(v).strip() == "" for v in grid[0]):
            grid.pop(0)  # ведущие пустые строки — не заголовки
        # меньше двух строк — либо пустой лист, либо только заголовки
        # без данных: пропускаем по контракту парсера.
        if len(grid) < 2:
            continue
        headers = [str(v).strip() if v is not None else "" for v in grid[0]]
        rows = [
            [_table_cell("" if v is None else str(v)) for v in row]
            for row in grid[1:]
            if any(v is not None and str(v).strip() != "" for v in row)
        ]
        if not rows:
            continue
        heading_path = [ws.title]
        table_id = f"tbl-{tbl_idx}"
        tables.append(
            Table(
                id=table_id,
                headers=headers,
                rows=rows,
                source_ref=_src(artifact_id, heading_path),
            )
        )
        sections.append(
            Section(
                id=f"sec-{len(sections)}",
                heading=ws.title,
                level=None,
                blocks=[
                    Block(
                        kind=Kind.table_ref,
                        text=table_id,
                        source_ref=_src(artifact_id, heading_path),
                    )
                ],
            )
        )
        tbl_idx += 1
        text_parts.extend(headers)

    if not sections:
        sections.append(Section(id="sec-0", heading="", level=None, blocks=[]))

    payload = "\n".join(text_parts)
    return ContentPack(
        schema_version=SCHEMA_VERSION,
        id=_pack_id(payload),
        language=language or detect_language(payload),
        title_hint=title_hint,
        sections=sections,
        tables=tables,
        assets=[],
        warnings=[],
    )


def parse_txt(
    text: str,
    artifact_id: str = "input.txt",
    language: str | None = None,
) -> ContentPack:
    """TXT -> ContentPack: единственная секция с одним paragraph-блоком."""
    block = Block(
        kind=Kind.paragraph,
        text=text.strip(),
        source_ref=_src(artifact_id),
    )
    return ContentPack(
        schema_version=SCHEMA_VERSION,
        id=_pack_id(text),
        language=language or detect_language(text),
        title_hint=None,
        sections=[Section(id="sec-0", heading="", level=None, blocks=[block])],
        tables=[],
        assets=[],
        warnings=[],
    )


# Обрывок CSS (например, содержимое <style> из вставленного HTML-письма/
# веб-страницы, чьи теги были сняты ДО того, как текст дошёл до парсера —
# найдено вживую: пользователь вставил текст письма, стилевой блок
# ("body {\npadding: 0;...'MailSans Roman'...") дошёл как обычный
# paragraph-блок и попал в презентацию как контент). Осознанно
# консервативно (фигурные скобки + несколько "свойство: значение;") —
# обычная проза так не выглядит почти никогда.
_CSS_DECLARATION_RE = re.compile(r"[a-zA-Z-]{2,24}\s*:\s*[^;{}\n]{1,120};")
_MIN_CSS_DECLARATIONS = 3


def _looks_like_css_block(text: str) -> bool:
    if "{" not in text or "}" not in text:
        return False
    return len(_CSS_DECLARATION_RE.findall(text)) >= _MIN_CSS_DECLARATIONS


def _drop_garbage_blocks(pack: ContentPack) -> ContentPack:
    """Пост-обработка результата ЛЮБОГО парсера: убрать блоки (и элементы
    списков), похожие на утёкшую разметку/CSS, а не на реальный контент.
    Каждое удаление — предупреждение в ``warnings``, никогда не молча."""
    warnings = list(pack.warnings)
    new_sections: list[Section] = []
    for section in pack.sections:
        new_blocks: list[Block] = []
        for block in section.blocks:
            if block.text and _looks_like_css_block(block.text):
                warnings.append(
                    Warning(
                        code="content.garbage_text_dropped",
                        message="dropped a block that looks like leaked CSS/markup, not content",
                        source_ref=block.source_ref,
                    )
                )
                continue
            if block.items:
                kept_items = [it for it in block.items if not _looks_like_css_block(it)]
                if len(kept_items) != len(block.items):
                    warnings.append(
                        Warning(
                            code="content.garbage_text_dropped",
                            message="dropped list item(s) that look like leaked CSS/markup",
                            source_ref=block.source_ref,
                        )
                    )
                if not kept_items:
                    continue
                block = block.model_copy(update={"items": kept_items})
            new_blocks.append(block)
        # A section already empty before this pass (e.g. a parser's
        # placeholder "no content" section) is left exactly as-is -- only
        # a section we ourselves emptied out by dropping garbage collapses
        # away (same "empty preamble" rule _MarkdownParser._flush() uses).
        if new_blocks or section.heading or not section.blocks:
            new_sections.append(section.model_copy(update={"blocks": new_blocks}))
    return pack.model_copy(update={"sections": new_sections, "warnings": warnings})


# (parser, mode): "text" — файл читается как utf-8 str; "binary" — как bytes.
_PARSERS: dict[str, tuple[Any, str]] = {
    ".json": (parse_json_content, "text"),
    ".md": (parse_markdown, "text"),
    ".markdown": (parse_markdown, "text"),
    ".txt": (parse_txt, "text"),
    ".docx": (parse_docx, "binary"),
    ".pdf": (parse_pdf, "binary"),
    ".xlsx": (parse_xlsx, "binary"),
}


def parse_file(path: str | Path, artifact_id: str | None = None) -> ContentPack:
    """Диспетчер по расширению; неизвестный формат — typed warning-less
    `invalid_input` (per CONTENT_INGESTION.md §7)."""
    path = Path(path)
    entry = _PARSERS.get(path.suffix.lower())
    artifact_id = artifact_id or path.name
    if entry is None:
        raise DeckDNAError(
            code="invalid_input",
            message=f"unsupported content format: {path.suffix}",
            stage="content_ingestion",
            details={"artifact_id": artifact_id, "supported": sorted(_PARSERS)},
        )
    parser, mode = entry
    if mode == "binary":
        pack = parser(path.read_bytes(), artifact_id)
    else:
        raw = path.read_text(encoding="utf-8", errors="replace")
        pack = parser(raw, artifact_id)
    return _drop_garbage_blocks(pack)
