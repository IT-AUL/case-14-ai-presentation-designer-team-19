"""Несколько файлов в одном паке (ADR-032 §2).

«Вот доклад и таблица»: контент приходит двумя-тремя файлами. Каждый файл
разбирается своим парсером с ``artifact_id`` своего артефакта, затем паки
склеиваются в один: разделы идут подряд в порядке файлов, ``source_ref``
каждого блока, таблицы, графика, схемы и картинки указывает на свой файл.
Идентификаторы таблиц, графиков, схем, картинок и разделов получают префикс
файла (``f1-tbl-0``), ссылки ``table_ref``/``chart_ref``/``diagram_ref`` в
блоках переписываются вместе с ними — иначе ``tbl-0`` двух файлов совпали бы.
"""

from __future__ import annotations

from collections import Counter

from deckdna.contracts.content_pack import Block, ContentPack, Kind

_REF_KINDS = {Kind.table_ref, Kind.chart_ref, Kind.diagram_ref}


def _prefixed(pack: ContentPack, prefix: str) -> ContentPack:
    renamed: dict[str, str] = {}
    for item in [*pack.tables, *(pack.charts or []), *(pack.diagrams or [])]:
        renamed[item.id] = f"{prefix}{item.id}"

    def block(b: Block) -> Block:
        if b.kind in _REF_KINDS and b.text in renamed:
            return b.model_copy(update={"text": renamed[b.text]})
        return b

    return pack.model_copy(update={
        "sections": [
            s.model_copy(update={
                "id": f"{prefix}{s.id}",
                "blocks": [block(b) for b in s.blocks],
            })
            for s in pack.sections
        ],
        "tables": [t.model_copy(update={"id": renamed[t.id]}) for t in pack.tables],
        "charts": [c.model_copy(update={"id": renamed[c.id]}) for c in pack.charts or []],
        "diagrams": [d.model_copy(update={"id": renamed[d.id]}) for d in pack.diagrams or []],
        "assets": [a.model_copy(update={"id": f"{prefix}{a.id}"}) for a in pack.assets or []],
    })


def merge_packs(packs: list[ContentPack]) -> ContentPack:
    """Один пак из нескольких, по порядку файлов. Один пак — как есть."""
    if len(packs) == 1:
        return packs[0]
    parts = [_prefixed(p, f"f{i + 1}-") for i, p in enumerate(packs)]
    languages = Counter(p.language for p in packs if p.language)
    first = parts[0]
    return first.model_copy(update={
        "id": "+".join(p.id for p in packs),
        "language": languages.most_common(1)[0][0] if languages else first.language,
        "title_hint": next((p.title_hint for p in packs if p.title_hint), None),
        "sections": [s for p in parts for s in p.sections],
        "tables": [t for p in parts for t in p.tables],
        "charts": [c for p in parts for c in p.charts or []],
        "diagrams": [d for p in parts for d in p.diagrams or []],
        "assets": [a for p in parts for a in p.assets or []],
        "warnings": [w for p in parts for w in p.warnings or []],
        "external_sources": [e for p in parts for e in p.external_sources or []],
        # ADR-033: id датасетов уникальны по файлу (хэш artifact_id)
        "datasets": [d for p in packs for d in p.datasets or []],
        "insights": [i for p in packs for i in p.insights or []],
    })


__all__ = ["merge_packs"]
