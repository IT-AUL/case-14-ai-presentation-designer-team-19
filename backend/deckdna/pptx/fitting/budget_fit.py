"""Вписать текст каждого слайда в бюджет его эталона — до компоновки.

После того как эталон шаблона выбран для каждого слайда колоды (Stage 3
``layout_fit`` + роли титула/раздела/финала), текст мог остаться длиннее,
чем вмещают рамки: Stage 3 отказал для слайда, слайд без пунктов, титул
взят по роли. Итог — заголовки на 3–4 строки, ужатые до 8 pt карточки.
``text_fit`` чинит переполнение уже после сборки, но только то, что аудит
признал переполнением; ужатый шрифт переполнением не считается.

Здесь бюджет каждого слайда измерен на его эталоне
(``text_budget.template_budgets``), и слайды, где заголовок или пункт
длиннее бюджета, переписываются моделью (промпт ``slide_budget_fit``)
параллельно, один вызов на слайд. Ответ принимается по частям: элемент
заменяется, только если стал короче и прошёл проверки — числа не
выдуманы и не потеряны, предложение начинается с заглавной, без «…».
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from deckdna.contracts.deck_plan import DeckPlan, Kind, Purpose
from deckdna.evaluation.measures import extract_numbers, starts_with_capital
from deckdna.pptx.composing.card_reflow import split_label
from deckdna.providers.base import ModelGateway

logger = logging.getLogger(__name__)

PROMPT_NAME = "slide_budget_fit"
# превышение, с которого слайд отправляется модели: мелкие излишки
# поглощают reflow карточек и запас метрик
_TITLE_SLACK = 1.1
_BULLET_SLACK = 1.15
_MAX_CONCURRENT = 8
_AGENDA_ITEM_CHARS = 45
_BODY_KINDS = frozenset({Kind.bullet, Kind.paragraph, Kind.subtitle, Kind.quote})


class BudgetFitAnswer(BaseModel):
    title: str = Field(min_length=1)
    bullets: list[str] = Field(default_factory=list)


@dataclass
class BudgetFitReport:
    slides_over_budget: int = 0
    slides_rewritten: int = 0
    items_rewritten: int = 0
    model_calls: int = 0
    rejected: dict[int, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "slides_over_budget": self.slides_over_budget,
            "slides_rewritten": self.slides_rewritten,
            "items_rewritten": self.items_rewritten,
            "model_calls": self.model_calls,
            "rejected": {str(k): v for k, v in self.rejected.items()},
        }


def can_rewrite(gateway: object) -> bool:
    """Mock без явного fixture пишет бессмыслицу — его не пускаем."""
    if getattr(gateway, "provider_name", None) == "mock":
        return PROMPT_NAME in (getattr(gateway, "fixtures", None) or {})
    return gateway is not None


def _bare(numbers: set[str]) -> set[str]:
    return {re.sub(r"[^\d.]", "", n) for n in numbers}


def _accept(old: str, new: str, *, headed: bool = False, limit: int = 0) -> bool:
    """Новый вариант элемента годится вместо старого.

    Обычно он обязан стать короче. *headed* — пункту карточки добавлен
    заголовок («Заголовок: фраза»): тогда он может и удлиниться, но не
    больше бюджета *limit*."""
    new = new.strip()
    if not new:
        return False
    if headed:
        label, _ = split_label(new)
        if not label or (len(new) >= len(old) and len(new) > limit * _BULLET_SLACK):
            return False
    elif len(new) >= len(old):
        return False
    if not starts_with_capital(new) or new.endswith(("…", "...", ":")):
        return False
    return _bare(extract_numbers(new)) == _bare(extract_numbers(old))


def _body_units(slide) -> list[int]:
    return [
        i
        for i, u in enumerate(slide.content_units)
        if u.kind in _BODY_KINDS and u.text and u.role != "equation"
    ]


def _title(slide) -> tuple[str, int | None]:
    for i, u in enumerate(slide.content_units):
        if u.kind == Kind.title and u.text:
            return u.text, i
    return slide.title_intent or "", None


async def fit_plan_to_budgets(
    plan: DeckPlan,
    slide_parts: list[str],
    budgets: dict[str, dict],
    gateway: ModelGateway,
    *,
    language: str = "ru",
    protected_indices: frozenset[int] = frozenset(),
) -> tuple[DeckPlan, BudgetFitReport]:
    """(новый план, отчёт). *slide_parts[i]* — эталон слайда *i*."""
    report = BudgetFitReport()
    jobs: list[tuple[int, str, list[int], int | None, int | None, bool]] = []
    for i, slide in enumerate(plan.slides):
        if i >= len(slide_parts) or slide.index in protected_indices:
            continue
        budget = budgets.get(slide_parts[i]) or {}
        t_max, b_max = budget.get("title_max_chars"), budget.get("bullet_max_chars")
        title, _ = _title(slide)
        body = _body_units(slide)
        over_title = bool(t_max) and len(title) > t_max * _TITLE_SLACK
        over_body = bool(b_max) and any(
            len(slide.content_units[k].text or "") > b_max * _BULLET_SLACK for k in body
        )
        # карточки «заголовок + текст», а у пунктов нет заголовка — строка
        # заголовка карточки осталась бы пустой
        headings = (
            bool(budget.get("card_headings"))
            and slide.purpose != Purpose.agenda  # пункты повестки — темы, не карточки
            and len(body) >= 2
        ) and any(
            split_label(slide.content_units[k].text or "")[0] is None for k in body
        )
        # повестка = заголовки слайдов колоды (planning/agenda.py) — всегда
        # сжимаются до коротких тем
        agenda = slide.purpose == Purpose.agenda and len(body) >= 2
        if agenda:
            b_max = min(b_max or _AGENDA_ITEM_CHARS, _AGENDA_ITEM_CHARS)
            over_body = any(len(slide.content_units[k].text or "") > b_max for k in body)
        if over_title or over_body or headings:
            jobs.append((i, title, body, t_max, b_max, headings))
    report.slides_over_budget = len(jobs)
    if not jobs:
        return plan, report

    sem = asyncio.Semaphore(_MAX_CONCURRENT)

    async def ask(i: int, title: str, body: list[int], t_max, b_max, headings: bool):
        slide = plan.slides[i]
        payload = {
            "title": title,
            "bullets": [slide.content_units[k].text for k in body],
            "title_max_chars": t_max or max(len(title), 1),
            "bullet_max_chars": b_max or max(
                (len(slide.content_units[k].text or "") for k in body), default=1
            ),
            "card_headings": headings,
            "agenda": slide.purpose == Purpose.agenda,
            "language": language,
        }
        async with sem:
            return await gateway.text_json(PROMPT_NAME, payload, BudgetFitAnswer)

    answers = await asyncio.gather(*(ask(*job) for job in jobs), return_exceptions=True)
    report.model_calls = len(jobs)

    slides = list(plan.slides)
    for (i, title, body, _t, b_max, headings), answer in zip(jobs, answers, strict=True):
        if isinstance(answer, BaseException):
            report.rejected[i] = f"ошибка модели: {type(answer).__name__}"
            continue
        if not isinstance(answer, BudgetFitAnswer):
            report.rejected[i] = "ответ не по схеме"
            continue
        if len(answer.bullets) != len(body):
            report.rejected[i] = f"{len(answer.bullets)} пунктов вместо {len(body)}"
            continue
        slide = slides[i]
        units = list(slide.content_units)
        changed = 0
        for k, new in zip(body, answer.bullets, strict=True):
            old = units[k].text or ""
            if _accept(old, new, headed=headings, limit=b_max or len(old)):
                units[k] = units[k].model_copy(update={"text": new.strip()})
                changed += 1
        update: dict = {"content_units": units}
        if _accept(title, answer.title.rstrip(". ")):
            new_title = answer.title.strip().rstrip(".")
            _, title_idx = _title(slide)
            if title_idx is not None:
                units[title_idx] = units[title_idx].model_copy(update={"text": new_title})
            update["title_intent"] = new_title
            changed += 1
        if changed:
            slides[i] = slide.model_copy(update=update)
            report.slides_rewritten += 1
            report.items_rewritten += changed
        else:
            report.rejected[i] = "ни один элемент не прошёл проверку"
    logger.info(
        "budget fit: %d/%d slides over budget rewritten (%d items)",
        report.slides_rewritten,
        report.slides_over_budget,
        report.items_rewritten,
    )
    return plan.model_copy(update={"slides": slides}), report
