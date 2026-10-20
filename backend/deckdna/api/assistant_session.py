"""Возобновление помощника и единый job автопилота (ADR-034 §8).

Шаг вычисляется по текущим артефактам. В STORE остаются лишь отметка
просмотренного пака и пропущенные пользователем шаги. Оркестратор
вызывает те же функции планирования, превью и генерации, что REST.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from deckdna.assistant.questions import build_questions
from deckdna.assistant.understanding import phrases
from deckdna.contracts.dataset import InsightKind
from deckdna.contracts.deck_plan import Brief
from deckdna.errors import DeckDNAError

router = APIRouter(prefix="/api/v1", tags=["assistant"])


class SessionArtifacts(BaseModel):
    template_id: str | None = None
    content_pack_id: str | None = None
    brandbook_id: str | None = None
    plan_id: str | None = None
    preview_id: str | None = None
    generation_id: str | None = None


class AssistantSession(BaseModel):
    context_revision: int = 1
    step: int = Field(ge=1, le=7)
    completed_steps: list[int]
    skipped_steps: list[int]
    artifacts: SessionArtifacts
    brief: dict[str, Any]
    brief_origins: dict[str, Any] = Field(default_factory=dict)
    assistant_note: str
    job_id: str | None = None


class AutopilotAccepted(BaseModel):
    job_id: str
    events_url: str
    assistant_note: str


class AutopilotRequest(BaseModel):
    from_step: int = Field(default=1, ge=1, le=7)
    use_llm: bool | None = None
    provider_session_id: str | None = None
    progressive_previews: bool = False


def _current_artifacts(project_id: str) -> dict[str, Any]:
    from deckdna.api import app as api
    from deckdna.api.dependencies import is_current

    store = api.STORE
    project = api._require(store.projects, project_id, "project")
    pack_id = project.content_pack_id

    def matches(candidate: str) -> bool:
        return bool(pack_id and (candidate == pack_id or api._derived_from(candidate, pack_id)))

    plans = [
        p for p in list(store.plans.values())
        if p.project_id == project_id and matches(p.content_pack_id) and p.plans
        and is_current(project_id, p.created_at)
        and (p.template_id is None or p.template_id == project.template_id)
    ]
    plan = max(plans, key=lambda p: getattr(p, "updated_at", None) or p.created_at,
               default=None)
    previews = [
        p for p in list(store.style_previews.values())
        if p["project_id"] == project_id and plan is not None
        and p["plan_id"] == plan.plan_id and p["template_id"] == project.template_id
        and p["created_at"] >= (getattr(plan, "updated_at", None) or plan.created_at).isoformat()
    ]
    # Reuse only the plan revision and branded template actually previewed.
    if previews:
        template_bytes = api._template_bytes(project.template_id, project_id)[0]
        template_digest = hashlib.sha256(template_bytes).hexdigest()
        compatible = []
        for preview in previews:
            entry = next((e for e in plan.plans
                          if e.deck_plan.id == preview.get("deck_plan_id")), None)
            digest = (hashlib.sha256(entry.deck_plan.model_dump_json().encode()).hexdigest()
                      if entry else None)
            if (digest == preview.get("plan_digest")
                    and template_digest == preview.get("template_digest")):
                compatible.append(preview)
        previews = compatible
    preview = max(previews, key=lambda p: p["created_at"], default=None)
    runs = [
        r for r in list(store.runs.values())
        if r.detail.project_id == project_id and matches(r.detail.content_pack_id)
        and is_current(project_id, r.detail.created_at)
        and r.detail.template_id == project.template_id
        and (plan is None or r.detail.created_at >= (
            getattr(plan, "updated_at", None) or plan.created_at
        ))
    ]
    run = max(runs, key=lambda r: r.detail.created_at, default=None)
    return {"project": project, "plan": plan, "preview": preview, "run": run}


@router.get("/projects/{project_id}/session")
async def get_session(project_id: str) -> AssistantSession:
    from deckdna.api import app as api

    current = _current_artifacts(project_id)
    project, plan, preview, run = (current[k] for k in ("project", "plan", "preview", "run"))
    pack_id = plan.content_pack_id if plan else project.content_pack_id
    pack = api.STORE.content_packs.get(pack_id)
    marks = api.STORE.assistant_progress.get(project_id, {})
    brief = dict(api.STORE.project_briefs.get(project_id, {}))
    completed: set[int] = set()
    skipped: set[int] = set()
    step = 1
    cfg = phrases()["session"]
    note = cfg["intake"]
    if project.template_id and pack:
        completed.add(1)
        step, note = 2, cfg["understanding"]
        understood = marks.get("understood_pack_id") == project.content_pack_id
        if understood or plan is not None:
            completed.add(2)
            qs = build_questions(
                pack, brief,
                defer_questions=marks.get("deferred_questions_pack_id") == project.content_pack_id,
            )["questions"]
            if qs and plan is None:
                step, note = 3, cfg["questions"]
            else:
                if marks.get("answered_pack_id") == project.content_pack_id:
                    completed.add(3)
                else:
                    skipped.add(3)
                step, note = 4, cfg["plan"]
    if plan is not None:
        completed.update((1, 2, 4))
        step, note = 5, cfg["style"]
    if preview is not None:
        completed.add(5)
        step, note = 6, cfg["generation"]
    if run is not None:
        step, note = 6, cfg["generation"]
        if run.detail.state == api.JobState.completed:
            completed.add(6)
            step, note = 7, cfg["ready"]
        elif run.detail.state in (api.JobState.failed, api.JobState.canceled):
            note = cfg["retry"]
    active = next((j for j in reversed(list(api.STORE.jobs.values()))
                   if j.project_id == project_id and j.kind == api.JobKind.autopilot
                   and j.state not in api._TERMINAL_JOB_STATES), None)
    if active is not None:
        step, note = 6, cfg["autopilot"]
    # Marks belong to a particular content version; a new upload starts afresh.
    if marks.get("understood_pack_id") == project.content_pack_id:
        skipped.update(marks.get("skipped_steps", []))
    skipped.discard(step)
    completed.difference_update(skipped)
    return AssistantSession.model_validate({
        "context_revision": marks.get("context_revision", 1),
        "brief_origins": marks.get("brief_origins", {}),
        "step": step, "completed_steps": sorted(completed), "skipped_steps": sorted(skipped),
        "artifacts": {
            "template_id": project.template_id, "content_pack_id": pack_id,
            "brandbook_id": (api.STORE.project_brand.get(project_id) or {}).get("brandbook_id"),
            "plan_id": plan.plan_id if plan else None,
            "preview_id": preview["preview_id"] if preview else None,
            "generation_id": run.detail.id if run else None,
        },
        "brief": brief, "assistant_note": note, "job_id": active.id if active else None,
    })


def _execute_autopilot(job_id: str, project_id: str, body: AutopilotRequest) -> None:
    from deckdna.api import app as api

    def stage(name: str, progress: float) -> None:
        if api.STORE.jobs[job_id].state == api.JobState.canceled:
            raise api._PlanCanceled()
        api._set_job(job_id, state=api.JobState.running, stage=name, progress=progress)
        api._journal(job_id, "stage.started", {"stage": name})

    try:
        api._set_job(job_id, state=api.JobState.running, started_at=api._utcnow())
        current = _current_artifacts(project_id)
        project, plan = current["project"], current["plan"]
        if body.from_step <= 3:
            plan = None
            current["preview"] = None
        pack_id = plan.content_pack_id if plan else project.content_pack_id
        pack = api.STORE.content_packs[pack_id]
        marks = api.STORE.assistant_progress.setdefault(project_id, {})
        stage("understanding", 0.05)
        if plan is None:
            understanding = asyncio.run(api.get_understanding(
                pack_id, use_llm=(body.use_llm if body.use_llm is not None
                                  else api._server_provider_ready()),
                provider_session_id=body.provider_session_id,
            ))
            api._journal(job_id, "understanding.ready", {
                "content_pack_id": pack_id, "summary": understanding["summary"],
            })
            pack = api.STORE.content_packs[pack_id]
            threshold = float(phrases()["autopilot"]["insight_strength"])
            safe = [
                i for i in pack.insights if i.strength >= threshold and not i.implication
                and i.kind == InsightKind.fact and not i.needs_confirmation and not i.confirmed
            ]
            for insight in safe:
                patch = api.InsightPatch(confirmed=True)
                asyncio.run(api.patch_insight(pack_id, insight.id, patch))
            api._journal(job_id, "insights.confirmed", {
                "insight_ids": [i.id for i in safe], "recommendations_confirmed": False,
            })
            stage("questions", 0.1)
            known = dict(api.STORE.project_briefs.get(project_id, {}))
            policy = build_questions(api.STORE.content_packs[pack_id], known)
            # Автопилот делегирован пользователем, но его предпочтения
            # не выдумываются из рекомендованных чипов анкеты.
            answers: dict[str, Any] = {}
            known = {**policy["suggested_brief"], **known}
            # No unresolved interactive 'ask' can block an autonomous job.
            if known.get("content_fill") in (None, "ask"):
                known["content_fill"] = "expand"
            known.setdefault("target_slide_count", policy["suggested_brief"]["target_slide_count"])
            known.setdefault("purpose", pack.title_hint or "")
            known.setdefault("audience", "")
            known.setdefault("language", pack.language or "ru")
            brief = Brief.model_validate(known)
            api._remember_brief(project_id, brief.model_dump(mode="json", exclude_none=True),
                                source="default")
            api._journal(job_id, "questions.answered", {
                "answers": answers, "automatic": True,
                "suggested_brief": policy["suggested_brief"],
            })
            marks.update(understood_pack_id=project.content_pack_id,
                         answered_pack_id=project.content_pack_id, skipped_steps=[2, 3, 4, 5],
                         deferred_questions_pack_id=project.content_pack_id)
            stage("plan", 0.2)
            pack = api.STORE.content_packs[pack_id]
            request = api.PlanRequest(
                content_pack_id=pack_id, template_id=project.template_id, brief=brief,
                strategies=["balanced"], use_llm=body.use_llm,
                provider_session_id=body.provider_session_id,
            )
            plan = asyncio.run(api._build_plan_set(
                project_id, request, pack, api._gateway_for(request, project_id),
                on_event=lambda kind, data: api._journal(job_id, kind, data),
                is_canceled=lambda: api.STORE.jobs[job_id].state == api.JobState.canceled,
            ))
            if not plan.plans:
                raise DeckDNAError("invalid_input", "autopilot did not produce a deck plan")
        else:
            brief = plan.plans[0].deck_plan.brief
            marks.update(understood_pack_id=project.content_pack_id,
                         skipped_steps=list(range(max(4, body.from_step), 6)))
        api._set_job(job_id, result_ids={"plan_id": plan.plan_id})
        preview = current["preview"]
        entry = next((e for e in plan.plans if preview is not None
                      and e.deck_plan.id == preview.get("deck_plan_id")), plan.plans[0])
        if preview is not None:
            strategy = preview["recommended"]
            chosen = next(s for s in preview["styles"] if s["strategy"] == strategy)
            if chosen.get("error"):
                raise DeckDNAError("internal_error", chosen["error"], stage="style_preview")
        else:
            from deckdna.generation.preview import recommend_style

            strategy, _ = recommend_style(entry.deck_plan)
        preview_id = preview["preview_id"] if preview is not None else None
        api._journal(job_id, "style.selected", {
            "strategy": strategy, "preview_id": preview_id, "automatic": True,
            "previews_skipped": preview is None,
        })
        generation = api.GenerationCreate(
            template_id=project.template_id, content_pack_id=plan.content_pack_id,
            brief=brief, variants=[api.VariantRequest(strategy=strategy)],
            deck_plan=entry.deck_plan.model_copy(deep=True), preview_id=preview_id,
            use_llm=body.use_llm, provider_session_id=body.provider_session_id,
            progressive_previews=body.progressive_previews,
        )
        stage("generation", 0.5)
        api._set_job(job_id, result_ids={"plan_id": plan.plan_id,
                                       **({"preview_id": preview_id} if preview_id else {})})
        gateway = api._gateway_for(generation, project_id)
        run = api._create_generation(project, generation, parent_run_id=None, job_id=job_id)
        # This inline plan came from the saved PlanSet, not from a user edit.
        run.plan_source = "plan_set"
        # Executes in this worker and writes to the SAME job and sequence journal.
        api._execute_generation(run.detail.id, gateway)
    except api._PlanCanceled:
        api._journal(job_id, "job.state_changed", {"state": "canceled"})
    except Exception as exc:  # noqa: BLE001 — always settle a background job
        if api.STORE.jobs[job_id].state == api.JobState.canceled:
            api._journal(job_id, "job.state_changed", {"state": "canceled"})
            return
        error = api.JobError(
            code=exc.code if isinstance(exc, DeckDNAError) else "internal_error",
            message=(exc.message if isinstance(exc, DeckDNAError)
                     else f"{type(exc).__name__}: {exc}"),
            stage=api.STORE.jobs[job_id].stage,
        )
        api._set_job(job_id, state=api.JobState.failed, error=error, finished_at=api._utcnow())
        api._journal(job_id, "job.failed", {"state": "failed", "error": error.model_dump()})
    finally:
        api._persist()


@router.post("/projects/{project_id}/session/autopilot", status_code=202)
async def autopilot(project_id: str, body: AutopilotRequest) -> AutopilotAccepted:
    from deckdna.api import app as api

    project = api._require(api.STORE.projects, project_id, "project")
    if not project.template_id or not project.content_pack_id:
        raise DeckDNAError(
            "invalid_input", "autopilot needs a template and content", http_status=422,
        )
    api._require_owned_template(project.template_id, project_id)
    api._require_owned_content_pack(project.content_pack_id, project_id)
    if body.provider_session_id:
        api._require_session(body.provider_session_id, project_id)
    with api._JOB_LOCK:
        job = next((j for j in api.STORE.jobs.values()
                    if j.project_id == project_id and j.kind == api.JobKind.autopilot
                    and j.state not in api._TERMINAL_JOB_STATES), None)
        if job is None:
            job = api.Job(id=api._new_id("job"), kind=api.JobKind.autopilot,
                          state=api.JobState.queued, progress=0.0,
                          project_id=project_id, created_at=api._utcnow())
            api.STORE.jobs[job.id] = job
            api._journal(job.id, "job.state_changed", {"state": "queued"})
            api._PLAN_POOL.submit(_execute_autopilot, job.id, project_id, body)
    return AutopilotAccepted(
        job_id=job.id, events_url=f"/api/v1/jobs/{job.id}/events",
        assistant_note=phrases()["session"]["autopilot"],
    )
