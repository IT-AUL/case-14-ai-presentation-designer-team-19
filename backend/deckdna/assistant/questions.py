"""Шаг «Пара вопросов» — только по причине (ADR-034 §3).

Вопрос попадает в список, только когда у сервиса есть причина:

- ``content_short`` — материала меньше, чем просили слайдов;
- ``key_message`` — нет явного намерения, а сильных мыслей несколько;
- ``call_to_action`` — в выводах есть рекомендации, а цель не задана;
- ``audience`` — аудитория не названа и не извлечена из слов автора.

Варианты строятся из блоков и выводов детерминированно, один уже выбран
(``recommended``). Обратимые настройки предлагаются в suggested_brief,
не больше двух существенных вопросов за обращение. Пустой список — шаг пропускается."""

from __future__ import annotations

from typing import Any

from deckdna.analysis.pipeline import analyze_pack
from deckdna.assistant.brief import normalize_answers
from deckdna.assistant.context import find_audience
from deckdna.assistant.count_request import read_count_request
from deckdna.assistant.understanding import phrases, slides_word
from deckdna.contracts.content_pack import ContentPack
from deckdna.contracts.deck_plan import Brief
from deckdna.planning.content_fill import estimate_content_capacity, is_sources_heading
from deckdna.planning.story_director import _GENERIC_PURPOSES


def _options(raw: list[dict[str, Any]], **fmt: Any) -> list[dict[str, Any]]:
    return [
        {
            "value": o["value"],
            "label": str(o["label"]).format(**fmt),
            "recommended": bool(o.get("recommended")),
        }
        for o in raw
    ]


def _candidates(pack: ContentPack, brief_text: str | None) -> list[str]:
    """Кандидаты главной мысли: сильнейшие выводы данных, затем мысли
    разделов текста."""
    out: list[str] = []
    for analysis in analyze_pack(pack, brief_text):
        # главный вывод каждого представления, начиная с главного
        for va in analysis.views:
            best = max(
                (i for i in va.insights if i.fact_type not in ("missing", "flat")),
                key=lambda i: (i.confirmed, i.strength),
                default=None,
            )
            if best is not None and best.strength >= 0.5:
                out.append(best.headline or best.fact)
    for section in pack.sections:
        if not section.heading or is_sources_heading(section.heading) or not section.blocks:
            continue
        out.append(section.heading)
    seen: set[str] = set()
    unique = [c for c in out if not (c.casefold() in seen or seen.add(c.casefold()))]
    return unique[:3]


