"""Помощник-коллега в чате (ADR-058): свободный диалог поверх typed операций.

Модель на каждом шаге возвращает строгий JSON ``AgentTurn``: ответ автору,
до четырёх действий из реестра ``TOOLS`` и, если нужно, один вопрос с
вариантами. Действия исполняет ``Workspace`` (API-слой) существующими
операциями компилятора; модель не пишет в PPTX и не вызывает ничего вне
реестра. Результаты действий возвращаются модели как наблюдения, цикл
ограничен по шагам и времени. Числа в новом тексте сверяются с материалом,
найденными в интернете фактами и сообщениями автора."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from deckdna.errors import DeckDNAError

PROMPT = "assistant_agent"
MAX_STEPS = 5
BUDGET_S = 150.0
CALL_TIMEOUT_S = 45.0

Tool = Literal[
    "view_slide", "web_research", "audit", "explain_chart",
    "rewrite_slide", "set_title", "shorter", "other_layout", "change_chart", "remove_chart",
    "larger_chart", "restyle", "to_chart", "to_diagram", "split", "merge_next",
    "add_slide", "remove_slide", "move_slide", "generate_image", "update_brief", "undo",
]

READ_ONLY: frozenset[str] = frozenset({"view_slide", "web_research", "audit", "explain_chart"})

TOOLS: dict[str, str] = {
    "view_slide": "полный текст и состав слайда {slide}",
    "web_research": "поиск фактов и цифр в интернете {query}, можно для {slide}; "
                    "найденное с источниками вернётся наблюдением",
    "audit": "проблемы качества колоды или слайда {slide}",
    "explain_chart": "почему на слайде {slide} такой график и какие есть альтернативы",
    "rewrite_slide": "новый текст слайда {slide}: {title} (необязательно) и пункты {items}; "
                     "{source}=material|web|model — откуда содержание",
    "set_title": "новый заголовок слайда {slide} = {text}",
    "shorter": "сократить слайд {slide}",
    "other_layout": "другая раскладка шаблона для слайда {slide}",
    "change_chart": "другой тип графика на слайде {slide}: {chart_type} или следующий",
    "remove_chart": "убрать график, таблицу или схему со слайда {slide}, оставить только текст",
    "larger_chart": "увеличить график на слайде {slide}",
    "restyle": "слайд {slide} в стиле {style}=faithful|balanced|visual",
    "to_chart": "пункты слайда {slide} -> график (только если есть данные)",
    "to_diagram": "пункты слайда {slide} -> схема-процесс",
    "split": "разбить слайд {slide} на два",
    "merge_next": "объединить слайд {slide} со следующим",
    "add_slide": "новый слайд после {slide} с {title} и {items}, {source} как у rewrite_slide",
    "remove_slide": "удалить слайд {slide}",
    "move_slide": "переставить слайд {slide} на место {to}",
    "generate_image": "сгенерировать иллюстрацию для слайда {slide} по описанию {prompt}",
    "update_brief": "поменять задачу колоды: {field}=audience|purpose|key_message|tone|"
                    "call_to_action, {value}",
    "undo": "отменить последнюю правку помощника",
}

STATUS_RU: dict[str, str] = {
    "view_slide": "Смотрю слайд {slide}",
    "web_research": "Ищу в интернете: {query}",
    "audit": "Проверяю качество",
    "explain_chart": "Разбираю график на слайде {slide}",
    "rewrite_slide": "Переписываю слайд {slide}",
    "set_title": "Меняю заголовок слайда {slide}",
    "shorter": "Сокращаю слайд {slide}",
    "other_layout": "Подбираю другую раскладку для слайда {slide}",
    "change_chart": "Меняю график на слайде {slide}",
    "remove_chart": "Убираю график со слайда {slide}",
    "larger_chart": "Увеличиваю график на слайде {slide}",
    "restyle": "Перестраиваю слайд {slide}",
    "to_chart": "Превращаю слайд {slide} в график",
    "to_diagram": "Собираю схему на слайде {slide}",
    "split": "Разбиваю слайд {slide}",
    "merge_next": "Объединяю слайд {slide} со следующим",
    "add_slide": "Добавляю слайд после {slide}",
    "remove_slide": "Убираю слайд {slide}",
    "move_slide": "Переставляю слайд {slide} на место {to}",
    "generate_image": "Рисую иллюстрацию для слайда {slide}",
    "update_brief": "Обновляю задачу презентации",
    "undo": "Отменяю последнюю правку",
}


class AgentAction(BaseModel):
    tool: Tool
    slide: int | None = Field(default=None, ge=1, le=200)
    to: int | None = Field(default=None, ge=1, le=200)
    title: str | None = Field(default=None, max_length=200)
    text: str | None = Field(default=None, max_length=1200)
    items: list[str] = Field(default_factory=list, max_length=8)
    source: Literal["material", "web", "model"] | None = None
    chart_type: str | None = Field(default=None, max_length=40)
    style: Literal["faithful", "balanced", "visual"] | None = None
    query: str | None = Field(default=None, max_length=300)
    prompt: str | None = Field(default=None, max_length=600)
    field: Literal["audience", "purpose", "key_message", "tone", "call_to_action"] | None = None
    value: str | None = Field(default=None, max_length=600)


class AgentQuestion(BaseModel):
    text: str = Field(max_length=400)
    options: list[str] = Field(default_factory=list, max_length=4)


class AgentTurn(BaseModel):
    reply: str = Field(default="", max_length=2000)
    actions: list[AgentAction] = Field(default_factory=list, max_length=4)
    question: AgentQuestion | None = None
    done: bool = True


class Workspace(Protocol):
    def overview(self) -> dict[str, Any]: ...

    def capabilities(self) -> dict[str, Any]: ...

    def grounding_corpus(self) -> str: ...

    async def execute(self, action: AgentAction) -> dict[str, Any]: ...


Emit = Callable[[str, dict[str, Any]], None]


class AgentResult(BaseModel):
    reply: str
    question: AgentQuestion | None = None
    actions: list[dict[str, Any]] = Field(default_factory=list)
    model_calls: int = 0
    mode: Literal["model", "local", "unavailable"] = "model"


_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def numbers_of(text: str) -> set[str]:
    """Числа текста в нормальной форме (запятая → точка, без пробелов-разрядов)."""
    compact = re.sub(r"(?<=\d)[\s\u00a0\u202f](?=\d{3}\b)", "", text or "")
    return {n.replace(",", ".").rstrip("0").rstrip(".") if "." in n.replace(",", ".")
            else n for n in _NUM_RE.findall(compact)}


def ungrounded_numbers(texts: list[str], corpus: str) -> list[str]:
    """Числа из *texts*, которых нет в *corpus* (номера слайдов 1–9 допустимы)."""
    known = numbers_of(corpus)
    out: list[str] = []
    for text in texts:
        for n in sorted(numbers_of(text)):
            if n not in known and not (n.isdigit() and len(n) == 1) and n not in out:
                out.append(n)
    return out


def status_text(action: AgentAction) -> str:
    template = STATUS_RU.get(action.tool, action.tool)
    values = {"slide": action.slide or "", "to": action.to or "",
              "query": (action.query or "")[:80]}
    return template.format(**values).strip()


def _observation(action: AgentAction, result: dict[str, Any]) -> dict[str, Any]:
    return {"tool": action.tool, "slide": action.slide, **result}


def _history_view(history: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in history[-limit:]:
        text = str(item.get("text") or "")[:600]
        question = item.get("question")
        actions = item.get("actions")
        if text or question or actions:
            entry: dict[str, Any] = {"role": str(item.get("role")), "text": text}
            if isinstance(question, dict) and question.get("text"):
                entry["asked_question"] = {
                    "text": str(question.get("text"))[:200],
                    "options": [str(o)[:80] for o in (question.get("options") or [])[:4]],
                }
            if isinstance(actions, list) and actions:
                entry["actions"] = [
                    {
                        "tool": a.get("tool"),
                        "slide": a.get("slide"),
                        "ok": a.get("ok"),
                        "summary": a.get("summary"),
                    }
                    for a in actions[:4]
                    if isinstance(a, dict)
                ]
            out.append(entry)
    return out


async def run_agent(
    message: str,
    *,
    workspace: Workspace,
    gateway: Any,
    history: list[dict[str, Any]] | None = None,
    selected_slide: int | None = None,
    emit: Emit | None = None,
    canceled: Callable[[], bool] | None = None,
    max_steps: int = MAX_STEPS,
    budget_s: float = BUDGET_S,
) -> AgentResult:
    """Диалоговый цикл: модель → действия → наблюдения → ответ автору."""
    emit = emit or (lambda _e, _d: None)
    canceled = canceled or (lambda: False)
    if gateway is None:
        return await run_local(message, workspace=workspace, selected_slide=selected_slide,
                               emit=emit)
    deadline = time.monotonic() + budget_s
    observations: list[dict[str, Any]] = []
    performed: list[dict[str, Any]] = []
    calls = 0
    last: AgentTurn | None = None
    interrupted = False
    for step in range(max_steps):
        if canceled():
            raise DeckDNAError("state_conflict", "assistant message was canceled",
                               http_status=409)
        remaining = deadline - time.monotonic()
        if remaining <= 5:
            break
        emit("agent.status", {"text": "Думаю…" if step == 0 else "Проверяю результат…",
                              "step": step + 1})
        payload = {
            "message": message,
            "history": _history_view(history or []),
            "deck": workspace.overview(),
            "selected_slide": selected_slide,
            "capabilities": workspace.capabilities(),
            "tools": TOOLS,
            "observations": observations[-10:],
            "step": {"current": step + 1, "max": max_steps,
                     "last": step + 1 >= max_steps},
        }
        try:
            async with asyncio.timeout(min(CALL_TIMEOUT_S, remaining)):
                turn = await gateway.text_json(PROMPT, payload, AgentTurn)
        except (TimeoutError, DeckDNAError) as exc:
            calls += 1
            if last is None:
                raise DeckDNAError("provider_unavailable", "assistant model is unavailable",
                                   http_status=503) from exc
            interrupted = True
            break
        calls += 1
        if not isinstance(turn, AgentTurn):
            turn = AgentTurn.model_validate(turn)
        last = turn
        if not turn.actions:
            break
        any_failed = False
        mutated = False
        for action in turn.actions:
            if canceled():
                raise DeckDNAError("state_conflict", "assistant message was canceled",
                                   http_status=409)
            emit("agent.action", {"phase": "started", "tool": action.tool,
                                  "slide": action.slide, "text": status_text(action)})
            try:
                result = await workspace.execute(action)
            except DeckDNAError as exc:
                result = {"ok": False, "error": exc.message, "code": exc.code}
            except Exception:  # noqa: BLE001 — модель узнаёт о сбое, колода прежняя
                result = {"ok": False, "error": "internal error, the deck is unchanged",
                          "code": "internal_error"}
            ok = bool(result.get("ok"))
            any_failed = any_failed or not ok
            mutated = mutated or (ok and action.tool not in READ_ONLY)
            record = {"tool": action.tool, "slide": action.slide, "ok": ok,
                      "summary": result.get("summary") or result.get("error"),
                      **({"revision": result["revision"]} if "revision" in result else {}),
                      **({"sources": result["sources"]} if result.get("sources") else {})}
            performed.append(record)
            emit("agent.action", {"phase": "finished", **record,
                                  "text": status_text(action)})
            observations.append(_observation(action, result))
            if not ok:
                break
        read_only = all(a.tool in READ_ONLY for a in turn.actions)
        if turn.done and not any_failed and not read_only and mutated:
            break
    final = last or AgentTurn(reply="")
    reply = final.reply.strip()
    if interrupted and performed:
        done = [str(p["summary"]) for p in performed if p["ok"] and p["tool"] not in READ_ONLY]
        reply = ("Сделал: " + "; ".join(done) + "." if done else
                 "Не успел закончить — ответ занял слишком долго. Повторите просьбу.")
    if last is not None and last.actions and (
        not performed or any(not p["ok"] for p in performed[-len(last.actions):])
    ):
        failed = [p for p in performed if not p["ok"]]
        if failed and not reply:
            reply = "Не получилось: " + str(failed[-1].get("summary") or "ошибка")
    if not reply:
        reply = ("Готово." if performed and all(p["ok"] for p in performed)
                 else "Что поменять в презентации?")
    return AgentResult(reply=reply, question=final.question, actions=performed,
                       model_calls=calls, mode="model")


_LOCAL_TOOLS = {"shorter": "shorter", "other_layout": "other_layout",
                "other_chart": "change_chart", "remove_chart": "remove_chart",
                "to_chart": "to_chart", "to_diagram": "to_diagram",
                "larger_chart": "larger_chart", "replace_title": "set_title"}
_SLIDE_RE = re.compile(r"(?:слайд\w*\s*(?:№\s*)?(\d{1,3}))|(?:(\d{1,3})\s*(?:-?м|-?ом)?\s*слайд)",
                       re.I)


def slide_number_in(text: str) -> int | None:
    match = _SLIDE_RE.search(text or "")
    if not match:
        return None
    return int(match.group(1) or match.group(2))


async def run_local(
    message: str, *, workspace: Workspace, selected_slide: int | None, emit: Emit,
) -> AgentResult:
    """Без модели: точные быстрые команды (как у строки команд), иначе честный ответ."""
    from deckdna.assistant.commands import quick_parse

    slide = slide_number_in(message) or selected_slide
    text = re.sub(r"\s*(?:на|в)?\s*" + _SLIDE_RE.pattern, "", message, flags=re.I).strip()
    parsed = quick_parse(text or message)
    tool = _LOCAL_TOOLS.get(parsed.action or "") if parsed and parsed.intent == "slide_change" \
        else None
    if parsed and parsed.intent == "slide_change" and parsed.action and \
            parsed.action.startswith("style:"):
        tool = "restyle"
    if tool is None:
        return AgentResult(
            reply=("Сейчас я без языковой модели и понимаю только короткие команды: "
                   "«сделай короче», «другой макет», «другой график», «заголовок: …». "
                   "Подключите модель в настройках провайдера, и я смогу обсуждать "
                   "презентацию свободно."),
            mode="unavailable",
        )
    if slide is None:
        return AgentResult(reply="Какой слайд поменять? Выберите его или напишите номер.",
                           question=AgentQuestion(text="Какой слайд?"), mode="local")
    action = AgentAction(tool=tool, slide=slide, text=parsed.text,
                         chart_type=parsed.chart_type,
                         style=(parsed.action.split(":", 1)[1] if tool == "restyle"
                                else None))
    emit("agent.action", {"phase": "started", "tool": tool, "slide": slide,
                          "text": status_text(action)})
    try:
        result = await workspace.execute(action)
    except DeckDNAError as exc:
        result = {"ok": False, "error": exc.message}
    record = {"tool": tool, "slide": slide, "ok": bool(result.get("ok")),
              "summary": result.get("summary") or result.get("error"),
              **({"revision": result["revision"]} if "revision" in result else {})}
    emit("agent.action", {"phase": "finished", **record, "text": status_text(action)})
    reply = (record["summary"] or "Готово.") if record["ok"] else \
        f"Не получилось: {record['summary']}"
    return AgentResult(reply=reply, actions=[record], mode="local")


__all__ = [
    "PROMPT", "READ_ONLY", "TOOLS", "AgentAction", "AgentQuestion", "AgentResult",
    "AgentTurn", "Workspace", "numbers_of", "run_agent", "run_local", "slide_number_in",
    "status_text", "ungrounded_numbers",
]
