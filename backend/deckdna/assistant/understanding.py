"""Шаг «Вот что я понял» (ADR-034 §2).

Блоки — смысловые единицы пака, которые человек видит карточками:

- ``topic`` — раздел (заголовок + первые строки), id ``s:<section>``;
- ``fact`` / ``number`` / ``quote`` — блок раздела, id ``b:<section>:<i>``
  (``number`` — в тексте есть значимое число);
- ``process`` — найденная последовательность шагов (ADR-032), ``d:<diagram>``;
- ``data`` — датасет (ADR-033) с превью главного графика и первым
  выводом, ``ds:<dataset>``.

Правки блоков (``remove | merge | move | edit``) дают новую версию пака,
как добранный пак ADR-030: исходный не меняется, отмена на клиенте —
повтор предыдущей версии."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from deckdna.analysis.pipeline import analyze_pack
from deckdna.analysis.values import plural
from deckdna.contracts.content_pack import Block, ContentPack, Kind, Section, SourceRef
from deckdna.contracts.deck_plan import Brief
from deckdna.errors import DeckDNAError
from deckdna.evaluation.measures import extract_numbers
from deckdna.ingestion.pptx_content import is_speaker_note
from deckdna.planning.content_fill import estimate_content_capacity, is_sources_heading

CONFIG = Path(__file__).resolve().parents[3] / "configs" / "assistant.default.yaml"
_PREVIEW_POINTS = 24
_SLIDES = ("слайд", "слайда", "слайдов")


@lru_cache(maxsize=1)
def phrases() -> dict[str, Any]:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def slides_word(n: int) -> str:
    return plural(n, _SLIDES)


def _first_lines(section: Section, limit: int = 2) -> str:
    lines: list[str] = []
    for block in section.blocks:
        if is_speaker_note(block):
            continue
        if block.text and block.kind not in (
            Kind.table_ref,
            Kind.chart_ref,
            Kind.diagram_ref,
            Kind.image_ref,
        ):
            lines.append(block.text)
        lines.extend(block.items or [])
        if len(lines) >= limit:
            break
    return " ".join(lines[:limit])


def _block_kind(block: Block) -> str | None:
    if block.kind == Kind.quote:
        return "quote"
    if block.kind in (Kind.paragraph, Kind.list):
        text = " ".join([block.text or "", *(block.items or [])])
        return "number" if extract_numbers(text) else "fact"
    return None


def build_blocks(pack: ContentPack) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for section in pack.sections:
        if is_sources_heading(section.heading):
            continue
        content = [b for b in section.blocks if not is_speaker_note(b)]
        if not content and not section.heading:
            continue
        # A topic preview must not repeat a numeric card verbatim.
        numeric_texts = {
            " ".join((b.text or "\n".join(b.items or [])).casefold().split())
            for b in content
            if _block_kind(b) == "number"
        }
        preview = " ".join(_first_lines(section).casefold().split())
        if section.heading and content and preview not in numeric_texts:
            blocks.append(
                {
                    "id": f"s:{section.id}",
                    "kind": "topic",
                    "title": section.heading,
                    "text": _first_lines(section),
                    "source_ref": content[0].source_ref.model_dump(exclude_none=True),
                }
            )
        for i, block in enumerate(section.blocks):
            kind = _block_kind(block)
            if kind is None or is_speaker_note(block):
                continue
            text = block.text or "\n".join(block.items or [])
            if not text.strip():
                continue
            blocks.append(
                {
                    "id": f"b:{section.id}:{i}",
                    "kind": kind,
                    "title": section.heading or None,
                    "text": text,
                    "source_ref": block.source_ref.model_dump(exclude_none=True),
                }
            )
    for diagram in pack.diagrams:
        blocks.append(
            {
                "id": f"d:{diagram.id}",
                "kind": "process",
                "title": " → ".join(diagram.steps[:2]),
                "text": " → ".join(diagram.steps),
                "source_ref": diagram.source_ref.model_dump(exclude_none=True),
            }
        )
    charts = {c.source_ref.range: c for c in pack.charts if c.source_ref.range}
    for analysis in analyze_pack(pack):
        primary = analysis.primary
        chart = charts.get(primary.view.id)
        preview = None
        if chart is not None:
            preview = {
                "chart_type": chart.chart_type,
                "categories": chart.categories[-_PREVIEW_POINTS:],
                "series": [
                    {"name": s.name, "values": s.values[-_PREVIEW_POINTS:]}
                    for s in chart.series[:3]
                ],
                "unit": chart.unit,
            }
        blocks.append(
            {
                "id": f"ds:{analysis.dataset.id}",
                "kind": "data",
                "title": analysis.dataset.title,
                "text": primary.headline,
                "dataset_id": analysis.dataset.id,
                "source_ref": analysis.dataset.source_ref.model_dump(exclude_none=True),
                "preview": preview,
            }
        )
    return blocks


def _ordered_insights(analysis):
    """Подтверждённые первыми, затем главный вывод датасета (первое
    представление — то, с чего начнётся история), затем по силе."""
    primary = analysis.primary.view.id
    return sorted(
        analysis.insights,
        key=lambda i: (not i.confirmed, i.view_id != primary, -i.strength),
    )


def dataset_cards(pack: ContentPack, brief_text: str | None = None) -> list[dict[str, Any]]:
    out = []
    for analysis in analyze_pack(pack, brief_text):
        primary = analysis.primary
        out.append(
            {
                "id": analysis.dataset.id,
                "title": analysis.dataset.title,
                "file": analysis.dataset.file_name or analysis.dataset.source_ref.artifact_id,
                "profile_summary": analysis.profile.summary_ru(),
                "task": primary.task.task,
                "task_ru": primary.task.task_ru,
                "recommendations": [
                    {"chart_type": r.chart_type, "score": r.score, "reasons": r.reasons}
                    for r in primary.rec.ranked
                ],
                "why": primary.why,
                "insights": [
                    i.model_dump(mode="json", exclude_none=True)
                    for i in _ordered_insights(analysis)
                ],
            }
        )
    return out


def _main_points(pack: ContentPack, brief_text: str | None) -> list[str]:
    points: list[str] = []
    for analysis in analyze_pack(pack, brief_text):
        top = _ordered_insights(analysis)
        points.extend((i.headline or i.fact) for i in top[:2] if i.fact_type != "missing")
    if not points:
        points = [
            s.heading
            for s in pack.sections
            if s.heading and s.blocks and not is_sources_heading(s.heading)
        ]
    return points[:3]


def build_understanding(
    pack: ContentPack,
    brief: Brief | None = None,
) -> dict[str, Any]:
    brief = brief or Brief(
        purpose="", audience="", language=pack.language or "ru", target_slide_count=8
    )
    text = " ".join(t for t in (brief.purpose, brief.key_message, brief.call_to_action) if t)
    capacity = estimate_content_capacity(pack, brief).capacity
    points = _main_points(pack, text or None)
    cfg = phrases()["understanding"]
    if capacity <= 3 and not pack.datasets:
        note = cfg["short"]
    elif pack.datasets and points:
        note = cfg["data"].format(
            points=" и ".join(p.casefold() if i else p for i, p in enumerate(points[:2]))
        )
    elif points:
        note = cfg["enough"].format(
            n=capacity, slides=slides_word(capacity), points=" и ".join(points[:2])
        )
    else:
        note = cfg["enough_no_points"].format(n=capacity, slides=slides_word(capacity))
    return {
        "content_pack_id": pack.id,
        "blocks": build_blocks(pack),
        "summary": {
            "capacity_slides": capacity,
            "main_points": points,
            "assistant_note": note,
        },
        "datasets": dataset_cards(pack, text or None),
    }


# --- правки блоков ----------------------------------------------------------


def _find_section(sections: list[Section], section_id: str) -> int:
    for i, s in enumerate(sections):
        if s.id == section_id:
            return i
    raise DeckDNAError("not_found", f"block not found: s:{section_id}")


def _parse_block_id(block_id: str) -> tuple[str, str, int | None]:
    kind, _, rest = block_id.partition(":")
    if kind == "b":
        section_id, _, index = rest.rpartition(":")
        if not section_id or not index.isdigit():
            raise DeckDNAError("invalid_input", f"bad block id: {block_id}")
        return kind, section_id, int(index)
    if kind in ("s", "d", "ds") and rest:
        return kind, rest, None
    raise DeckDNAError("invalid_input", f"bad block id: {block_id}")


def apply_block_ops(pack: ContentPack, ops: list[dict[str, Any]]) -> ContentPack:
    """``remove | merge | move | edit`` по id блоков из ``build_blocks``.
    Индексы блоков внутри раздела относятся к паку ДО правок (id стабильны,
    удалённые блоки помечаются и вычищаются в конце)."""
    sections = [s.model_copy(update={"blocks": list(s.blocks)}) for s in pack.sections]
    removed_blocks: set[tuple[str, int]] = set()
    datasets = list(pack.datasets)
    diagrams = list(pack.diagrams)
    for op in ops:
        name = op.get("op")
        kind, ref, index = _parse_block_id(str(op.get("block_id", "")))
        if name == "remove":
            if kind == "s":
                sections.pop(_find_section(sections, ref))
            elif kind == "b":
                _find_section(sections, ref)
                removed_blocks.add((ref, index))
            elif kind == "d":
                diagrams = [d for d in diagrams if d.id != ref]
                for s in sections:
                    s.blocks[:] = [
                        b for b in s.blocks if not (b.kind == Kind.diagram_ref and b.text == ref)
                    ]
            elif kind == "ds":
                datasets = [d for d in datasets if d.id != ref]
        elif name == "edit":
            if kind == "s":
                i = _find_section(sections, ref)
                sections[i] = sections[i].model_copy(
                    update={
                        "heading": str(op.get("title") or op.get("text") or sections[i].heading)
                    }
                )
            elif kind == "b":
                i = _find_section(sections, ref)
                block = sections[i].blocks[index]
                text = op.get("text")
                if block.kind == Kind.list:
                    items = op.get("items") or [
                        ln.strip() for ln in str(text or "").splitlines() if ln.strip()
                    ]
                    block = block.model_copy(update={"items": items})
                else:
                    block = block.model_copy(update={"text": str(text or "")})
                sections[i].blocks[index] = block
            else:
                raise DeckDNAError("invalid_input", f"{kind} blocks cannot be edited")
        elif name == "move":
            if kind != "s":
                raise DeckDNAError("invalid_input", "only topic blocks (s:…) can be moved")
            i = _find_section(sections, ref)
            section = sections.pop(i)
            to = max(0, min(int(op.get("index", 0)), len(sections)))
            sections.insert(to, section)
        elif name == "merge":
            into_kind, into_ref, _ = _parse_block_id(str(op.get("into", "")))
            if kind != "s" or into_kind != "s":
                raise DeckDNAError("invalid_input", "merge joins topic blocks (s:… into s:…)")
            src = sections.pop(_find_section(sections, ref))
            j = _find_section(sections, into_ref)
            sections[j] = sections[j].model_copy(
                update={"blocks": [*sections[j].blocks, *src.blocks]}
            )
        else:
            raise DeckDNAError("invalid_input", f"unknown block op: {name}")
    if removed_blocks:
        sections = [
            s.model_copy(
                update={
                    "blocks": [b for k, b in enumerate(s.blocks) if (s.id, k) not in removed_blocks]
                }
            )
            for s in sections
        ]
    keep = {d.id for d in datasets}
    gone_views = (
        {
            c.source_ref.range
            for c in pack.charts
            if c.source_ref.range and not any(c.source_ref.range.startswith(k + "-") for k in keep)
        }
        if len(keep) != len(pack.datasets)
        else set()
    )
    return pack.model_copy(
        update={
            "sections": sections,
            "datasets": datasets,
            "diagrams": diagrams,
            "insights": [i for i in pack.insights if i.dataset_id in keep],
            "charts": [c for c in pack.charts if c.source_ref.range not in gone_views],
            "tables": [t for t in pack.tables if t.source_ref.range not in gone_views],
        }
    )


def add_notes(pack: ContentPack, items: list[str]) -> ContentPack:
    """«Я добавлю пару тезисов»: тезисы пользователя — раздел пака."""
    items = [i.strip() for i in items if i and i.strip()]
    if not items:
        raise DeckDNAError("invalid_input", "notes need at least one non-empty item")
    n = sum(1 for s in pack.sections if s.id.startswith("sec-notes")) + 1
    section = Section(
        id=f"sec-notes-{n}",
        heading="Тезисы автора" if (pack.language or "ru").startswith("ru") else "Key points",
        level=2,
        blocks=[Block(kind=Kind.list, items=items, source_ref=SourceRef(artifact_id="user:notes"))],
    )
    sources = [s for s in pack.sections if is_sources_heading(s.heading)]
    rest = [s for s in pack.sections if not is_sources_heading(s.heading)]
    return pack.model_copy(update={"sections": [*rest, section, *sources]})


__all__ = [
    "add_notes",
    "apply_block_ops",
    "build_blocks",
    "build_understanding",
    "dataset_cards",
    "phrases",
    "slides_word",
]
