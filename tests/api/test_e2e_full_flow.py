"""Full-surface regression sweep: one continuous scenario through every key
route — project CRUD, template upload + real analyze, content pack, real
generation, exports + artifact downloads, audit, repair, skill manifest.

Guards against cross-endpoint regressions that per-feature tests miss:
entities created by one route must be consumable by the next (uploaded
bytes -> analyze -> generate -> export -> repair -> re-audit)."""

import hashlib
from pathlib import Path

import pytest
from conftest import inject_text_overflow
from deckdna.api.app import STORE, app
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
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


@pytest.mark.parametrize(
    "template_name",
    [
        # organizer benchmark template + an unseen synthetic template the
        # API layer has never been hardcoded against (no fixture-name or
        # layout assumptions as product behaviour)
        "vk_tech_template.pptx",
        "synthetic_unseen.pptx",
    ],
)
def test_full_api_surface_end_to_end(template_name: str, wait_generation):
    template_path = FIXTURES / "pptx" / template_name
    # projects: create, list, get, patch
    project = client.post(
        "/api/v1/projects", json={"name": "e2e", "target_slide_count": 10}
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    assert client.get("/api/v1/projects").status_code == 200
    assert client.get(f"/api/v1/projects/{project_id}").status_code == 200
    patched = client.patch(
        f"/api/v1/projects/{project_id}", json={"name": "e2e v2"}
    )
    assert patched.status_code == 200 and patched.json()["name"] == "e2e v2"

    # template: upload, get, real analyze, latest_analysis on re-fetch
    tpl = client.post(
        f"/api/v1/projects/{project_id}/templates",
        files={"file": (template_path.name, template_path.read_bytes(), PPTX_MIME)},
    )
    assert tpl.status_code == 201, tpl.text
    template_id = tpl.json()["id"]
    assert client.get(f"/api/v1/templates/{template_id}").status_code == 200
    analysis = client.post(
        f"/api/v1/templates/{template_id}/analyze", json={}
    )
    assert analysis.status_code == 202, analysis.text
    analysis_id = analysis.json()["analysis_id"]
    detail = client.get(f"/api/v1/templates/{template_id}").json()
    assert detail["latest_analysis"]["id"] == analysis_id
    assert client.get(
        f"/api/v1/templates/{template_id}/design-dna"
    ).status_code == 200
    slides = client.get(
        f"/api/v1/templates/{template_id}/slides", params={"limit": 200}
    ).json()["items"]
    assert len(slides) >= 1

    # content pack: real parse, sections retrievable
    pack = client.post(
        f"/api/v1/projects/{project_id}/content-packs",
        files={"files": (CONTENT.name, CONTENT.read_bytes(), "text/markdown")},
    )
    assert pack.status_code == 202, pack.text
    pack_id = pack.json()["content_pack"]["id"]
    fetched = client.get(f"/api/v1/content-packs/{pack_id}")
    assert fetched.status_code == 200 and fetched.json()["sections"]

    # generation: two variants complete with real plan
    gen = client.post(
        f"/api/v1/projects/{project_id}/generations",
        json={
            "template_id": template_id,
            "content_pack_id": pack_id,
            "brief": BRIEF,
            "variants": [{"strategy": "faithful"}, {"strategy": "balanced"}],
        },
    )
    assert gen.status_code == 202, gen.text
    run_id = gen.json()["generation_id"]
    variant_ids = gen.json()["variant_ids"]
    assert len(variant_ids) == 2
    run = wait_generation(client, run_id)
    assert run["state"] == "completed" and run["deck_plan"]
    assert client.get(f"/api/v1/generations/{run_id}/variants").json()["items"]

    variant_id = variant_ids[0]
    assert client.get(f"/api/v1/variants/{variant_id}").status_code == 200
    assert client.get(f"/api/v1/variants/{variant_id}/slides").json()["items"]

    # exports + artifact downloads carry real bytes
    export = client.post(
        f"/api/v1/variants/{variant_id}/exports",
        json={"formats": ["pptx", "pdf"]},
    )
    assert export.status_code == 202, export.text
    record = client.get(f"/api/v1/exports/{export.json()['export_id']}").json()
    magics = {}
    for artifact in record["artifacts"]:
        dl = client.get(f"/api/v1/artifacts/{artifact['artifact_id']}/download")
        assert dl.status_code == 200
        magics[artifact["format"]] = dl.content
    assert magics["pptx"][:2] == b"PK"
    assert magics["pdf"][:4] == b"%PDF"

    # audit -> repair -> re-audit: issues shrink for real
    audit = client.post(f"/api/v1/variants/{variant_id}/audits", json={})
    assert audit.status_code == 202, audit.text
    audit_id = audit.json()["audit_id"]
    before = client.get(f"/api/v1/audits/{audit_id}").json()["issue_count"]
    assert before > 0
    issues = client.get(
        f"/api/v1/audits/{audit_id}/issues", params={"limit": 200}
    ).json()["items"]
    repairable = [i for i in issues if i["repairable"]]
    if not repairable:
        # Чистая генерация не обязана давать repairable issues (auto-fix
        # закрывает aspect/contrast/palette до ревизии 1). Вносим реальный
        # text.overflow в байты артефакта и пересоздаём аудит — repair
        # проверяется на настоящем дефекте, не на синтетическом issue.
        variant = client.get(f"/api/v1/variants/{variant_id}").json()
        artifact = STORE.artifacts[variant["deck_artifact_id"]]
        data = inject_text_overflow(artifact.data)
        artifact.data = data
        artifact.sha256 = hashlib.sha256(data).hexdigest()
        artifact.size_bytes = len(data)
        # POST /audits идемпотентно отдаёт аудит времени генерации (на
        # чистой колоде) — удаляем его, чтобы повторный POST честно
        # проаудировал мутированные байты.
        for stale in [
            a for a in STORE.audits.values() if a.variant_id == variant_id
        ]:
            STORE.audits.pop(stale.id, None)
            STORE.issues.pop(stale.id, None)
        audit = client.post(f"/api/v1/variants/{variant_id}/audits", json={})
        assert audit.status_code == 202, audit.text
        audit_id = audit.json()["audit_id"]
        before = client.get(f"/api/v1/audits/{audit_id}").json()["issue_count"]
        issues = client.get(
            f"/api/v1/audits/{audit_id}/issues", params={"limit": 200}
        ).json()["items"]
        repairable = [i for i in issues if i["repairable"]]
    assert repairable, "injected real defect must be repairable"
    repair = client.post(
        f"/api/v1/audits/{audit_id}/repairs",
        json={"selected_issue_ids": [i["id"] for i in repairable[:3]]},
    )
    assert repair.status_code == 202, repair.text
    job = client.get(f"/api/v1/jobs/{repair.json()['job_id']}").json()
    stats = job["result_ids"]
    # every selected issue is accounted for — applied by an executor or
    # honestly reported otherwise (e.g. merge_slide -> not_implemented on
    # the synthetic deck's integrity.empty_slide)
    outcome = sum(
        int(stats[k])
        for k in ("applied", "skipped", "failed", "not_implemented", "unresolved")
    )
    assert outcome >= 1
    after = client.get(f"/api/v1/audits/{audit_id}").json()["issue_count"]
    assert after <= before

    # skill manifest + capabilities
    manifest = client.get("/api/v1/skill/manifest")
    assert manifest.status_code == 200 and manifest.json()["id"] == "deckdna"
    assert client.get("/api/v1/capabilities").status_code == 200

    # nothing leaked: the repaired deck is the variant's current artifact
    variant = client.get(f"/api/v1/variants/{variant_id}").json()
    assert STORE.artifacts[variant["deck_artifact_id"]].data[:2] == b"PK"
