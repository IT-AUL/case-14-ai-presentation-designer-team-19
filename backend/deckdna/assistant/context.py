"""Один ограниченный разбор слов автора; принятые поля заземлены цитатой."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from deckdna.assistant.count_request import read_count_request
from deckdna.contracts.content_pack import ContentPack
from deckdna.providers.prompts import load_prompt

CONTEXT_PROMPT = "assistant_context"
_LIMIT = 8000
_FIELDS = ("audience", "purpose", "key_message", "call_to_action", "target_slide_count", "tone",
           "slide_count_policy", "requested_slide_count")


class ContextField(BaseModel):
    field: Literal[
        "audience", "purpose", "key_message", "call_to_action", "target_slide_count", "tone",
    ]
    value: str = Field(min_length=1, max_length=300)
    source_id: str
    quote: str = Field(min_length=1, max_length=500)


class ContextAnswer(BaseModel):
    fields: list[ContextField] = Field(default_factory=list, max_length=6)


def context_sources(pack: ContentPack, brief: dict[str, Any]) -> dict[str, str]:
    sources = {}
    if brief.get("purpose"):
        sources["brief:purpose"] = str(brief["purpose"])[:2000]
    external = {s.id for s in pack.external_sources}
    remaining = _LIMIT - sum(len(t) for t in sources.values())
    for section in pack.sections:
        for index, block in enumerate(section.blocks):
            if remaining <= 0 or len(sources) >= 24:
                return sources
            if block.source_ref.artifact_id in external:
                continue
            text = "\n".join([block.text or "", *(block.items or [])]).strip()
            if text and remaining > 0:
                value = text[:remaining]
                sources[f"b:{section.id}:{index}"] = value
                remaining -= len(value)
    return sources


def context_key(sources: dict[str, str], brief: dict[str, Any]) -> str:
    payload = {"sources": sources, "known": {k: brief.get(k) for k in _FIELDS},
               "prompt": load_prompt(CONTEXT_PROMPT).version}
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(data).hexdigest()


def _normal(text: str) -> str:
    return " ".join(text.casefold().split())


# «для X»: кандидат — фраза до знака препинания; принимается, только если
# в ней есть слово-аудитория (иначе «для печати» или «для анализа»
# оборачивались бы фантомной аудиторией).
_AUDIENCE_RE = re.compile(r"(?:для|перед|for)\s+([^,.;:!?()\n]{2,80})", re.I)
_CLAUSE_STOP = re.compile(
    r"\s+(?:и|или|но|а|что|чтобы|котор\w*|с|со|на|в|во|по|за|из|о|об|от|у|к|ко|"
    r"до|при|про|над|под|без|через|между|где|когда|как|если|and|with|that|which)\b",
    re.I,
)
_AUDIENCE_WORDS = re.compile(
    r"(?:совет|директор|руководств|команд|инвестор|клиент|заказчик|коллег|"
    r"партн[её]р|участник|студент|эксперт|комисс|жюри|отдел|менеджер|"
    r"слушател|аудитори|публик|стейкхолдер|акционер|сотрудник|подрядчик|"
    r"компани|board|director|investor|management|executive|customer|client|"
    r"team|colleague|stakeholder|audience|jury|committee)",
    re.I,
)


def find_audience(text: str) -> tuple[str, str] | None:
    """Явная аудитория в словах автора («итоги для совета директоров»).

    Возвращает (значение, цитата) — цитата дословно из текста, как у
    полей от модели. Документы здесь не разбираются: «для кого» имеет
    смысл только в формулировках брифа/идеи."""
    for match in _AUDIENCE_RE.finditer(text or ""):
        candidate = _CLAUSE_STOP.split(match.group(1), maxsplit=1)[0].strip(" \t-–—«»\"'")
        if len(candidate) >= 2 and _AUDIENCE_WORDS.search(candidate):
            quote = match.group(0).strip()
            end = quote.find(candidate) + len(candidate)
            return candidate, quote[:end]
    return None


# Only explicit labels in uploaded materials are author instructions. Ordinary
# prose mentioning an audience or a slide count remains content.
_LABELS = {
    "audience": r"(?:целевая\s+аудитория|аудитория|audience)",
    "purpose": r"(?:цель(?:\s+презентации)?|purpose|goal)",
    "target_slide_count": r"(?:количество\s+слайдов|число\s+слайдов|слайдов|slides|slide\s+count)",
}


def labeled_context(sources: dict[str, str], brief: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = []
    for field, label in _LABELS.items():
        if brief.get(field) not in (None, ""):
            continue
        candidates = []
        for source_id, text in sources.items():
            for match in re.finditer(
                rf"^\s*(?:[-*]\s*)?(?:\*\*)?{label}(?:\*\*)?\s*:\s*(?:\*\*)?(.+)$",
                text, re.I | re.M,
            ):
                value = match.group(1).strip().strip("*").strip()
                if field == "target_slide_count":
                    count = re.fullmatch(r"(\d+)\s*(?:слайд\w*|slides?)?\.?", value, re.I)
                    if not count or not 3 <= int(count[1]) <= 40:
                        continue
                    value = int(count[1])
                candidates.append({"field": field, "value": value,
                                   "source_id": source_id, "quote": match[0].strip()})
        # Conflicting source instructions require clarification.
        if candidates and len({str(c["value"]).casefold() for c in candidates}) == 1:
            evidence.append(candidates[0])
    return evidence


def extract_context_rules(
    sources: dict[str, str], brief: dict[str, Any],
) -> list[dict[str, Any]]:
    """Правила без модели (ADR-035): явно названная аудитория в полях
    брифа. Известные поля не перезаписываются — явное слово автора
    (ответ на вопрос) важнее извлечённого."""
    evidence: list[dict[str, Any]] = labeled_context(sources, brief)
    brief = {**brief, **{e["field"]: e["value"] for e in evidence}}
    if brief.get("audience") in (None, ""):
        for source_id, text in sources.items():
            if not source_id.startswith("brief:"):
                continue
            found = find_audience(text)
            if found is not None:
                value, quote = found
                evidence.append({
                    "field": "audience", "value": value,
                    "source_id": source_id, "quote": quote,
                })
                break
    if brief.get("target_slide_count") is None and brief.get("slide_count_policy") is None:
        for source_id, text in sources.items():
            if not source_id.startswith("brief:"):
                continue
            request = read_count_request(text)
            if request.status != "accepted":
                continue
            values = {"target_slide_count": request.count, "slide_count_policy": request.policy}
            if request.policy != "preferred":
                values["requested_slide_count"] = request.count
            evidence.extend({"field": field, "value": value, "source_id": source_id,
                             "quote": request.quote} for field, value in values.items())
            break
    return evidence


async def extract_context(
    sources: dict[str, str], brief: dict[str, Any], gateway: object,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    # MockProvider без явного ответа синтезирует данные по JSON Schema.
    # Такой ответ не может выдавать себя за понимание автора.
    fixtures = getattr(gateway, "fixtures", None)
    if gateway is None or (fixtures is not None and CONTEXT_PROMPT not in fixtures):
        return {}, [], "unavailable"
    try:
        known = {k: value[:500] if isinstance(value, str) else value
                 for k in _FIELDS if (value := brief.get(k)) not in (None, "")}
        answer = await asyncio.wait_for(gateway.text_json(  # type: ignore[attr-defined]
            CONTEXT_PROMPT, {"sources": sources, "known": known}, ContextAnswer,
        ), timeout=12)
    except Exception:  # noqa: BLE001 — вопросы остаются, успех не имитируется
        return {}, [], "unavailable"
    if not isinstance(answer, ContextAnswer):
        return {}, [], "unavailable"
    found, evidence = {}, []
    for item in answer.fields:
        if brief.get(item.field) not in (None, "") or item.field in found:
            continue
        source = sources.get(item.source_id, "")
        if _normal(item.quote) not in _normal(source):
            continue
        # Не достаточно процитировать вход и придумать значение рядом.
        # Копируем сами слова автора, включая допустимую падежную форму.
        if _normal(item.value) not in _normal(item.quote):
            continue
        value: Any = item.value.strip()
        if item.field == "target_slide_count":
            request = read_count_request(source)
            if (not item.source_id.startswith("brief:")
                    or request.status not in ("plain", "accepted")):
                continue
            if not value.isdecimal() or not 3 <= int(value) <= 40:
                continue
            if int(value) != request.count:
                continue
            if not re.search(rf"\b{re.escape(value)}[\s-]*(?:слайд\w*|slides?)\b",
                             item.quote, re.I):
                continue
            value = int(value)
        found[item.field] = value
        evidence.append({"field": item.field, "value": value,
                         "source_id": item.source_id, "quote": item.quote})
    return found, evidence, "reviewed"
