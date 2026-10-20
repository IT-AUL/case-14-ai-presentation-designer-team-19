"""Табличные входы → ``Dataset`` (ADR-033 §1).

- CSV: разделитель через ``csv.Sniffer``, кодировки utf-8 / utf-8-sig /
  cp1251; десятичная запятая и пробелы в тысячах разбирает профиль;
- табличный JSON: массив записей, ``{columns, data}``, ``{"колонка": [...]}``;
- XLSX: все листы, строка шапки — первая строка, где заполнены почти все
  колонки (над ней — заголовок листа, он идёт в ``context_text``),
  объединённые ячейки шапки.

Значения остаются сырыми: разбор чисел и дат один на все форматы
(``analysis/values.py``), поэтому выбор графика от формата не зависит."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from typing import Any

from deckdna.contracts.content_pack import ContentPack, Section, SourceRef
from deckdna.contracts.dataset import Dataset, DatasetColumn
from deckdna.errors import DeckDNAError

_MAX_ROWS = 100_000
SCHEMA_VERSION = "1.0"


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("cp1251", errors="replace")


def _clean(value: Any) -> str | float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return float(value)
    if hasattr(value, "isoformat"):
        return (
            value.isoformat()[:10]
            if hasattr(value, "hour") and not (value.hour or value.minute or value.second)
            else value.isoformat()
        )
    text = str(value).strip()
    return text or None


def title_of(artifact_id: str) -> str:
    stem = re.sub(r"\.[a-z0-9]+$", "", artifact_id.rsplit("/", 1)[-1], flags=re.I)
    return re.sub(r"[_\-]+", " ", stem).strip().capitalize() or "Данные"


def _dataset_id(artifact_id: str, sheet: str | None, headers: list[str]) -> str:
    key = f"{artifact_id}|{sheet or ''}|{'|'.join(headers)}"
    return "ds-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]  # noqa: S324


def dataset_from_grid(
    headers: list[str],
    rows: list[list[Any]],
    *,
    artifact_id: str,
    sheet: str | None = None,
    title: str | None = None,
    context_text: str | None = None,
) -> Dataset:
    """Шапка + строки → Dataset. Пустые колонки без имени отбрасываются,
    дубли имён получают номер."""
    width = max([len(headers), *(len(r) for r in rows)] or [0])
    names: list[str] = []
    for i in range(width):
        name = str(headers[i]).strip() if i < len(headers) and headers[i] is not None else ""
        name = name or f"Колонка {i + 1}"
        base, n = name, 2
        while name in names:
            name = f"{base} ({n})"
            n += 1
        names.append(name)
    rows = [r for r in rows if any(_clean(v) is not None for v in r)][:_MAX_ROWS]
    columns = []
    for i, name in enumerate(names):
        values = [_clean(r[i]) if i < len(r) else None for r in rows]
        if all(v is None for v in values) and name.startswith("Колонка "):
            continue
        columns.append(DatasetColumn(name=name, values=values))
    return Dataset(
        id=_dataset_id(artifact_id, sheet, [c.name for c in columns]),
        title=title
        or (
            sheet
            if sheet and not re.fullmatch(r"(sheet|лист)\s*\d*", sheet, re.I)
            else title_of(artifact_id)
        ),
        columns=columns,
        row_count=len(rows),
        source_ref=SourceRef(artifact_id=artifact_id, sheet=sheet),
        context_text=context_text,
    )


def sniff_delimiter(sample: str) -> str:
    """Разделитель, при котором строки разбираются на одинаковое число
    полей; при равенстве — больше полей. ``csv.Sniffer`` путается, когда
    в шапке «Сумма, ₽», а в числах десятичная запятая."""
    lines = [ln for ln in sample.splitlines()[:50] if ln.strip()]
    best, best_score = ",", (-1.0, 0)
    for delim in (";", "\t", "|", ","):
        counts = [len(r) for r in csv.reader(lines, delimiter=delim, quotechar='"')]
        if not counts:
            continue
        mode = max(set(counts), key=counts.count)
        if mode < 2:
            continue
        score = (counts.count(mode) / len(counts), mode)
        if score > best_score:
            best, best_score = delim, score
    return best


def read_csv(data: bytes | str, artifact_id: str = "data.csv") -> Dataset:
    text = data if isinstance(data, str) else _decode(data)
    text = text.lstrip("﻿")
    delimiter = sniff_delimiter(text[:20000])
    reader = csv.reader(io.StringIO(text), delimiter=delimiter, quotechar='"')
    grid = [row for row in reader]
    grid = [r for r in grid if any(c.strip() for c in r)]
    if len(grid) < 2:
        raise DeckDNAError(
            code="invalid_input",
            message=f"{artifact_id}: CSV needs a header row and at least one data row",
            stage="content_ingestion",
            details={"artifact_id": artifact_id},
        )
    return dataset_from_grid(grid[0], grid[1:], artifact_id=artifact_id)


def json_table(obj: Any) -> tuple[list[str], list[list[Any]], str | None] | None:
    """Табличный JSON → (шапка, строки, подпись) или None."""
    title = None
    if isinstance(obj, dict):
        title = obj.get("title") if isinstance(obj.get("title"), str) else None
        for key in ("data", "rows", "records", "items", "values"):
            if isinstance(obj.get(key), list) and "columns" not in obj:
                inner = json_table(obj[key])
                if inner:
                    return inner[0], inner[1], title
        if isinstance(obj.get("columns"), list) and isinstance(obj.get("data"), list):
            cols = [
                c if isinstance(c, str) else (c.get("name") if isinstance(c, dict) else str(c))
                for c in obj["columns"]
            ]
            rows = [r if isinstance(r, list) else [r.get(c) for c in cols] for r in obj["data"]]
            return [str(c) for c in cols], rows, title
        lists = {k: v for k, v in obj.items() if isinstance(v, list)}
        if (
            lists
            and len(lists) == len([k for k in obj if k != "title"])
            and len({len(v) for v in lists.values()}) == 1
            and all(not isinstance(x, (dict, list)) for v in lists.values() for x in v)
        ):
            cols = list(lists)
            n = len(next(iter(lists.values())))
            return cols, [[lists[c][i] for c in cols] for i in range(n)], title
        return None
    if isinstance(obj, list) and obj and all(isinstance(r, dict) for r in obj):
        cols: list[str] = []
        for r in obj:
            for k in r:
                if k not in cols:
                    cols.append(k)
        if any(isinstance(r.get(c), (dict, list)) for r in obj for c in cols):
            return None
        return cols, [[r.get(c) for c in cols] for r in obj], title
    return None


def read_json_table(data: bytes | str | Any, artifact_id: str = "data.json") -> Dataset | None:
    obj = data
    if isinstance(data, (bytes, str)):
        try:
            obj = json.loads(data if isinstance(data, str) else _decode(data))
        except json.JSONDecodeError:
            return None
    found = json_table(obj)
    if found is None:
        return None
    headers, rows, title = found
    if not rows:
        return None
    return dataset_from_grid(headers, rows, artifact_id=artifact_id, title=title)


def _filled(row: list[Any]) -> int:
    return sum(1 for v in row if _clean(v) is not None)


def read_xlsx(data: bytes, artifact_id: str = "data.xlsx") -> list[Dataset]:
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(data), data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise DeckDNAError(
            code="invalid_input",
            message=f"{artifact_id}: not a readable xlsx ({type(exc).__name__})",
            stage="content_ingestion",
            details={"artifact_id": artifact_id},
        ) from exc
    out: list[Dataset] = []
    for ws in wb.worksheets:
        grid = [list(r) for r in ws.iter_rows(values_only=True)]
        # объединённые ячейки шапки: значение левой верхней — на всю область
        for rng in getattr(ws, "merged_cells", None) and ws.merged_cells.ranges or []:
            top = grid[rng.min_row - 1][rng.min_col - 1] if rng.min_row - 1 < len(grid) else None
            for r in range(rng.min_row, min(rng.max_row, len(grid)) + 1):
                for c in range(rng.min_col, rng.max_col + 1):
                    if c - 1 < len(grid[r - 1]) and grid[r - 1][c - 1] is None:
                        grid[r - 1][c - 1] = top
        grid = [r for r in grid if _filled(r)]
        if len(grid) < 2:
            continue
        width = max(_filled(r) for r in grid)
        header_at = next(
            (i for i, r in enumerate(grid[:10]) if _filled(r) >= max(2, 0.8 * width)),
            0,
        )
        context = (
            " ".join(str(v) for r in grid[:header_at] for v in r if _clean(v) is not None) or None
        )
        headers = [str(v).strip() if v is not None else "" for v in grid[header_at]]
        rows = grid[header_at + 1 :]
        if not rows:
            continue
        out.append(
            dataset_from_grid(
                headers,
                rows,
                artifact_id=artifact_id,
                sheet=ws.title,
                context_text=context,
                title=context if context and len(context) <= 80 else None,
            )
        )
    return out


def pack_from_datasets(
    datasets: list[Dataset],
    artifact_id: str,
    language: str = "ru",
    title_hint: str | None = None,
) -> ContentPack:
    """Пак с одними данными. Раздел без блоков держит название файла:
    слайды строятся из датасетов (``planning/data_story.py``)."""
    payload = "|".join(d.id for d in datasets)
    return ContentPack(
        schema_version=SCHEMA_VERSION,
        id=f"pack-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:8]}",
        language=language,
        title_hint=title_hint,
        sections=[Section(id="sec-0", heading=datasets[0].title if datasets else "", blocks=[])],
        tables=[],
        assets=[],
        warnings=[],
        datasets=datasets,
    )


def parse_csv(data: bytes, artifact_id: str = "data.csv") -> ContentPack:
    return pack_from_datasets([read_csv(data, artifact_id)], artifact_id)


__all__ = [
    "dataset_from_grid",
    "title_of",
    "json_table",
    "pack_from_datasets",
    "parse_csv",
    "read_csv",
    "read_json_table",
    "read_xlsx",
]