def build_questions(
    pack: ContentPack | None,
    brief: dict[str, Any],
    *,
    web_available: bool = False,
    defer_questions: bool = False,
) -> dict[str, Any]:
    if pack is not None:
        from deckdna.assistant.context import context_sources, extract_context_rules

        found = extract_context_rules(context_sources(pack, brief), brief)
        brief = {**brief, **{e["field"]: e["value"] for e in found}}
    cfg = phrases()["questions"]
    questions: list[dict[str, Any]] = []
    purpose = (brief.get("purpose") or "").strip()
    audience = (brief.get("audience") or "").strip()
    if not audience:
        # Явное «для совета директоров» в словах автора читается правилом
        # без модели — тогда вопрос об аудитории не нужен (ADR-035).
        for text in (purpose, brief.get("key_message") or "", brief.get("call_to_action") or ""):
            found = find_audience(text)
            if found is not None:
                audience = found[0]
                break
    explicit_target = brief.get("target_slide_count")
    # Число слайдов в свободном тексте брифа («на 12 слайдов») — тоже
    # явный запрос длины, даже если поле target_slide_count не заполнено.
    parsed_count = read_count_request(purpose)
    text_target = (
        parsed_count.count
        if parsed_count.status in ("accepted", "plain") and parsed_count.count
        else None
    )
    want = explicit_target or text_target
    target = int(want or 8)
    policy = brief.get("slide_count_policy")
    full = Brief(
        purpose=purpose,
        audience=audience,
        language=brief.get("language") or (pack.language if pack else "ru") or "ru",
        target_slide_count=max(3, min(40, target)),
    )
    capacity = estimate_content_capacity(pack, full).capacity if pack is not None else 2
    proposed_target = max(3, min(12, capacity))
    if pack is not None and pack.datasets:
        proposed_target = max(proposed_target, min(12, len(pack.charts) + 3))
    # С явным «на N слайдов» дефицит — любой разрыв с запрошенным; без
    # явного запроса система подстраивает длину под материал сама, но
    # вход вдвое тоньше дефолта (2-3 наполненных слайда из 8) — повод
    # спросить, а не молча дополнять из модели.
    deficit = capacity < target if want else capacity * 2 < target
    short_conflict = bool(
        policy != "maximum" and pack is not None and deficit and not pack.datasets
    )
    suggested = {
        **brief,
        "purpose": purpose,
        "audience": audience,
        "language": full.language,
        "target_slide_count": want or proposed_target,
        # блокирующий «ask» — только при явном запросе длины: неявная
        # худоба показывается вопросом, но не вето на план (иначе дефолт,
        # сохранённый до ответа «не больше N», ломал бы hard-policy)
        "content_fill": brief.get("content_fill")
        or ("ask" if short_conflict and want else "expand"),
    }

    if not audience:
        q = cfg["audience"]
        questions.append(
            {
                "id": "audience",
                "kind": "audience",
                "reason": q["reason"],
                "options": _options(q["options"]),
                "allow_custom": True,
                "optional": False,
            }
        )

    if short_conflict and brief.get("content_fill") not in ("shrink", "expand"):
        q = cfg["content_exact"] if policy == "exact" else cfg["content_short"]
        options = _options(q["options"], have=capacity, slides=slides_word(capacity))
        if not web_available:
            options = [o for o in options if o["value"] != "expand_web"]
        questions.append(
            {
                "id": "content_short",
                "kind": "content_short",
                "reason": q["reason"].format(have=capacity, want=target),
                "options": options,
                "allow_custom": False,
                "optional": False,
            }
        )

    if not explicit_target and read_count_request(purpose).status == "ambiguous":
        questions.insert(
            0,
            {
                "id": "length",
                "kind": "length",
                "reason": cfg["length"]["ambiguous_reason"],
                "options": [],
                "allow_custom": True,
                "optional": False,
            },
        )

    brief_text = " ".join(t for t in (purpose, brief.get("key_message")) if t) or None
    has_intent = bool(brief.get("key_message")) or purpose.casefold() not in _GENERIC_PURPOSES
    if pack is not None and not has_intent:
        candidates = _candidates(pack, brief_text)
        if len(candidates) >= 2:
            questions.append(
                {
                    "id": "key_message",
                    "kind": "key_message",
                    "reason": cfg["key_message"]["reason"],
                    "options": [
                        {"value": c, "label": c, "recommended": i == 0}
                        for i, c in enumerate(candidates)
                    ],
                    "allow_custom": True,
                    "optional": False,
                }
            )

    # Следствия подтверждаются на карточке вывода, а не вопросом с
    # выдуманным ответом «утвердить бюджет». Длину можно изменить на плане.
    # Разрешение на добор важнее косметических настроек черновика.
    questions.sort(
        key=lambda q: {"length": -1, "content_short": 0, "audience": 1, "key_message": 2}[q["kind"]]
    )
    limit = int(cfg.get("max_per_turn", 2))
    pending = questions if defer_questions else questions[limit:]
    visible = [] if defer_questions else questions[:limit]
    return {
        "questions": visible,
        "suggested_brief": suggested,
        "deferred_questions": [q["id"] for q in pending],
        "assistant_note": cfg["some"] if visible else cfg["none"],
    }


def apply_answers(brief: dict[str, Any], answers: dict[str, Any]) -> tuple[dict[str, Any], dict]:
    """Ответы → поля брифа. Возвращает (бриф, что сделать дальше):
    ``{"notes": True}`` — пользователь сам добавит тезисы."""
    values, follow_up = normalize_answers(answers)
    return {**brief, **values}, follow_up


__all__ = ["apply_answers", "build_questions"]
