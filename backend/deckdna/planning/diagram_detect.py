"""Схемы, которые система сама находит в тексте (ADR-032 §4).

Раньше схема появлялась только из явного блока ```diagram. Пользователь
пишет «внедрение идёт в четыре этапа: аудит, пилот, …» или нумерованный
список шагов — и получал абзац вместо SmartArt-аналога (пункт ТЗ).

* **Без модели — эвристика.** Список из 3–8 пунктов становится схемой,
  если это шаги: пункты нумерованы или начинаются с «Этап/Шаг/Stage/Step»,
  либо рядом (заголовок раздела, предшествующий абзац) есть слова процесса
  («этап», «шаг», «процесс», «порядок», «дорожная карта», «stage», …).
  Цепочка в тексте «А → Б → В» (3–8 звеньев) — тоже схема.
* **С моделью** — промпт ``prompts/content_planning/diagram_detect.v1.yaml``
  предлагает схемы для разделов без них. Каждый шаг обязан найтись в тексте
  раздела дословно (после нормализации пробелов и регистра), как цитаты
  веб-ресёрча (``research/web.py``): шаг, которого нет в тексте, — отказ
  всей схемы.

Шаг — короткая подпись: часть пункта до «:», «—» или «-» (метка), иначе весь
пункт, не длиннее ``MAX_STEP_CHARS``. Схема добавляется в ``pack.diagrams``
блоком ``diagram_ref`` сразу после источника. Если подписи совпадают с
пунктами целиком, список заменяется схемой (иначе текст повторился бы
дважды); если у пунктов есть пояснения, список остаётся телом слайда.
Вёрстка — исправленный в ADR-031 путь схем (``diagram_fill``).
"""

from __future__ import annotations

import asyncio
import logging
import re

from pydantic import BaseModel, Field

from deckdna.contracts.content_pack import Block, ContentPack, Diagram, Kind, Section
from deckdna.providers.base import ModelGateway

logger = logging.getLogger(__name__)

PROMPT_NAME = "diagram_detect"
MIN_STEPS, MAX_STEPS = 3, 8
MAX_STEP_CHARS = 40
_MAX_STEP_WORDS = 5
_SECTIONS_PER_CALL = 12
_MAX_SECTION_CHARS = 1500

_PROCESS_RE = re.compile(
    r"этап|шаг|процесс|порядок|последовательн|дорожн\w* карт|стади|фаз[аыу]|"
    r"\bstage|\bstep|\bprocess|\bworkflow|\bpipeline|\broadmap|\bphase",
    re.IGNORECASE,
)
_ORDINAL_RE = re.compile(
    r"^\s*(?:\d+[.)]\s+|(?:этап|шаг|стадия|фаза|stage|step|phase)\s*\d*\s*[.:)—–-]?\s*)",
    re.IGNORECASE,
)
_LABEL_SPLIT_RE = re.compile(r"\s*(?::|\s[—–-]\s)\s*")
_ARROW_RE = re.compile(r"\s*(?:→|->|⇒|=>|➝|➔)\s*")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _label(item: str) -> str | None:
    """Короткая подпись шага из пункта или None."""
    text = _ORDINAL_RE.sub("", item.strip()).strip()
    head = _LABEL_SPLIT_RE.split(text, maxsplit=1)[0].strip(" .;,")
    candidate = head if head else text
    candidate = candidate.strip(" .;,")
    if not candidate or len(candidate) > MAX_STEP_CHARS:
        return None
    if len(candidate.split()) > _MAX_STEP_WORDS:
        return None
    return candidate[:1].upper() + candidate[1:]


def _steps_from_list(items: list[str], context: str) -> list[str] | None:
    if not MIN_STEPS <= len(items) <= MAX_STEPS:
        return None
    ordinal = sum(1 for i in items if _ORDINAL_RE.match(i)) >= len(items) - 1
    if not ordinal and not _PROCESS_RE.search(context):
        return None
    labels = [_label(i) for i in items]
    if any(label is None for label in labels):
        return None
    if len({_norm(label) for label in labels}) != len(labels):
        return None
    return labels  # type: ignore[return-value]


def _steps_from_arrows(text: str) -> list[str] | None:
    parts = [p.strip(" .;,") for p in _ARROW_RE.split(text)]
    if parts and ":" in parts[0]:
        # «Цепочка: Сбор данных → …» — вводная часть до двоеточия не шаг
        parts[0] = parts[0].rsplit(":", 1)[1].strip()
    if parts and parts[-1]:
        # хвост предложения после последнего звена: «… → Решение, и так далее»
        parts[-1] = re.split(r"[,;(]", parts[-1], maxsplit=1)[0].strip()
    if not MIN_STEPS <= len(parts) <= MAX_STEPS or not _ARROW_RE.search(text):
        return None
    # «Схема показывает связку ingestion → planning → composing»: начало
    # предложения прилипает к первому звену. Звенья цепочки соразмерны —
    # первое и последнее обрезаются до длины самого длинного среднего
    middle = max((len(p.split()) for p in parts[1:-1]), default=1)
    if len(parts[0].split()) > middle + 1:
        parts[0] = " ".join(parts[0].split()[-middle:])
    if len(parts[-1].split()) > middle + 1:
        parts[-1] = " ".join(parts[-1].split()[:middle])
    if any(not p or len(p) > MAX_STEP_CHARS or len(p.split()) > _MAX_STEP_WORDS for p in parts):
        return None
    return [p[:1].upper() + p[1:] for p in parts]


