"""Repair contract test: POST /audits/{id}/repairs must really mutate the
variant's pptx (plan_repairs -> apply_repairs), bump deck_revision, and
re-audit the repaired deck — issue statuses follow the real audit, they are
not hand-marked "fixed"."""

import hashlib
from pathlib import Path

from conftest import inject_text_overflow
from deckdna.api import app as app_module
from deckdna.api.app import STORE, app
from deckdna.contracts.audit_issue import AuditIssue, Severity, Status
from deckdna.planning.config import GenerationConfig
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TEMPLATE = FIXTURES / "pptx" / "vk_tech_template.pptx"
CONTENT = FIXTURES / "content" / "poc_article.md"
PPTX_MIME = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)

client = TestClient(app)

BRIEF = {
    "purpose": "Представить решение",
    "audience": "эксперты и жюри",
    "language": "ru",
    "target_slide_count": 10,
}


def _generate_variant(wait_generation) -> str:
    """Create project -> template -> pack -> generation; return variant_id."""
    project = client.post(
        "/api/v1/projects", json={"name": "repair test", "target_slide_count": 10}
    ).json()
    tpl = client.post(
        f"/api/v1/projects/{project['id']}/templates",
        files={"file": (TEMPLATE.name, TEMPLATE.read_bytes(), PPTX_MIME)},
    )
    assert tpl.status_code == 201, tpl.text
    pack = client.post(
        f"/api/v1/projects/{project['id']}/content-packs",
        files={"files": (CONTENT.name, CONTENT.read_bytes(), "text/markdown")},
    )
    assert pack.status_code == 202, pack.text
    gen = client.post(
        f"/api/v1/projects/{project['id']}/generations",
        json={
            "template_id": tpl.json()["id"],
            "content_pack_id": pack.json()["content_pack"]["id"],
            "brief": BRIEF,
            "variants": [{"strategy": "balanced"}],
        },
    )
    assert gen.status_code == 202, gen.text
    wait_generation(client, gen.json()["generation_id"])
    return gen.json()["variant_ids"][0]


def _variant_with_audit(wait_generation) -> tuple[str, str]:
    """Create project -> template -> pack -> generation -> audit.

    Returns (variant_id, audit_id).
    """
    variant_id = _generate_variant(wait_generation)
    audit = client.post(f"/api/v1/variants/{variant_id}/audits", json={})
    assert audit.status_code == 202, audit.text
    return variant_id, audit.json()["audit_id"]


def _corrupt_variant_deck(variant_id: str) -> None:
    """Вносит реальный text.overflow в байты колоды варианта в STORE.

    Чистая генерация на фикстурном шаблоне не обязана давать repairable
    issues (auto-fix закрывает их до ревизии 1) — audit тогда нечего
    чинить. Мутируем сам артефакт (data + sha256 + size_bytes), чтобы
    детерминированный аудит увидел настоящий дефект, а не синтетическую
    запись issue."""
    variant = client.get(f"/api/v1/variants/{variant_id}").json()
    artifact = STORE.artifacts[variant["deck_artifact_id"]]
    data = inject_text_overflow(artifact.data)
    artifact.data = data
    artifact.sha256 = hashlib.sha256(data).hexdigest()
    artifact.size_bytes = len(data)
    # POST /audits идемпотентно отдаёт аудит, созданный внутри генерации
    # на чистой колоде — удаляем его, чтобы следующий POST честно
    # проаудировал мутированные байты (как API делает при удалении
    # варианта).
    for stale in [a for a in STORE.audits.values() if a.variant_id == variant_id]:
        STORE.audits.pop(stale.id, None)
        STORE.issues.pop(stale.id, None)


def _issue_items(audit_id: str) -> list[dict]:
    return client.get(
        f"/api/v1/audits/{audit_id}/issues", params={"limit": 200}
    ).json()["items"]


