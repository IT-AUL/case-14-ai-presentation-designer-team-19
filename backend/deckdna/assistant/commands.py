"""Bounded intent parsing: exact quick actions, or one real provider call."""

from __future__ import annotations

import asyncio
import re
from typing import Literal

from pydantic import BaseModel, Field

from deckdna.assistant.count_request import read_count_request


class BriefEdit(BaseModel):
    field: Literal["purpose", "audience", "key_message", "call_to_action", "tone",
                   "target_slide_count", "language", "slide_count_policy"]
    value: str
    quote: str


class ParsedCommand(BaseModel):
    intent: Literal["slide_change", "plan_action", "update_brief", "clarify"]
    action: Literal["shorter", "other_layout", "other_chart", "remove_chart", "style:faithful",
                    "style:balanced", "style:visual", "replace_title", "replace_text",
                    "larger_chart", "split", "merge_next", "remove", "to_chart",
                    "to_diagram"] | None = None
    text: str | None = Field(default=None, max_length=6000)
    chart_type: str | None = None
    brief: list[BriefEdit] = Field(default_factory=list, max_length=8)
    question: str | None = None


_QUICK = {
    "сократи": "shorter", "сделай короче": "shorter", "сократи слайд": "shorter",
    "другой макет": "other_layout", "поменяй раскладку": "other_layout",
    "другой график": "other_chart", "поменяй график": "other_chart",
    "убери график": "remove_chart", "удали график": "remove_chart",
    "без графика": "remove_chart", "убери диаграмму": "remove_chart",
    "удали диаграмму": "remove_chart", "убери таблицу": "remove_chart",
    "удали таблицу": "remove_chart", "оставь только текст": "remove_chart",
    "добавь график": "to_chart", "верни график": "to_chart",
    "сделай график": "to_chart", "добавь диаграмму": "to_chart",
    "сделай схему": "to_diagram", "добавь схему": "to_diagram",
    "крупнее график": "larger_chart", "увеличь график": "larger_chart",
    "увеличь диаграмму": "larger_chart",
}
_FIELDS = {"аудитория": "audience", "цель": "purpose", "главная мысль": "key_message",
           "тон": "tone", "язык": "language", "призыв": "call_to_action"}


def quick_parse(text: str) -> ParsedCommand | None:
    stripped = text.strip()
    normalized = re.sub(r"[.!]+$", "", stripped).casefold()
    if normalized in _QUICK:
        return ParsedCommand(intent="slide_change", action=_QUICK[normalized])
    for prefix, field in _FIELDS.items():
        if normalized.startswith(prefix + ":"):
            value = stripped.split(":", 1)[1].strip()
            if value:
                return ParsedCommand(intent="update_brief", brief=[
                    BriefEdit(field=field, value=value, quote=stripped),
                ])
    for prefix, action in (("заголовок:", "replace_title"), ("текст:", "replace_text")):
        if normalized.startswith(prefix):
            value = stripped.split(":", 1)[1].strip()
            if value:
                return ParsedCommand(intent="slide_change", action=action, text=value)
    request = read_count_request(stripped)
    if request.status == "ambiguous":
        return ParsedCommand(intent="clarify", question=(
            "Уточните одно число от 3 до 40 и условие: ровно, не более или примерно."
        ))
    if request.status == "accepted" and request.pure:
        return ParsedCommand(intent="update_brief", brief=[
            BriefEdit(field="target_slide_count", value=str(request.count), quote=request.quote),
            BriefEdit(field="slide_count_policy", value=request.policy, quote=request.quote),
        ])
    return None


async def parse_command(text: str, context: dict, gateway) -> tuple[ParsedCommand, str]:
    quick = quick_parse(text)
    if quick is not None:
        return quick, "local"
    if gateway is None:
        return ParsedCommand(intent="clarify", question=(
            "Сейчас не могу разобрать свободную формулировку. Укажите действие карточкой "
            "или отправьте «цель: …», «аудитория: …», «заголовок: …»."
        )), "unavailable"
    try:
        async with asyncio.timeout(15):
            result = await gateway.text_json("assistant_command", {
                "message": text, "context": context,
            }, ParsedCommand)
    except Exception:  # noqa: BLE001 — do not claim an unexecuted command succeeded
        return ParsedCommand(
            intent="clarify", question="Не удалось разобрать команду. Уточните действие.",
        ), "failed"
    if not isinstance(result, ParsedCommand):
        return ParsedCommand(intent="clarify", question="Какое изменение нужно внести?"), "failed"
    # Values affecting intent must come from the author's current message.
    request = read_count_request(text)

    def grounded(edit: BriefEdit) -> bool:
        if not edit.quote or edit.quote not in text:
            return False
        if edit.field == "slide_count_policy":
            return (request.status == "accepted" and edit.value == request.policy
                    and request.quote in edit.quote)
        if edit.value not in edit.quote:
            return False
        if edit.field == "target_slide_count":
            return (request.status in ("accepted", "plain") and edit.value.isdecimal()
                    and int(edit.value) == request.count)
        return True

    if (any(not grounded(edit) for edit in result.brief)
            or len({e.field for e in result.brief}) != len(result.brief)):
        return ParsedCommand(
            intent="clarify", question="Уточните новую цель или аудиторию своими словами.",
        ), "rejected"
    if result.intent == "update_brief" and request.status == "accepted":
        by_field = {e.field: e for e in result.brief}
        for field, value in (("target_slide_count", str(request.count)),
                             ("slide_count_policy", request.policy)):
            by_field[field] = BriefEdit(field=field, value=value, quote=request.quote)
        result = result.model_copy(update={"brief": list(by_field.values())})
    if result.action in {"replace_title", "replace_text"} and (
        not result.text or result.text not in text
    ):
        return ParsedCommand(
            intent="clarify", question="Пришлите точный текст для замены.",
        ), "rejected"
    return result, "model"
