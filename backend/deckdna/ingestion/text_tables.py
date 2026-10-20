"""Таблицы внутри текста → датасеты (ADR-033 §1).

В ``.txt`` и ``.md`` таблица может быть pipe-таблицей, TSV или колонками,
выровненными пробелами (≥ 2 пробела между колонками в ≥ 3 строках
подряд). Текст вокруг таблицы — её контекст: подпись и цель («показать
рост»), он идёт в ``Dataset.context_text`` и влияет на задачу."""

from __future__ import annotations

import re

from deckdna.contracts.content_pack import ContentPack, Kind
from deckdna.contracts.dataset import Dataset
from deckdna.ingestion.tabular import dataset_from_grid

_MIN_ROWS = 3  # шапка + 2 строки данных


def _split(line: str) -> list[str] | None:
    s = line.strip()
    if not s:
        return None
    if s.count("|") >= 2:
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            return []  # разделитель шапки markdown
        return cells
    if "\t" in s:
        return [c.strip() for c in s.split("\t")]
    parts = re.split(r"\s{2,}", s)
    return [p.strip() for p in parts] if len(parts) >= 2 else None


def extract_text_tables(text: str) -> tuple[str, list[tuple[list[str], list[list[str]], str]]]:
    """(текст без таблиц, [(шапка, строки, контекст)])."""
    lines = text.splitlines()
    tables: list[tuple[int, int, list[list[str]]]] = []
    i = 0
    while i < len(lines):
        first = _split(lines[i])
        if not first or len(first) < 2:
            i += 1
            continue
        width = len(first)
        rows = [first]
        j = i + 1
        while j < len(lines):
            cells = _split(lines[j])
            if cells == []:
                j += 1
                continue
            if not cells or len(cells) != width:
                break
            rows.append(cells)
            j += 1
        if len(rows) >= _MIN_ROWS:
            tables.append((i, j, rows))
            i = j
        else:
            i += 1
    if not tables:
        return text, []
    out: list[tuple[list[str], list[list[str]], str]] = []
    keep: list[str] = []
    prev = 0
    for start, end, rows in tables:
        keep.extend(lines[prev:start])
        before = " ".join(ln.strip() for ln in lines[max(0, start - 4) : start] if ln.strip())
        after = " ".join(ln.strip() for ln in lines[end : end + 3] if ln.strip())
        out.append((rows[0], rows[1:], " ".join(t for t in (before, after) if t)))
        prev = end
    keep.extend(lines[prev:])
    return "\n".join(keep).strip(), out


def _data_like(headers: list[str], rows: list[list[object]]) -> bool:
    """Таблица с данными: ≥ 2 строк и хотя бы одна числовая колонка.

    Колонка, где проценты смешаны с простыми числами (строки — разные
    метрики: «Выручка, млрд ₽» и «Маржа, %»), — сводка автора, а не ряд:
    на одной оси её не показать, она остаётся таблицей."""
    from deckdna.analysis.values import parse_number

    if len(rows) < 2:
        return False
    numeric = False
    for c in range(len(headers)):
        values = [r[c] for r in rows if c < len(r) and r[c] not in (None, "")]
        if values and sum(parse_number(v) is not None for v in values) >= 0.8 * len(values):
            percents = {"%" in str(v) for v in values}
            if len(percents) > 1:
                return False
            numeric = True
    return numeric


def attach_text_tables(pack: ContentPack, raw_text: str, artifact_id: str) -> ContentPack:
    """``.txt``: таблицы из текста → датасеты, текст без таблиц — абзацем."""
    clean, found = extract_text_tables(raw_text)
    datasets: list[Dataset] = []
    for n, (headers, rows, context) in enumerate(found):
        if not _data_like(headers, rows):
            continue
        datasets.append(
            dataset_from_grid(
                headers,
                rows,
                artifact_id=artifact_id,
                sheet=f"таблица {n + 1}" if n else None,
                context_text=context or None,
            )
        )
    if not datasets:
        return pack
    sections = []
    for section in pack.sections:
        blocks = []
        for block in section.blocks:
            if (
                block.kind == Kind.paragraph
                and block.text
                and block.text.strip() == raw_text.strip()
            ):
                if clean:
                    blocks.append(block.model_copy(update={"text": clean}))
                continue
            blocks.append(block)
        sections.append(section.model_copy(update={"blocks": blocks}))
    return pack.model_copy(update={"sections": sections, "datasets": [*pack.datasets, *datasets]})


def tables_to_datasets(pack: ContentPack, artifact_id: str) -> ContentPack:
    """``.md``: таблицы пака с данными → датасеты; текст раздела вокруг —
    контекст. Сама таблица остаётся в паке (её покажет план, если нужно)."""
    by_id = {t.id: t for t in pack.tables}
    datasets: list[Dataset] = []
    used: set[str] = set()
    for section in pack.sections:
        context = " ".join(
            b.text or " ".join(b.items or [])
            for b in section.blocks
            if b.kind in (Kind.paragraph, Kind.list)
        )
        for block in section.blocks:
            table = by_id.get(block.text or "") if block.kind == Kind.table_ref else None
            if table is None or table.id in used or not _data_like(table.headers, table.rows):
                continue
            used.add(table.id)
            datasets.append(
                dataset_from_grid(
                    table.headers,
                    table.rows,
                    artifact_id=artifact_id,
                    sheet=section.heading or None,
                    title=table.title or section.heading or None,
                    context_text=" ".join(t for t in (section.heading, context) if t) or None,
                )
            )
    if not datasets:
        return pack
    return pack.model_copy(update={"datasets": [*pack.datasets, *datasets]})


__all__ = ["attach_text_tables", "extract_text_tables", "tables_to_datasets"]
