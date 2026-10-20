"""Revision-safe audit publication and dependent cache invalidation (B11)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from deckdna.errors import DeckDNAError

if TYPE_CHECKING:
    from deckdna.api.app import AuditAccepted, AuditRequest

_ACTIVE: set[tuple[int, str]] = set()


async def create_audit_snapshot(variant_id: str, body: AuditRequest) -> AuditAccepted:
    from deckdna.api import app as api

    # Never hold a thread lock across inference awaits: author edits stay usable.
    token = (id(api.STORE), variant_id)
    with api._variant_lock(variant_id):
        run, variant = api._variant_or_404(variant_id)
        session = (api._require_session(body.provider_session_id, run.detail.project_id)
                   if body.provider_session_id is not None else None)
        existing = next((a for a in api.STORE.audits.values() if a.variant_id == variant_id), None)
        if token in _ACTIVE:
            raise DeckDNAError("state_conflict", "audit already running for this variant",
                               http_status=409)
        if existing is not None and (
            session is None or existing.contextual_status == "completed"
        ):
            job = api._complete_job(api.JobKind.audit, run.detail.project_id,
                                    {"audit_id": existing.id})
            return api.AuditAccepted(audit_id=existing.id, job_id=job.id)
        if variant.deck_artifact_id is None:
            raise DeckDNAError("state_conflict", "audit requires a finished deck", http_status=409)
        deck = api._require(api.STORE.artifacts, variant.deck_artifact_id, "variant deck bytes")
        revision = run.deck_revision.get(variant_id, 1)
        audit = existing.model_copy(deep=True) if existing else api.AuditRun(
            id=api._new_id("audit"), variant_id=variant_id, deck_revision=revision,
            status="completed", deterministic_status="completed",
            contextual_status="pending" if session else "skipped", issue_count=0,
            summary_by_severity=api.IssueSummary(), summary_by_type={},
            config_version=body.config_version, created_at=api._utcnow(),
        )
        issues = [i.model_copy(deep=True) for i in api.STORE.issues.get(audit.id, [])
                  if i.deterministic]
        _ACTIVE.add(token)
    try:
        if existing is None:
            with tempfile.TemporaryDirectory(prefix="deckdna-audit-") as tmpdir:
                deck_path = Path(tmpdir) / deck.filename
                deck_path.write_bytes(deck.data)
                try:
                    raw = api.audit_deck(deck_path, deck_revision=revision, audit_run_id=audit.id)
                except Exception as exc:  # noqa: BLE001 — cannot claim a corrupt deck opens
                    raise DeckDNAError("package_corrupt", "stored deck failed to open",
                                       stage="audit") from exc
            issues = [api._contract_issue(i.to_dict()) for i in raw]
        if session is not None:
            issues += await api._contextual_audit_issues(audit, run, variant, session)
        audit.issue_count = len(issues)
        audit.summary_by_severity, audit.summary_by_type = api._issue_summaries(issues)
        audit.finished_at = api._utcnow()
        with api._variant_lock(variant_id), api._JOB_LOCK:
            current_run, current_variant = api._variant_or_404(variant_id)
            if (current_run is not run or run.deck_revision.get(variant_id, 1) != revision
                    or api._current_deck(run, variant_id).sha256 != deck.sha256):
                raise DeckDNAError(
                    "state_conflict", "deck changed during audit; retry current revision",
                    http_status=409,
                )
            if existing is not None:
                # A dismissal is an author decision, not a new deck revision.
                # Preserve it even when it happened while inference was awaited.
                dismissed = [i for i in api.STORE.issues.get(audit.id, [])
                             if i.status == api.IssueStatus.dismissed]
                ids = {i.id for i in dismissed}
                fingerprints = {fp for i in dismissed if (fp := getattr(i, "fingerprint", None))}
                issues = [i.model_copy(update={"status": api.IssueStatus.dismissed})
                          if i.id in ids or getattr(i, "fingerprint", None) in fingerprints
                          else i for i in issues]
                # Preserve the object identity used by repair clients; publish atomically.
                for field in api.AuditRun.model_fields:
                    setattr(existing, field, getattr(audit, field))
                audit = existing
            api.STORE.audits[audit.id] = audit
            api.STORE.issues[audit.id] = issues
            run.variant_artifacts.setdefault(variant_id, {}).pop("quality_passport", None)
            idx = run.detail.variants.index(current_variant)
            run.detail.variants[idx] = current_variant.model_copy(update={
                "audit_status": "completed",
                "contextual_issues": (
                    sum(not i.deterministic for i in issues) if session is not None
                    else current_variant.contextual_issues
                ),
                "metrics": (current_variant.metrics or api.VariantMetrics()).model_copy(update={
                    "validity": 1.0, "issues_total": len(issues),
                }),
            })
            job = api._complete_job(api.JobKind.audit, run.detail.project_id,
                                    {"audit_id": audit.id})
            return api.AuditAccepted(audit_id=audit.id, job_id=job.id)
    finally:
        with api._variant_lock(variant_id):
            _ACTIVE.discard(token)