def test_repair_applies_real_fixes_and_reaudits(wait_generation):
    variant_id = _generate_variant(wait_generation)
    _corrupt_variant_deck(variant_id)
    audit = client.post(f"/api/v1/variants/{variant_id}/audits", json={})
    assert audit.status_code == 202, audit.text
    audit_id = audit.json()["audit_id"]

    issues = _issue_items(audit_id)
    repairable = [
        i
        for i in issues
        if i["repairable"]
        and i["rule_code"] in {"text.overflow", "image.aspect_ratio"}
    ]
    assert repairable, "fixture deck must produce repairable issues"
    selected = repairable[:3]

    variant = client.get(f"/api/v1/variants/{variant_id}").json()
    old_deck_id = variant["deck_artifact_id"]
    old_sha = STORE.artifacts[old_deck_id].sha256
    audit_before = client.get(f"/api/v1/audits/{audit_id}").json()

    repair = client.post(
        f"/api/v1/audits/{audit_id}/repairs",
        json={"selected_issue_ids": [i["id"] for i in selected]},
    )
    assert repair.status_code == 202, repair.text
    body = repair.json()
    assert body["deck_revision"] == audit_before["deck_revision"] + 1

    job = client.get(f"/api/v1/jobs/{body['job_id']}").json()
    assert job["state"] == "completed"
    assert int(job["result_ids"]["applied"]) >= 1

    # the variant deck really changed — new artifact, different sha256
    variant = client.get(f"/api/v1/variants/{variant_id}").json()
    assert variant["deck_artifact_id"] != old_deck_id
    new_sha = STORE.artifacts[variant["deck_artifact_id"]].sha256
    assert new_sha != old_sha
    deck = client.get(f"/api/v1/artifacts/{variant['deck_artifact_id']}/download")
    assert deck.status_code == 200 and deck.content[:2] == b"PK"

    # re-audit on the repaired deck: fewer issues than before, and nothing
    # is hand-marked "fixed" — remaining issues are real open findings
    audit_after = client.get(f"/api/v1/audits/{audit_id}").json()
    assert audit_after["issue_count"] < audit_before["issue_count"]
    assert audit_after["deck_revision"] == body["deck_revision"]
    remaining = _issue_items(audit_id)
    assert len(remaining) == audit_after["issue_count"]
    assert all(i["status"] == "open" for i in remaining)
    assert all(i["deck_revision"] == body["deck_revision"] for i in remaining)


def test_repair_not_implemented_action_stays_open(wait_generation):
    variant_id, audit_id = _variant_with_audit(wait_generation)

    # native_rebuild has no executor — such an issue must stay open after
    # repair. Inject one into this audit's issue list (cannot be produced
    # by the deterministic audit on the fixture deck).
    audit = STORE.audits[audit_id]
    fake_issue = AuditIssue(
        schema_version="1.0",
        id="iss_unfixable",
        audit_run_id=audit_id,
        deck_revision=audit.deck_revision,
        rule_code="editability.raster_only",
        deterministic=True,
        severity=Severity.error,
        slide_id="12345",
        slide_index=0,
        message="Synthetic raster-only slide for the not_implemented path",
        status=Status.open,
        repairable=True,
    )
    STORE.issues[audit_id].append(fake_issue)

    try:
        old_revision = audit.deck_revision
        repair = client.post(
            f"/api/v1/audits/{audit_id}/repairs",
            json={"selected_issue_ids": [fake_issue.id]},
        )
        assert repair.status_code == 202, repair.text
        job = client.get(f"/api/v1/jobs/{repair.json()['job_id']}").json()
        assert int(job["result_ids"]["not_implemented"]) >= 1
        assert int(job["result_ids"]["applied"]) == 0
        assert repair.json()["deck_revision"] == old_revision + 1
    finally:
        STORE.issues[audit_id] = [
            i for i in STORE.issues[audit_id] if i.id != fake_issue.id
        ]


def test_repair_unmapped_rule_goes_to_unresolved(wait_generation):
    variant_id, audit_id = _variant_with_audit(wait_generation)

    # layout.edge_margin has no planner handler — such an issue must land
    # in `unresolved` (not applied/not_implemented) without a 500 and
    # without pretending to be fixed. Inject one into this audit's list.
    audit = STORE.audits[audit_id]
    fake_issue = AuditIssue(
        schema_version="1.0",
        id="iss_unmapped",
        audit_run_id=audit_id,
        deck_revision=audit.deck_revision,
        rule_code="layout.edge_margin",
        deterministic=True,
        severity=Severity.warning,
        slide_id="12345",
        slide_index=0,
        message="Synthetic edge-margin issue for the unmapped-rule path",
        status=Status.open,
        repairable=True,
    )
    STORE.issues[audit_id].append(fake_issue)

    try:
        old_revision = audit.deck_revision
        repair = client.post(
            f"/api/v1/audits/{audit_id}/repairs",
            json={"selected_issue_ids": [fake_issue.id]},
        )
        assert repair.status_code == 202, repair.text
        job = client.get(f"/api/v1/jobs/{repair.json()['job_id']}").json()
        assert job["state"] == "completed"
        assert int(job["result_ids"]["unresolved"]) == 1
        assert int(job["result_ids"]["applied"]) == 0
        assert repair.json()["deck_revision"] == old_revision + 1
    finally:
        STORE.issues[audit_id] = [
            i for i in STORE.issues[audit_id] if i.id != fake_issue.id
        ]


