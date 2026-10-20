"""Выводы, шаги 2–3: «что значит» и «что из этого следует» (ADR-033 §5).

Факты считает код (``insights.py``). Модель только формулирует смысл и
следствие для 1–3 сильнейших фактов каждого датасета; каждое число в её
тексте сверяется с числами факта, как цитаты в ADR-030. Отказ — шаблонная
формулировка смысла из конфига, следствие не пишется. Без модели шаги
не выполняются: следствия не выдумываются.

Следствие — предложение: ``needs_confirmation = True`` до подтверждения
пользователем (``PATCH /content-packs/{id}/insights/{id}``); на финальный
слайд «Что предлагаем» попадают только подтверждённые.

Намерение: если в брифе и тексте вокруг таблицы нет маркеров задачи,
модель относит его к одной задаче (``prompts/analysis/analysis_intent.v1.yaml``);
ответ дописывается в ``Dataset.context_text`` словами задачи и дальше
работает как маркер — вес добавляет, невозможное возможным не делает."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from deckdna.analysis.task import TASK_RU, intent_of
from deckdna.contracts.content_pack import ContentPack
from deckdna.contracts.dataset import Insight
from deckdna.evaluation.measures import extract_numbers

logger = logging.getLogger(__name__)

INTERPRET_PROMPT = "analysis_interpret"
INTENT_PROMPT = "analysis_intent"
_PER_DATASET = 3
_MAX_CHARS = 200
_WORD_RATIOS = {
    "2": re.compile(r"\b(?:вдвое|двукратн\w*|удво\w*|doubl\w*)\b", re.I),
    "3": re.compile(r"\b(?:втрое|тр[её]хкратн\w*|утро[ие]\w*|tripl\w*)\b", re.I),
    "4": re.compile(r"\b(?:вчетверо|четыр[её]хкратн\w*|учетвер\w*|quadrupl\w*)\b", re.I),
}
# шаблонный смысл при отказе модели (числа — только из факта)
_TEMPLATES = {
    "growth": "Показатель заметно вырос за период.",
    "decline": "Показатель снизился за период.",
    "flat": "Показатель держится на одном уровне.",
    "peak": "Есть выраженный пик, его стоит объяснить.",
    "acceleration": "Рост ускоряется к концу периода.",
    "deceleration": "Рост замедляется к концу периода.",
    "leader": "Один элемент заметно опережает остальные.",
    "gap": "Разрыв между лидером и вторым местом большой.",
    "concentration": "Результат сосредоточен в нескольких позициях.",
    "spread": "Большинство значений лежит в узком диапазоне.",
    "wide_spread": (
        "Значения распределены в широком диапазоне; ориентироваться на среднее рискованно."
    ),
    "outlier": "Есть значения, заметно выбивающиеся из общей картины.",
    "correlation": "Показатели меняются вместе; причинность стоит проверить.",
    "cross_table": "Динамика подтверждается сразу в двух таблицах по общей оси.",
}


class InterpretItem(BaseModel):
    id: str
    meaning: str | None = None
    implication: str | None = None


class InterpretBatch(BaseModel):
    items: list[InterpretItem]


class IntentAnswer(BaseModel):
    task: Literal["trend", "comparison", "composition", "distribution", "relationship", "none"]


@dataclass
class InterpretReport:
    interpreted: int = 0
    rejected: int = 0
    implications: int = 0
    intent: str | None = None
    rejections: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "interpreted": self.interpreted,
            "rejected": self.rejected,
            "implications": self.implications,
            "intent": self.intent,
            "rejections": self.rejections[:10],
        }


def can_interpret(gateway: object, prompt: str = INTERPRET_PROMPT) -> bool:
    """Как у заметок докладчика: мок участвует только с фикстурой."""
    if gateway is None:
        return False
    if getattr(gateway, "provider_name", None) == "mock":
        return prompt in (getattr(gateway, "fixtures", None) or {})
    return True


def check_text(text: str | None, insight: Insight) -> str | None:
    """Причина отказа или None: числа текста ⊆ числа факта, без противоречий."""
    from deckdna.analysis.guard import has_narrow_spread_claim

    if not text:
        return None
    if len(text) > _MAX_CHARS:
        return f"длиннее {_MAX_CHARS} символов"
    if insight.fact_type == "wide_spread" and has_narrow_spread_claim(text):
        return "противоречие: узкий разброс при широком диапазоне"
    pool = " ".join([insight.fact, insight.headline or "", *insight.numbers])

    # «Двукратный» тоже означает 2: модель обходила проверку цифр и
    # превращала точный рост 2,1 в «удвоение» (живой VK-прогон 17.10).
    def numbers(value: str) -> set[str]:
        return extract_numbers(value, strict=True) | {
            n for n, pattern in _WORD_RATIOS.items() if pattern.search(value)
        }

    invented = numbers(text) - numbers(pool)
    if invented:
        return "числа не из факта: " + ", ".join(sorted(invented))
    return None


def _targets(pack: ContentPack) -> list[Insight]:
    by_dataset: dict[str, list[Insight]] = {}
    for ins in pack.insights:
        if ins.fact_type in ("missing", "reference", "kpi") or ins.meaning or ins.confirmed:
            continue
        by_dataset.setdefault(ins.dataset_id, []).append(ins)
    out = []
    for items in by_dataset.values():
        out.extend(sorted(items, key=lambda i: -i.strength)[:_PER_DATASET])
    return out


async def interpret_pack(
    pack: ContentPack,
    brief: object,
    gateway: object,
) -> tuple[ContentPack, InterpretReport]:
    from deckdna.analysis.guard import normalize_ru_text

    report = InterpretReport()
    if not pack.datasets or not can_interpret(gateway):
        return pack, report
    targets = _targets(pack)
    if not targets:
        return pack, report
    payload = {
        "language": getattr(brief, "language", None) or pack.language or "ru",
        "purpose": getattr(brief, "purpose", None),
        "audience": getattr(brief, "audience", None),
        "key_message": getattr(brief, "key_message", None),
        "items": [
            {"id": i.id, "fact": i.fact, "headline": i.headline, "numbers": i.numbers}
            for i in targets
        ],
    }
    try:
        out = await gateway.text_json(INTERPRET_PROMPT, payload, InterpretBatch)
    except Exception as exc:  # noqa: BLE001 — сбой модели: выводы остаются фактами
        logger.warning("insight interpretation failed (%s)", exc)
        return pack, report
    if not isinstance(out, InterpretBatch):
        return pack, report
    answers = {item.id: item for item in out.items}
    updated: dict[str, Insight] = {}
    for ins in targets:
        item = answers.get(ins.id)
        if item is None:
            continue
        meaning = normalize_ru_text(item.meaning) if item.meaning else None
        implication = normalize_ru_text(item.implication) if item.implication else None
        reason = check_text(meaning, ins)
        if reason:
            report.rejected += 1
            report.rejections.append({"id": ins.id, "field": "meaning", "reason": reason})
            meaning = _TEMPLATES.get(ins.fact_type or "")
        reason = check_text(implication, ins)
        if reason:
            report.rejected += 1
            report.rejections.append({"id": ins.id, "field": "implication", "reason": reason})
            implication = None
        if not meaning and not implication:
            continue
        report.interpreted += 1
        report.implications += int(bool(implication))
        updated[ins.id] = ins.model_copy(
            update={
                "meaning": (meaning or "").strip() or None,
                "implication": (implication or "").strip() or None,
                "needs_confirmation": bool(implication),
            }
        )
    if not updated:
        return pack, report
    return pack.model_copy(
        update={"insights": [updated.get(i.id, i) for i in pack.insights]}
    ), report


async def classify_intent(
    pack: ContentPack,
    brief_text: str | None,
    gateway: object,
) -> tuple[ContentPack, str | None]:
    """Намерение моделью, когда маркеров нет; результат — в контекст
    датасетов словами задачи («Цель: динамика»)."""
    if not pack.datasets or not can_interpret(gateway, INTENT_PROMPT):
        return pack, None
    texts = " ".join(t for t in [brief_text, *(d.context_text or "" for d in pack.datasets)] if t)
    if intent_of(texts):
        return pack, None
    payload = {
        "text": texts,
        "columns": [c.name for d in pack.datasets for c in d.columns][:30],
    }
    try:
        out = await gateway.text_json(INTENT_PROMPT, payload, IntentAnswer)
    except Exception as exc:  # noqa: BLE001
        logger.warning("intent classification failed (%s)", exc)
        return pack, None
    task = getattr(out, "task", None)
    if task not in TASK_RU:
        return pack, None
    marker = f"Цель: {TASK_RU[task]}."
    datasets = [
        d.model_copy(update={"context_text": " ".join(t for t in (d.context_text, marker) if t)})
        for d in pack.datasets
    ]
    return pack.model_copy(update={"datasets": datasets}), task


__all__ = [
    "INTENT_PROMPT",
    "INTERPRET_PROMPT",
    "IntentAnswer",
    "InterpretBatch",
    "InterpretReport",
    "can_interpret",
    "check_text",
    "classify_intent",
    "interpret_pack",
]
