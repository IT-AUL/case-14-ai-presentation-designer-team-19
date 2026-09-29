"""Неструктурированный текст получает разделы; факты остаются дословными."""

from __future__ import annotations

import asyncio

from deckdna.contracts.content_pack import Block, ContentPack, Kind, Section, SourceRef
from deckdna.ingestion.restructure import (
    PROMPT_NAME,
    needs_restructure,
    restructure_pack,
    split_sentences,
)
from deckdna.providers.mock import MockProvider

SENTS = [
    f"Предложение номер {i} рассказывает о пользе ролевой игры для команды."
    for i in range(24)
]


def _wall() -> ContentPack:
    return ContentPack(
        schema_version="1", id="p1", language="ru",
        sections=[Section(id="s0", heading="", blocks=[
            Block(kind=Kind.paragraph, text=" ".join(SENTS), source_ref=SourceRef(artifact_id="a"))
        ])],
        tables=[], assets=[], warnings=[],
    )


def _all_text(pack: ContentPack) -> str:
    return " ".join(b.text or "" for s in pack.sections for b in s.blocks)


def test_split_sentences_drops_slide_noise():
    assert split_sentences("Первое. Слайд 3. Второе предложение!") == [
        "Первое.", "Второе предложение!",
    ]


def test_structured_pack_is_left_alone():
    pack = _wall()
    pack = pack.model_copy(update={"sections": [
        pack.sections[0].model_copy(update={"heading": "Раздел А"}),
        pack.sections[0].model_copy(update={"id": "s1", "heading": "Раздел Б"}),
    ]})
    assert not needs_restructure(pack)


def test_deterministic_split_without_model():
    new = asyncio.run(restructure_pack(_wall(), None))
    assert len(new.sections) >= 3
    assert all(s.heading for s in new.sections)
    assert _all_text(new) == " ".join(SENTS)


def test_model_groups_indices_and_text_stays_verbatim():
    def fixture(payload):
        n = len(payload["sentences"])
        return {"title": "Польза игры", "sections": [
            {"heading": "Начало", "sentences": list(range(0, n // 2))},
            {"heading": "Продолжение", "sentences": list(range(n // 2, n))},
        ]}

    gw = MockProvider(fixtures={PROMPT_NAME: fixture})
    pack = _wall().model_copy(update={"id": "p2"})
    new = asyncio.run(restructure_pack(pack, gw))
    assert [s.heading for s in new.sections] == ["Начало", "Продолжение"]
    assert new.title_hint == "Польза игры"
    assert _all_text(new) == " ".join(SENTS)


def test_bad_outline_falls_back_to_deterministic():
    gw = MockProvider(fixtures={PROMPT_NAME: {"sections": [{"heading": "Всё", "sentences": [0]}]}})
    pack = _wall().model_copy(update={"id": "p3", "language": "ru"})
    sents = SENTS[:23]  # другой текст — другой ключ кеша
    pack.sections[0].blocks[0].text = " ".join(sents)
    new = asyncio.run(restructure_pack(pack, gw))
    assert len(new.sections) >= 3


def test_partially_covered_outline_does_not_drop_source_sentences():
    """An outline missing a small tail must be rejected as lossy."""
    def fixture(payload):
        n = len(payload["sentences"])
        return {
            "sections": [
                {"heading": "Начало", "sentences": list(range(0, n // 2))},
                # Two valid sentences are intentionally omitted.
                {"heading": "Продолжение", "sentences": list(range(n // 2, n - 2))},
            ]
        }

    gw = MockProvider(fixtures={PROMPT_NAME: fixture})
    pack = _wall().model_copy(update={"id": "p-partial"})
    new = asyncio.run(restructure_pack(pack, gw))
    assert _all_text(new) == " ".join(SENTS)


def test_model_outlines_structured_pack_and_keeps_table_in_place():
    ref = SourceRef(artifact_id="a")
    first = " ".join(SENTS[:12])
    second = " ".join(SENTS[12:])
    pack = ContentPack(
        schema_version="1", id="p4", language="ru",
        sections=[
            Section(id="s0", heading="ЗАГОЛОВОК ПАРСЕРА", blocks=[
                Block(kind=Kind.paragraph, text=first, source_ref=ref),
                Block(kind=Kind.table_ref, text="tbl-0", source_ref=ref),
            ]),
            Section(id="s1", heading="Второй", blocks=[
                Block(kind=Kind.paragraph, text=second, source_ref=ref),
            ]),
        ],
        tables=[], assets=[], warnings=[],
    )
    seen = {}

    def fixture(payload):
        seen["h"] = {s["h"] for s in payload["sentences"]}
        return {"sections": [
            {"heading": "Начало", "sentences": list(range(0, 12))},
            {"heading": "Конец", "sentences": list(range(12, 24))},
        ]}

    new = asyncio.run(restructure_pack(pack, MockProvider(fixtures={PROMPT_NAME: fixture})))
    assert seen["h"] == {"ЗАГОЛОВОК ПАРСЕРА", "Второй"}  # подсказка, а не правило
    assert [s.heading for s in new.sections] == ["Начало", "Конец"]
    assert new.sections[0].blocks[-1].kind == Kind.table_ref  # таблица на своём месте
    assert _all_text(new).replace("tbl-0 ", "").replace(" tbl-0", "") == " ".join(SENTS)


def test_structured_pack_untouched_without_model():
    pack = _wall().model_copy(update={"id": "p5", "sections": [
        Section(id="a", heading="Раздел А", blocks=_wall().sections[0].blocks),
        Section(id="b", heading="Раздел Б", blocks=_wall().sections[0].blocks),
    ]})
    assert asyncio.run(restructure_pack(pack, None)) is pack
