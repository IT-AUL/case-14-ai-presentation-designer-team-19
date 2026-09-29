"""build_evidence_graph: ContentPack → EvidenceGraph детерминированно."""

from __future__ import annotations

import json
from pathlib import Path

from deckdna.contracts import to_schema_dict
from deckdna.contracts.content_pack import (
    Asset,
    Block,
    ContentPack,
    Kind,
    Section,
    SourceRef,
    Table,
)
from deckdna.ingestion.content_parsers import parse_file
from deckdna.planning.evidence import build_evidence_graph

FIXTURE_MD = (
    Path(__file__).resolve().parents[2] / "tests/fixtures/content/poc_article.md"
)
SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"


def _pack_with_table() -> ContentPack:
    src = SourceRef(artifact_id="art_x")
    return ContentPack(
        schema_version="1.0",
        id="pack_1",
        language="ru",
        sections=[
            Section(
                id="s0",
                heading="Введение",
                blocks=[
                    Block(kind=Kind.paragraph, text="Первый факт.", source_ref=src),
                    Block(
                        kind=Kind.list,
                        items=["тезис один", "тезис два"],
                        source_ref=src,
                    ),
                ],
            )
        ],
        tables=[
            Table(
                id="t0",
                title="Метрики",
                headers=["Показатель", "Значение"],
                rows=[["Выручка", 42.0], ["Рост", "17%"]],
                units={"Значение": "mln_rub"},
                source_ref=src,
            )
        ],
        assets=[
            Asset(id="a0", kind="image", artifact_id="art_img", caption="Схема")
        ],
        warnings=[],
    )


def _ids(graph):
    return {n.id for n in graph.nodes}


def test_real_file_nodes_cover_content():
    pack = parse_file(FIXTURE_MD, artifact_id="art_md")
    graph = build_evidence_graph(pack)

    # Секция-узел на каждую непустую секцию + claim-узел на каждый блок/item.
    assert sum(n.type.value == "section" for n in graph.nodes) == len(pack.sections)
    expected_claims = sum(
        (1 if b.text else 0) + len(b.items or [])
        for s in pack.sections
        for b in s.blocks
    )
    claims = [n for n in graph.nodes if n.type.value == "claim"]
    assert len(claims) == expected_claims
    # Тексты узлов дословно из пака, source_ref прокинут.
    texts = {n.text for n in claims}
    assert "Ручная вёрстка одной колоды занимает часы" in texts
    assert all(n.source_ref.artifact_id == "art_md" for n in graph.nodes)
    # Рёбра ссылаются на существующие узлы.
    assert {e.from_ for e in graph.edges} | {e.to for e in graph.edges} <= _ids(graph)


def test_deterministic_ids():
    pack = parse_file(FIXTURE_MD, artifact_id="art_md")
    g1 = build_evidence_graph(pack)
    g2 = build_evidence_graph(pack)
    assert [n.id for n in g1.nodes] == [n.id for n in g2.nodes]
    assert g1.id == g2.id


def test_table_cells_numeric_and_claim():
    graph = build_evidence_graph(_pack_with_table())
    by_id = {n.id: n for n in graph.nodes}

    num = by_id["ev_tbl_0_r0_c1"]
    assert num.type.value == "number"
    assert num.value.numeric == 42.0
    assert num.value.unit == "mln_rub"

    word = by_id["ev_tbl_0_r0_c0"]
    assert word.type.value == "claim"
    assert word.text == "Выручка"

    # belongs_to: ячейка → таблица.
    assert any(
        e.from_ == "ev_tbl_0_r0_c1" and e.to == "ev_tbl_0" for e in graph.edges
    )
    # items списка — отдельные узлы, привязанные к секции.
    assert by_id["ev_lst_0_1_0"].text == "тезис один"
    assert any(
        e.from_ == "ev_lst_0_1_0" and e.to == "ev_sec_0" for e in graph.edges
    )
    # asset → visual-узел.
    assert by_id["ev_ast_0"].type.value == "visual"


def test_output_passes_evidence_graph_schema():
    import jsonschema

    graph = build_evidence_graph(_pack_with_table())
    schema = json.loads((SCHEMAS / "evidence-graph.schema.json").read_text())
    jsonschema.validate(instance=to_schema_dict(graph), schema=schema)


def test_empty_cells_and_empty_section_skipped():
    src = SourceRef(artifact_id="art_x")
    pack = ContentPack(
        schema_version="1.0",
        id="pack_2",
        language="ru",
        sections=[
            Section(id="s0", heading="Пустая", blocks=[]),
            Section(
                id="s1",
                heading="Живая",
                blocks=[Block(kind=Kind.paragraph, text="x", source_ref=src)],
            ),
        ],
        tables=[
            Table(
                id="t0",
                headers=["a", "b"],
                rows=[[None, ""]],
                source_ref=src,
            )
        ],
        assets=[],
        warnings=[],
    )
    graph = build_evidence_graph(pack)
    # Пустая секция — без узла; пустые ячейки — без узлов (осталась рамка таблицы).
    assert "ev_sec_1" in _ids(graph)
    assert not any(n.id.startswith("ev_tbl_0_r") for n in graph.nodes)
    assert any(n.id == "ev_tbl_0" for n in graph.nodes)
