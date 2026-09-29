"""Evidence Graph: ContentPack → EvidenceGraph (Stage B, детерминированно).

Строит граф подтверждений без LLM: каждый content-блок секции
(paragraph/quote/note/code и каждый item списка) и каждая непустая
ячейка таблицы становятся узлом; секции и таблицы — узлами-рамками;
assets — visual-узлами. source_ref прокидывается из ContentPack,
id узлов детерминированные (позиционные) — один и тот же pack даёт
один и тот же граф.

Правила маппинга:
- paragraph/quote/note/code/любой блок с непустым text → ``claim``;
- item списка → ``claim`` (каждый item — отдельный узел);
- непустая ячейка таблицы: число (или строка, парсящаяся в число) →
  ``number`` с Value(raw, numeric, unit по колонке из table.units);
  остальное → ``claim``; заголовки колонок — схема, не факты, узлами
  не становятся (попадают в text узла таблицы);
- секция → ``section``, рёбра ``belongs_to`` от дочерних узлов;
- таблица → ``table``, рёбра ``belongs_to`` от ячеек;
- asset → ``visual``.

confidence=1.0 у всех узлов: извлечение verbatim, без оценки
достоверности — оценка правдивости не входит в этот этап.
"""

from __future__ import annotations

from deckdna.contracts import evidence_graph as eg
from deckdna.contracts.content_pack import Block, ContentPack, SourceRef
from deckdna.contracts.evidence_graph import EvidenceGraph

# Версия схемы — общая замороженная для freeze-контрактов (как в api/app.py).
SCHEMA_VERSION = "freeze-1"


def _eref(src: SourceRef) -> eg.SourceRef:
    return eg.SourceRef(
        artifact_id=src.artifact_id, page=src.page, sheet=src.sheet, range=src.range
    )


def _numeric_cell(cell: str | float | None) -> float | None:
    if isinstance(cell, (int, float)):
        return float(cell)
    if isinstance(cell, str):
        try:
            return float(cell.replace(",", "."))
        except ValueError:
            return None
    return None


def _block_nodes(
    block: Block, si: int, bi: int, edges: list[eg.Edge], section_id: str
) -> list[eg.Node]:
    nodes: list[eg.Node] = []

    def _edge(node_id: str) -> None:
        edges.append(
            eg.Edge(**{"from": node_id, "to": section_id}, type=eg.Type1.belongs_to)
        )

    if block.text and block.text.strip():
        node_id = f"ev_blk_{si}_{bi}"
        nodes.append(
            eg.Node(
                id=node_id,
                type=eg.Type.claim,
                text=block.text.strip(),
                source_ref=_eref(block.source_ref),
                confidence=1.0,
            )
        )
        _edge(node_id)
    for k, item in enumerate(block.items or []):
        if not item or not item.strip():
            continue
        node_id = f"ev_lst_{si}_{bi}_{k}"
        nodes.append(
            eg.Node(
                id=node_id,
                type=eg.Type.claim,
                text=item.strip(),
                source_ref=_eref(block.source_ref),
                confidence=1.0,
            )
        )
        _edge(node_id)
    return nodes


def build_evidence_graph(pack: ContentPack) -> EvidenceGraph:
    nodes: list[eg.Node] = []
    edges: list[eg.Edge] = []

    for si, section in enumerate(pack.sections):
        blocks = [b for b in section.blocks]
        if not blocks:
            continue
        section_id = f"ev_sec_{si}"
        nodes.append(
            eg.Node(
                id=section_id,
                type=eg.Type.section,
                text=section.heading,
                source_ref=_eref(blocks[0].source_ref),
                confidence=1.0,
            )
        )
        for bi, block in enumerate(blocks):
            nodes.extend(_block_nodes(block, si, bi, edges, section_id))

    for ti, table in enumerate(pack.tables):
        table_id = f"ev_tbl_{ti}"
        label = table.title or ", ".join(table.headers)
        nodes.append(
            eg.Node(
                id=table_id,
                type=eg.Type.table,
                text=label or None,
                source_ref=_eref(table.source_ref),
                confidence=1.0,
            )
        )
        units = table.units or {}
        for ri, row in enumerate(table.rows):
            for ci, cell in enumerate(row):
                if cell is None or (isinstance(cell, str) and not cell.strip()):
                    continue
                node_id = f"ev_tbl_{ti}_r{ri}_c{ci}"
                numeric = _numeric_cell(cell)
                header = table.headers[ci] if ci < len(table.headers) else None
                if numeric is not None:
                    nodes.append(
                        eg.Node(
                            id=node_id,
                            type=eg.Type.number,
                            text=str(cell),
                            value=eg.Value(
                                raw=str(cell),
                                numeric=numeric,
                                unit=units.get(header) if header else None,
                            ),
                            source_ref=_eref(table.source_ref),
                            confidence=1.0,
                        )
                    )
                else:
                    nodes.append(
                        eg.Node(
                            id=node_id,
                            type=eg.Type.claim,
                            text=str(cell).strip(),
                            source_ref=_eref(table.source_ref),
                            confidence=1.0,
                        )
                    )
                edges.append(
                    eg.Edge(
                        **{"from": node_id, "to": table_id},
                        type=eg.Type1.belongs_to,
                    )
                )

    for ai, asset in enumerate(pack.assets):
        nodes.append(
            eg.Node(
                id=f"ev_ast_{ai}",
                type=eg.Type.visual,
                text=asset.caption or asset.alt,
                source_ref=eg.SourceRef(artifact_id=asset.artifact_id),
                confidence=1.0,
            )
        )

    return eg.EvidenceGraph(
        schema_version=SCHEMA_VERSION,
        id=f"eg-{pack.id}",
        content_pack_id=pack.id,
        nodes=nodes,
        edges=edges,
    )