def _section_text(section: Section) -> str:
    return " ".join(
        t for b in section.blocks for t in [b.text or "", *(b.items or [])] if t
    )


def _has_diagram(section: Section) -> bool:
    return any(b.kind == Kind.diagram_ref for b in section.blocks)


def detect_in_section(section: Section) -> tuple[int, list[str], bool] | None:
    """(индекс блока-источника, шаги, заменить ли блок) или None."""
    if _has_diagram(section):
        return None
    previous = section.heading or ""
    for i, block in enumerate(section.blocks):
        if block.kind == Kind.list and block.items:
            steps = _steps_from_list(block.items, f"{section.heading} {previous}")
            if steps:
                replace = all(
                    _norm(_ORDINAL_RE.sub("", item).strip(" .;,")) == _norm(step)
                    for item, step in zip(block.items, steps, strict=True)
                )
                return i, steps, replace
        if block.kind == Kind.paragraph and block.text:
            steps = _steps_from_arrows(block.text)
            if steps:
                return i, steps, False
            previous = block.text
    return None


def _insert(
    pack: ContentPack, section_index: int, block_index: int, steps: list[str],
    replace: bool, origin: str,
) -> ContentPack:
    section = pack.sections[section_index]
    source = section.blocks[block_index]
    diagram_id = f"dg-auto-{len(pack.diagrams or []) + 1}"
    diagram = Diagram(id=diagram_id, steps=steps, source_ref=source.source_ref)
    ref = Block(kind=Kind.diagram_ref, text=diagram_id, source_ref=source.source_ref)
    blocks = list(section.blocks)
    if replace:
        blocks[block_index] = ref
    else:
        blocks.insert(block_index + 1, ref)
    sections = list(pack.sections)
    sections[section_index] = section.model_copy(update={"blocks": blocks})
    logger.info("diagram detected (%s) in section %s: %s", origin, section.id, steps)
    return pack.model_copy(update={
        "sections": sections, "diagrams": [*(pack.diagrams or []), diagram],
    })


class DetectedDiagram(BaseModel):
    section_id: str
    kind: str = "process"
    steps: list[str] = Field(min_length=MIN_STEPS, max_length=MAX_STEPS)


class DiagramDetectResult(BaseModel):
    diagrams: list[DetectedDiagram]


def verify_steps(steps: list[str], section_text: str) -> bool:
    """Каждый шаг — дословная подстрока текста раздела (как цитаты ресёрча)."""
    haystack = _norm(section_text)
    clean = [s.strip(" .;,") for s in steps]
    return (
        MIN_STEPS <= len(clean) <= MAX_STEPS
        and all(1 < len(s) <= MAX_STEP_CHARS and _norm(s) in haystack for s in clean)
        and len({_norm(s) for s in clean}) == len(clean)
    )


def can_detect(gateway: object) -> bool:
    if getattr(gateway, "provider_name", None) == "mock":
        return PROMPT_NAME in (getattr(gateway, "fixtures", None) or {})
    return gateway is not None


async def _model_proposals(pack: ContentPack, gateway: ModelGateway) -> list[DetectedDiagram]:
    candidates = [
        {"section_id": s.id, "heading": s.heading, "text": _section_text(s)[:_MAX_SECTION_CHARS]}
        for s in pack.sections
        if not _has_diagram(s) and len(_section_text(s)) >= 40
    ]
    if not candidates:
        return []
    batches = [
        candidates[i : i + _SECTIONS_PER_CALL]
        for i in range(0, len(candidates), _SECTIONS_PER_CALL)
    ]

    async def one(batch: list[dict]) -> list[DetectedDiagram]:
        payload = {"language": pack.language, "sections": batch, "max_steps": MAX_STEPS}
        try:
            out = await gateway.text_json(PROMPT_NAME, payload, DiagramDetectResult)
        except Exception as exc:  # noqa: BLE001 — сбой модели: эвристика остаётся
            logger.warning("diagram_detect batch failed (%s)", exc)
            return []
        return out.diagrams if isinstance(out, DiagramDetectResult) else []

    results = await asyncio.gather(*(one(b) for b in batches))
    return [d for part in results for d in part]


async def detect_diagrams(
    pack: ContentPack, gateway: ModelGateway | None = None
) -> tuple[ContentPack, int]:
    """Пак с найденными схемами и их число. Пак без находок — тот же объект."""
    found = 0
    for index in range(len(pack.sections)):
        hit = detect_in_section(pack.sections[index])
        if hit is not None:
            block_index, steps, replace = hit
            pack = _insert(pack, index, block_index, steps, replace, "heuristic")
            found += 1
    if gateway is not None and can_detect(gateway):
        by_id = {s.id: i for i, s in enumerate(pack.sections)}
        for proposal in await _model_proposals(pack, gateway):
            index = by_id.get(proposal.section_id)
            if index is None or _has_diagram(pack.sections[index]):
                continue
            section = pack.sections[index]
            if not verify_steps(proposal.steps, _section_text(section)):
                logger.info("diagram_detect: steps not verbatim in %s, rejected", section.id)
                continue
            steps = [s.strip(" .;,") for s in proposal.steps]
            steps = [s[:1].upper() + s[1:] for s in steps]
            pack = _insert(pack, index, len(section.blocks) - 1, steps, False, "model")
            found += 1
    return pack, found


__all__ = [
    "PROMPT_NAME",
    "DiagramDetectResult",
    "can_detect",
    "detect_diagrams",
    "detect_in_section",
    "verify_steps",
]