def test_repair_invalidates_contextual_status_for_new_revision(
    wait_generation, monkeypatch
):
    variant_id, audit_id = _variant_with_audit(wait_generation)
    audit = STORE.audits[audit_id]
    old_revision = audit.deck_revision
    audit.contextual_status = "completed"
    fake_issue = AuditIssue(
        schema_version="1.0",
        id="iss_contextual_revision_probe",
        audit_run_id=audit_id,
        deck_revision=old_revision,
        rule_code="layout.edge_margin",
        deterministic=True,
        severity=Severity.warning,
        slide_id="12345",
        slide_index=0,
        message="Synthetic issue to trigger a revision without a model",
        status=Status.open,
        repairable=True,
    )
    STORE.issues[audit_id].append(fake_issue)

    repair = client.post(
        f"/api/v1/audits/{audit_id}/repairs",
        json={"selected_issue_ids": [fake_issue.id]},
    )
    assert repair.status_code == 202, repair.text
    assert repair.json()["deck_revision"] == old_revision + 1
    assert audit.contextual_status == "pending"
    assert client.get(f"/api/v1/audits/{audit_id}").json()["contextual_status"] == "pending"
    assert all(issue.deterministic for issue in STORE.issues[audit_id])

    called = []

    async def fake_contextual(current_audit, run, variant, session):
        called.append((current_audit.deck_revision, variant.id))
        current_audit.contextual_status = "completed"
        return []

    monkeypatch.setattr(app_module, "_contextual_audit_issues", fake_contextual)
    monkeypatch.setattr(
        app_module,
        "_require_session",
        lambda session_id, project_id: object(),
    )
    upgraded = client.post(
        f"/api/v1/variants/{variant_id}/audits",
        json={"provider_session_id": "local-test-session"},
    )
    assert upgraded.status_code == 202, upgraded.text
    assert called == [(old_revision + 1, variant_id)]
    assert audit.contextual_status == "completed"


def test_repair_loads_protected_slide_config(wait_generation, monkeypatch):
    """pipeline.generate() threads protected_slide_
    indices into auto-fix/compose, but API repair used to call
    apply_repairs() with no `protected` argument at all -- config was
    never loaded on this path, so a protected slide's issues were fair
    game. Spies on apply_repairs to confirm the loaded config now
    actually reaches it, rather than re-testing apply_repairs' own
    protection enforcement (already covered by tests/pptx/
    test_protected_slides.py)."""
    variant_id, audit_id = _variant_with_audit(wait_generation)
    monkeypatch.setattr(
        app_module,
        "load_generation_config",
        lambda: GenerationConfig(protected_slide_indices=[0, 2]),
    )
    seen: dict = {}
    real_apply_repairs = app_module.apply_repairs

    def spy(pptx_path, actions, out_path, protected=None):
        seen["protected"] = protected
        return real_apply_repairs(pptx_path, actions, out_path, protected=protected)

    monkeypatch.setattr(app_module, "apply_repairs", spy)

    # layout.edge_margin has no planner handler -- plan.actions stays
    # empty regardless of protection, so this only isolates the wiring.
    audit = STORE.audits[audit_id]
    fake_issue = AuditIssue(
        schema_version="1.0",
        id="iss_protected_wiring",
        audit_run_id=audit_id,
        deck_revision=audit.deck_revision,
        rule_code="layout.edge_margin",
        deterministic=True,
        severity=Severity.warning,
        slide_id="12345",
        slide_index=0,
        message="Synthetic issue to isolate the protected-config wiring",
        status=Status.open,
        repairable=True,
    )
    STORE.issues[audit_id].append(fake_issue)
    try:
        repair = client.post(
            f"/api/v1/audits/{audit_id}/repairs",
            json={"selected_issue_ids": [fake_issue.id]},
        )
        assert repair.status_code == 202, repair.text
        assert seen["protected"] == frozenset({0, 2})
    finally:
        STORE.issues[audit_id] = [
            i for i in STORE.issues[audit_id] if i.id != fake_issue.id
        ]


def test_repair_unknown_issue_id(wait_generation):
    _, audit_id = _variant_with_audit(wait_generation)
    repair = client.post(
        f"/api/v1/audits/{audit_id}/repairs",
        json={"selected_issue_ids": ["iss_no_such_issue"]},
    )
    assert repair.status_code == 422
    assert repair.json()["error"]["code"] == "invalid_input"
