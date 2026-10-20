"""Hybrid assistant command endpoint, backed by the existing typed operations."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from deckdna.assistant.commands import parse_command, quick_parse
from deckdna.errors import DeckDNAError

router = APIRouter(prefix="/api/v1", tags=["assistant"])


class CommandRequest(BaseModel):
    text: str = Field(min_length=1, max_length=6000)
    variant_id: str | None = None
    slide_id: str | None = None
    plan_id: str | None = None
    expected_revision: int | None = Field(default=None, ge=1)
    expected_context_revision: int | None = Field(default=None, ge=1)
    provider_session_id: str | None = None
    use_llm: bool | None = None


@router.post("/projects/{project_id}/commands")
async def submit_command(
    project_id: str, body: CommandRequest, response: Response,
) -> dict[str, Any]:
    from deckdna.api import app as api
    from deckdna.api.changes import ChangeRequest, create_change
    from deckdna.api.dependencies import save_brief

    api._require(api.STORE.projects, project_id, "project")
    context_revision = api.STORE.assistant_progress.get(project_id, {}).get("context_revision", 1)
    # Validate all referenced objects and lease before provider use or mutations.
    if body.provider_session_id:
        api._require_session(body.provider_session_id, project_id)
    if body.variant_id:
        run, _ = api._variant_or_404(body.variant_id)
        if run.detail.project_id != project_id:
            raise DeckDNAError("not_found", "variant not found in project")
    if body.plan_id:
        plan = api._require(api.STORE.plans, body.plan_id, "plan")
        if plan.project_id != project_id:
            raise DeckDNAError("not_found", "plan not found in project")
    gateway = None
    try:
        if quick_parse(body.text) is None:
            if body.use_llm is not False and (
                body.provider_session_id or api._server_provider_ready()
            ):
                gateway = api._gateway_for(body, project_id)
        parsed, parser = await parse_command(body.text, {
            "has_variant": bool(body.variant_id), "has_plan": bool(body.plan_id),
            "selected_slide": body.slide_id,
        }, gateway)
    finally:
        close = getattr(gateway, "aclose", None)
        if close:
            await close()
    result: dict[str, Any] = {"parser": parser, "command": parsed.model_dump(mode="json")}
    if parsed.intent == "update_brief" and parsed.brief:
        if any(e.field == "target_slide_count" and not e.value.isdecimal() for e in parsed.brief):
            return {**result, "status": "clarification", "assistant_note": "Укажите число слайдов."}
        values = {e.field: int(e.value) if e.field == "target_slide_count" else e.value
                  for e in parsed.brief}
        if not values:
            return {**result, "status": "clarification", "assistant_note": "Укажите число слайдов."}
        saved = save_brief(project_id, values,
                           expected=body.expected_context_revision or context_revision)
        return {**result, **saved, "status": "applied",
                "assistant_note": "Обновил задачу. Пересмотрю зависимые план и оформление."}
    if parsed.intent == "slide_change" and parsed.action in {
        "shorter", "other_layout", "other_chart", "style:faithful", "style:balanced",
        "style:visual", "replace_title", "replace_text", "larger_chart",
    }:
        if not body.slide_id or not (body.variant_id or body.plan_id):
            return {**result, "status": "clarification", "assistant_note": "Какой слайд изменить?"}
        if body.variant_id:
            if body.expected_revision is None:
                return {**result, "status": "clarification",
                        "assistant_note": "Откройте актуальную версию слайда перед правкой."}
            queued = await create_change(body.variant_id, ChangeRequest(
                slide_id=body.slide_id, expected_revision=body.expected_revision,
                action=parsed.action, chart_type=parsed.chart_type, text=parsed.text,
            ))
            response.status_code = 202
            return {**result, "status": "accepted", "change": queued,
                    "assistant_note": "Принял правку. Обновлю этот слайд, сохранив остальные."}
        # Shortening while still on the plan uses the existing plan editor.
        if parsed.action == "shorter":
            out = await api.plan_slide_action(body.plan_id, body.slide_id,
                                             api.SlideActionBody(action="shorten"))
            return {**result, "status": "applied", "plan": out.model_dump(mode="json")}
    if (parsed.intent == "plan_action" and parsed.action in {
        "split", "merge_next", "remove", "to_chart", "to_diagram",
    } and body.plan_id and body.slide_id):
        out = await api.plan_slide_action(body.plan_id, body.slide_id,
                                         api.SlideActionBody(action=parsed.action))
        return {**result, "status": "applied", "plan": out.model_dump(mode="json")}
    return {**result, "status": "clarification", "assistant_note": parsed.question or
            "Уточните изменение или выберите действие рядом со слайдом."}
