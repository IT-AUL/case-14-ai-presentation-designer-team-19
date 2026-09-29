"""Markdown-конвенция описания chart-данных и её парсер.

 fenced code block с языком ``chart``:

    ```chart id=ch-1
    categories: 2021, 2022, 2023, 2024
    Ряд 1: 4.3, 2.5, 3.5, 4.5
    Ряд 2: 2.4, 4.4, 1.5, 2.8
    ```

- ``id=NAME`` в строке fence опционален (без него — id по порядку);
- одна строка ``categories: a, b, c`` — подписи категорий строками;
- ``Имя серии: v1, v2, ...`` — одна строка на серию, значения числовые.
"""

from __future__ import annotations

import re

from deckdna.contracts.content_pack import Chart, ChartSeries, SourceRef


def parse_chart_block(text: str, source_ref: SourceRef) -> Chart | None:
    """Parse a ```chart fenced block (see module docstring) into a
    Chart. Returns None when the text isn't a chart block.

    Convention:
    - opening fence ``​```chart`` with optional ``id=NAME``;
    - one ``categories: a, b, c`` line (string labels; numeric-looking
      labels stay strings — the chart's own cat cache type decides);
    - one ``Series name: v1, v2, ...`` line per series (values must be
      numeric; a missing name defaults to None).
    """
    m = re.match(r"^```chart\b([^\n]*)\n(.*?)```\s*$", text.strip(), re.S)
    if not m:
        return None
    attrs, body = m.group(1), m.group(2)
    id_m = re.search(r"id=([^\s]+)", attrs)
    categories: list[str] = []
    series: list[ChartSeries] = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        name, sep, values = line.partition(":")
        if not sep:
            continue
        if name.strip().lower() == "categories":
            categories = [v.strip() for v in values.split(",") if v.strip()]
            continue
        nums: list[float] = []
        for v in values.split(","):
            v = v.strip()
            if not v:
                continue
            try:
                nums.append(float(v))
            except ValueError:
                nums.append(0.0)
        series.append(ChartSeries(name=name.strip() or None, values=nums))
    if not categories or not series:
        return None
    return Chart(
        id=id_m.group(1) if id_m else "chart-1",
        categories=categories,
        series=series,
        source_ref=source_ref,
    )
