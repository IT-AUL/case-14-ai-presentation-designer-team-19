"""Project intent revisions and invalidation; historical artifacts are retained."""

from __future__ import annotations

from typing import Any

from deckdna.assistant.brief import BRIEF_FIELDS, normalize_brief
from deckdna.errors import DeckDNAError

_FIELDS = BRIEF_FIELDS


def save_brief(project_id: str, values: dict[str, Any], *, expected: int | None = None,
               origins: dict[str, str] | None = None) -> dict:
    from deckdna.api import app as api

    values = normalize_brief(values, title_hint=True)
    with api._JOB_LOCK:
        project = api._require(api.STORE.projects, project_id, "project")
        marks = api.STORE.assistant_progress.setdefault(project_id, {})
        revision = marks.get("context_revision", 1)
        if expected is not None and expected != revision:
            raise DeckDNAError("state_conflict", "project context changed", http_status=409,
                               details={"actual_revision": revision})
        old = api.STORE.project_briefs.get(project_id, {})
        if ("target_slide_count" in values and "requested_slide_count" not in values
                and old.get("slide_count_policy") in ("exact", "maximum")):
            values["requested_slide_count"] = values["target_slide_count"]
        merged = {**old, **values}
        merged = normalize_brief(merged, title_hint=True)
        changed = sorted(k for k in _FIELDS if old.get(k) != merged.get(k))
        api.STORE.project_briefs[project_id] = merged
        if changed:
            revision += 1
            marks.update(context_revision=revision, invalidated_before=api._utcnow().isoformat())
            marks.pop("interpretation", None)
            marks.pop("context", None)
            # Reset only model outputs which the author has not edited/confirmed.
            for pack_id, records in marks.pop("interpretation_outputs", {}).items():
                pack = api.STORE.content_packs.get(pack_id)
                if pack is None:
                    continue
                rebuilt = []
                for insight in pack.insights:
                    record = records.get(insight.id)
                    if (record and not insight.confirmed
                            and insight.model_dump(mode="json") == record["after"]):
                        insight = type(insight).model_validate(record["before"])
                    rebuilt.append(insight)
                api.STORE.content_packs[pack_id] = pack.model_copy(update={"insights": rebuilt})
            for job in list(api.STORE.jobs.values()):
                if job.project_id != project_id or job.state in api._TERMINAL_JOB_STATES:
                    continue
                api._set_job(job.id, state=api.JobState.canceled, finished_at=api._utcnow())
                api._journal(job.id, "job.state_changed", {
                    "state": "canceled", "superseded": True,
                    "context_revision": revision, "reason": "project_brief_changed",
                })
            for run in api.STORE.runs.values():
                if run.detail.project_id == project_id and run.detail.state in {
                    api.JobState.queued, api.JobState.running,
                }:
                    run.detail = run.detail.model_copy(update={
                        "state": api.JobState.canceled, "finished_at": api._utcnow(),
                    })
            # Questions are evaluated anew against the new brief; known fields
            # are still present and are not asked twice.
            for key in ("answered_pack_id", "deferred_questions_pack_id"):
                marks.pop(key, None)
            api.STORE.projects[project_id] = project.model_copy(
                update={"updated_at": api._utcnow()})
        provenance = marks.setdefault("brief_origins", {})
        for key in values:
            source = "user" if origins is None else origins.get(key)
            if source is None:
                continue
            if source != "default" or key not in provenance:
                if key in changed or provenance.get(key, {}).get("source") != source:
                    provenance[key] = {"source": source, "context_revision": revision}
        return {"brief": merged, "context_revision": revision, "changed_fields": changed,
                "brief_origins": dict(provenance),
                "invalidated": (["plan", "style_previews", "generation", "interpretation"]
                                if changed else [])}


def is_current(project_id: str, created_at) -> bool:
    from deckdna.api import app as api

    if project_id not in api.STORE.projects:
        return False
    boundary = api.STORE.assistant_progress.get(project_id, {}).get("invalidated_before")
    return not boundary or created_at.isoformat() >= boundary
