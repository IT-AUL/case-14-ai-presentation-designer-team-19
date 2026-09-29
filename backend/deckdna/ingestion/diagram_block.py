"""Markdown-конвенция описания простой flow-диаграммы и её парсер.

 fenced code block с языком ``diagram``:

    ```diagram id=dg-1
    Шаг 1 -> Шаг 2 -> Шаг 3 -> Шаг 4
    ```

- ``id=NAME`` в строке fence опционален (без него — id по порядку);
- шаги — через разделитель ``->``; несколько непустых строк
  добавляют свои шаги по порядку (одна цепочка).

Рендерится как SmartArt-like группа нативных редактируемых фигур
(baseline: «SmartArt-like grouped editable shapes») —
не настоящий dgm/OOXML SmartArt.
"""

from __future__ import annotations

import re

from deckdna.contracts.content_pack import Diagram, SourceRef


def parse_diagram_block(text: str, source_ref: SourceRef) -> Diagram | None:
    """Parse a ```diagram fenced block (see module docstring) into a
    Diagram. Returns None when the text isn't a diagram block or has
    fewer than two steps (a lone box isn't a flow)."""
    m = re.match(r"^```diagram\b([^\n]*)\n(.*?)```\s*$", text.strip(), re.S)
    if not m:
        return None
    attrs, body = m.group(1), m.group(2)
    id_m = re.search(r"id=([^\s]+)", attrs)
    steps: list[str] = []
    for line in body.splitlines():
        for step in line.split("->"):
            step = step.strip()
            if step:
                steps.append(step)
    if len(steps) < 2:
        return None
    return Diagram(
        id=id_m.group(1) if id_m else "diagram-1",
        steps=steps,
        source_ref=source_ref,
    )
