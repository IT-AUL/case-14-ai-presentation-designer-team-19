"""Revisioned local-change queue; waits for the base deck without polling threads."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from deckdna.errors import DeckDNAError

router = APIRouter(prefix="/api/v1", tags=["changes"])
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="deckdna-changes")


class ChangeRequest(BaseModel):
    slide_id: str = Field(min_length=1)
    expected_revision: int = Field(ge=1)
    action: Literal["shorter", "other_layout", "other_chart", "style:faithful",
                    "style:balanced", "style:visual", "replace_title", "replace_text",
                    "larger_chart"]
    chart_type: str | None = None
    text: str | None = Field(default=None, max_length=6000)


def _api():
    from deckdna.api import app

    return app


def accepted_revision(run, variant_id: str) -> int:
    return max(
        [run.deck_revision.get(variant_id, 1)] + [
            item["target_revision"] for item in getattr(run, "changes", {}).get(variant_id, [])
            if item["state"] in {"queued", "running", "completed"}
        ]
    )


def _settle(item: dict, state: str, *, error: dict | None = None) -> None:
    api = _api()
    job_id = item["job_id"]
    if (job_id in api.STORE.jobs and api.STORE.jobs[job_id].state == api.JobState.canceled
            and state != "canceled"):
        item.update(state="canceled", error=error)
        return
    item.update(state=state, error=error)
    if job_id not in api.STORE.jobs:
        return
    api._set_job(job_id, state=api.JobState(state), finished_at=api._utcnow(),
                 progress=1.0 if state == "completed" else 0.0,
                 error=api.JobError(**error) if error else None)
    api._journal(job_id, "job.completed" if state == "completed" else "job.state_changed", {
        "state": state, "change_id": item["id"], "target_revision": item["target_revision"],
        **({"error": error} if error else {}),
    })


def _restore_cards(run, variant_id: str) -> None:
    if any(i["state"] in {"queued", "running"} for i in run.changes.get(variant_id, [])):
        return
    revision = run.deck_revision.get(variant_id, 1)
    for card in run.slide_drafts.get(variant_id, {}).values():
        card["status"] = "rendered" if run.base_ready.get(variant_id) else "draft"
        card["revision"] = revision


def settle_unavailable(run) -> None:
    api = _api()
    with api._JOB_LOCK:
        for variant_id, items in getattr(run, "changes", {}).items():
            if variant_id in run.variant_artifacts:
                continue
            for item in items:
                if item["state"] == "queued":
                    _settle(item, "canceled", error={"code": "state_conflict",
                            "message": "Base generation did not produce this variant."})


def drain(run_id: str, variant_id: str) -> None:
    api = _api()
    with api._variant_lock(variant_id):
        run = api.STORE.runs.get(run_id)
        if run is None:
            return
        # The generation worker calls us after slides/artifacts are attached.
        if not run.base_ready.get(variant_id):
            return
        while True:
            with api._JOB_LOCK:
                pending = next((i for i in run.changes.get(variant_id, [])
                                if i["state"] == "queued" and (
                                    not api.settings.durable_queue or i.get('durable_ready'))),
                               None)
                if pending is None:
                    return
                job = api.STORE.jobs.get(run.detail.job_id)
                if job is None or job.state in {api.JobState.canceled, api.JobState.failed}:
                    _settle(pending, "canceled", error={"code": "state_conflict",
                            "message": "Base generation was canceled or failed."})
                    continue
                pending["state"] = "running"
                api._set_job(pending["job_id"], state=api.JobState.running,
                             started_at=api._utcnow())
                api._journal(pending["job_id"], "job.state_changed", {"state": "running"})
            try:
                if api.settings.durable_queue:
                    from deckdna.api.dispatch import checkpoint

                    checkpoint()
                result = api._regenerate_variant_slide(
                    variant_id, pending["slide_id"], api.RegenerateRequest(
                        action=pending["action"], chart_type=pending.get("chart_type"),
                        text=pending.get("text"),
                        expected_revision=pending["expected_revision"],
                    ), queued=True,
                )
                # No stale exported PDF or passport can masquerade as this revision.
                api._ensure_pdf(run, variant_id)
                api._ensure_passport(run, variant_id)
                api._ensure_previews(run, variant_id)
                with api._JOB_LOCK:
                    if api.STORE.jobs[pending["job_id"]].state == api.JobState.canceled:
                        pending["state"] = "canceled"
                        continue
                    pending["result"] = result
                    target = api._variant_slide(variant_id, pending["slide_id"])
                    status = "superseded" if any(
                        c["state"] == "queued" for c in run.changes.get(variant_id, [])
                    ) else "rendered"
                    event = {"variant_id": variant_id, "slide_id": pending["slide_id"],
                             "slide_key": f"{variant_id}:{target.index}", "index": target.index,
                             "revision": result["deck_revision"], "preview_revision": 1,
                             "preview_artifact_id": target.preview_artifact_id,
                             "status": status, "change_id": pending["id"]}
                    run.slide_drafts.setdefault(variant_id, {})[target.index] = event
                    for card in api.STORE.slides.get(variant_id, []):
                        run.slide_drafts.setdefault(variant_id, {})[card.index] = {
                            "variant_id": variant_id, "slide_id": card.slide_plan_id,
                            "slide_key": f"{variant_id}:{card.index}", "index": card.index,
                            "revision": result["deck_revision"], "preview_revision": 1,
                            "preview_artifact_id": card.preview_artifact_id, "status": status,
                        }
                    api._journal(pending["job_id"], "slide.updated", event)
                    if job.state not in api._TERMINAL_JOB_STATES:
                        api._journal(job.id, "slide.updated", event)
                    _settle(pending, "completed")
            except Exception as exc:  # noqa: BLE001 — settle every queued operation
                error = {"code": exc.code if isinstance(exc, DeckDNAError) else "internal_error",
                         "message": exc.message if isinstance(exc, DeckDNAError)
                         else "Local change failed; the last committed revision remains available."}
                with api._JOB_LOCK:
                    _settle(pending, "failed", error=error)
                    for dependent in run.changes.get(variant_id, []):
                        if dependent["state"] == "queued":
                            _settle(dependent, "canceled", error={"code": "state_conflict",
                                    "message": "A preceding change failed; resubmit edits."})
                    _restore_cards(run, variant_id)
                return
            finally:
                api._persist()


@router.post("/variants/{variant_id}/changes", status_code=202)
async def create_change(variant_id: str, body: ChangeRequest) -> dict[str, Any]:
    api = _api()
    run, variant = api._variant_or_404(variant_id)
    with api._JOB_LOCK:
        if not hasattr(run, "changes"):
            run.changes = {}
        if not hasattr(run, "base_ready"):
            run.base_ready = {variant_id: bool(api.STORE.slides.get(variant_id))}
        from deckdna.api.dependencies import is_current

        if not is_current(run.detail.project_id, run.detail.created_at):
            raise DeckDNAError("state_conflict", "variant belongs to an older project brief",
                               http_status=409)
        base_job = api.STORE.jobs[run.detail.job_id]
        if variant.status in {api.JobState.failed, api.JobState.canceled} or base_job.state in {
            api.JobState.failed, api.JobState.canceled,
        }:
            raise DeckDNAError("state_conflict", "generation is not editable", http_status=409)
        plan = run.variant_plans.get(variant_id) or run.supplied_plans.get(
            run.variant_strategy.get(variant_id, ""))
        draft_ids = {d["slide_id"] for d in run.slide_drafts.get(variant_id, {}).values()}
        if not (plan and any(s.id == body.slide_id for s in plan.slides)):
            if body.slide_id not in draft_ids:
                raise DeckDNAError("not_found", "slide is not in this variant plan")
        if body.slide_id.startswith("protected:"):
            raise DeckDNAError("protected_slide", "protected slides cannot be changed",
                               http_status=422)
        actual = accepted_revision(run, variant_id)
        if body.expected_revision != actual:
            raise DeckDNAError("state_conflict", "accepted revision changed", http_status=409,
                               details={"actual_revision": actual})
        if len([i for i in run.changes.get(variant_id, [])
                if i["state"] in {"queued", "running"}]) >= 20:
            raise DeckDNAError("state_conflict", "too many pending changes", http_status=409)
        change_id, job_id = api._new_id("chg"), api._new_id("job")
        item = {"id": change_id, "job_id": job_id, "variant_id": variant_id,
                **body.model_dump(), "target_revision": actual + 1, "state": "queued",
                "created_at": api._utcnow().isoformat(), "result": None, "error": None}
        run.changes.setdefault(variant_id, []).append(item)
        api.STORE.jobs[job_id] = api.Job(
            id=job_id, kind=api.JobKind.repair, state=api.JobState.queued,
            stage="slide_change", project_id=run.detail.project_id,
            created_at=api._utcnow(), progress=0.0,
            result_ids={"change_id": change_id, "variant_id": variant_id,
                        "generation_id": run.detail.id},
        )
        api._journal(job_id, "job.state_changed", {"state": "queued", "change_id": change_id})
        for card in run.slide_drafts.get(variant_id, {}).values():
            card["status"] = "superseded"
        if base_job.state not in api._TERMINAL_JOB_STATES:
            api._journal(base_job.id, "change.queued", {
                "variant_id": variant_id, "change_id": change_id,
                "target_revision": actual + 1, "slide_id": body.slide_id,
            })
        ready = bool(run.base_ready.get(variant_id))
    if api.settings.durable_queue:
        from deckdna.api.dispatch import accept

        try:
            await accept(job_id)
        except DeckDNAError:
            with api._JOB_LOCK:
                _settle(item, 'failed', error={'code': 'internal_error',
                        'message': 'Durable enqueue failed; resubmit from accepted revision.'})
                _restore_cards(run, variant_id)
            raise
    elif ready:
        _POOL.submit(drain, run.detail.id, variant_id)
    return {**dict(item), "events_url": f"/api/v1/jobs/{job_id}/events"}


@router.get("/variants/{variant_id}/changes")
async def list_changes(variant_id: str) -> dict[str, Any]:
    api = _api()
    run, _ = api._variant_or_404(variant_id)
    with api._JOB_LOCK:
        return {"revision": run.deck_revision.get(variant_id, 1),
                "accepted_revision": accepted_revision(run, variant_id),
                "items": [dict(item) for item in getattr(run, "changes", {}).get(variant_id, [])]}


@router.post("/variants/{variant_id}/changes/{change_id}/cancel")
async def cancel_change(variant_id: str, change_id: str) -> dict[str, Any]:
    api = _api()
    run, _ = api._variant_or_404(variant_id)
    with api._JOB_LOCK:
        items = getattr(run, "changes", {}).get(variant_id, [])
        item = next((i for i in items if i["id"] == change_id), None)
        if item is None:
            raise DeckDNAError("not_found", "change not found")
        if item["state"] != "queued":
            raise DeckDNAError("state_conflict", "only queued changes can be canceled",
                               http_status=409)
        for dependent in items[items.index(item):]:
            if dependent["state"] == "queued":
                _settle(dependent, "canceled", error={"code": "state_conflict",
                        "message": "A preceding queued change was canceled."})
        _restore_cards(run, variant_id)
        return dict(item)
