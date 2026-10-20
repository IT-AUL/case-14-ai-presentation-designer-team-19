"""Raw-derived dataset evidence; cached model interpretations are never sources."""

from __future__ import annotations

import json

from deckdna.contracts import evidence_graph as eg
from deckdna.contracts.content_pack import ContentPack


def dataset_evidence(pack: ContentPack) -> tuple[list[eg.Node], list[eg.Edge]]:
    # Local import keeps ordinary text-only evidence extraction lightweight.
    from deckdna.analysis.insights import cross_table_insights
    from deckdna.analysis.pipeline import analyze_dataset, view_chart
    from deckdna.analysis.views import DataView

    nodes: list[eg.Node] = []
    edges: list[eg.Edge] = []
    cross_items: list[tuple[str, str, list[DataView]]] = []
    sources_by_ds: dict[str, tuple[eg.SourceRef, str]] = {}
    for dataset in pack.datasets:
        source = eg.SourceRef(**dataset.source_ref.model_dump())
        dataset_id = f"{pack.id}:{dataset.id}"
        source_label = " · ".join(
            str(v)
            for v in (
                source.artifact_id,
                dataset.file_name,
                source.sheet,
                source.range,
            )
            if v
        )
        sources_by_ds[dataset.id] = (source, source_label)
        nodes.append(
            eg.Node(
                id=dataset_id,
                type=eg.Type.table,
                source_ref=source,
                confidence=1.0,
                text=f"Источник: {source_label}. Датасет: {dataset.title}; "
                f"строк: {dataset.row_count}; колонки: "
                + ", ".join(c.name for c in dataset.columns),
            )
        )
        # Recompute from raw values. Stored pack.insights can contain user/model
        # edits, so even their fact/headline fields are not independent evidence.
        analysis = analyze_dataset(dataset)
        cross_items.append((dataset.id, dataset.title, [va.view for va in analysis.views]))
        for insight in analysis.insights:
            node_id = f"{pack.id}:{insight.id}"
            statements = list(dict.fromkeys(t for t in (insight.headline, insight.fact) if t))
            text = "\n".join(statements + ["Источник: " + source_label] + insight.evidence)
            nodes.append(
                eg.Node(
                    id=node_id, type=eg.Type.claim, text=text, source_ref=source, confidence=1.0
                )
            )
            edges.append(eg.Edge(**{"from": node_id, "to": dataset_id}, type=eg.Type1.derived_from))
        for va in analysis.views:
            chart = view_chart(dataset, va)
            if chart is None:
                continue
            # Uploaded-file merging may prefix chart IDs. Only use the stored
            # identity, never its possibly edited caches as source values.
            aliases = [
                c.id
                for c in pack.charts
                if c.source_ref.range == va.view.id
                and c.source_ref.artifact_id == dataset.source_ref.artifact_id
            ]
            for chart_id in dict.fromkeys([chart.id, *aliases]):
                node_id = f"{pack.id}:{chart_id}"
                nodes.append(
                    eg.Node(
                        id=node_id,
                        type=eg.Type.table,
                        source_ref=source,
                        confidence=1.0,
                        text=json.dumps(
                            {
                                "source": source_label,
                                "view_id": va.view.id,
                                "title": chart.title,
                                "categories": chart.categories,
                                "series": [s.model_dump() for s in chart.series],
                                "unit": chart.unit,
                                "source_note": chart.source_note,
                            },
                            ensure_ascii=False,
                            allow_nan=False,
                        ),
                    )
                )
                edges.append(
                    eg.Edge(**{"from": node_id, "to": dataset_id}, type=eg.Type1.derived_from)
                )
    for cross_ins in cross_table_insights(cross_items):
        src_pair = sources_by_ds.get(cross_ins.dataset_id)
        if src_pair is None:
            continue
        source, source_label = src_pair
        node_id = f"{pack.id}:{cross_ins.id}"
        statements = list(dict.fromkeys(t for t in (cross_ins.headline, cross_ins.fact) if t))
        text = "\n".join(statements + ["Источник: " + source_label] + cross_ins.evidence)
        nodes.append(
            eg.Node(id=node_id, type=eg.Type.claim, text=text, source_ref=source, confidence=1.0)
        )
        edges.append(
            eg.Edge(
                **{"from": node_id, "to": f"{pack.id}:{cross_ins.dataset_id}"},
                type=eg.Type1.derived_from,
            )
        )
    return nodes, edges
