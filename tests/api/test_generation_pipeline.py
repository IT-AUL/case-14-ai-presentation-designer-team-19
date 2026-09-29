"""Generation contract test: POST /projects/{id}/generations must run the real
deckdna.generation pipeline (parse -> plan -> compose -> audit -> render ->
passport) on the uploaded template + content, and every artifact must be
downloadable through the artifact endpoints."""

from pathlib import Path

from deckdna.api.app import app
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


def _project_with_inputs() -> tuple[str, str, str]:
    project = client.post(
        "/api/v1/projects", json={"name": "gen test", "target_slide_count": 10}
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
    return project["id"], tpl.json()["id"], pack.json()["content_pack"]["id"]


def _generate(project_id: str, template_id: str, pack_id: str) -> dict:
    response = client.post(
        f"/api/v1/projects/{project_id}/generations",
        json={
            "template_id": template_id,
            "content_pack_id": pack_id,
            "brief": BRIEF,
            "variants": [{"strategy": "balanced"}],
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_generation_runs_real_pipeline(wait_generation):
    project_id, template_id, pack_id = _project_with_inputs()
    accepted = _generate(project_id, template_id, pack_id)

    run = wait_generation(client, accepted["generation_id"])
    assert run["state"] == "completed"
    job = client.get(f"/api/v1/jobs/{run['job_id']}").json()
    assert job["state"] == "completed"
    assert job["result_ids"]["deck_plan_id"]

    # real DeckPlan from story_director, not the stub
    plan = run["deck_plan"]
    assert plan is not None and len(plan["slides"]) == 10
    assert plan["brief"]["purpose"] == BRIEF["purpose"]


def test_variant_artifacts_are_real_and_downloadable(wait_generation):
    project_id, template_id, pack_id = _project_with_inputs()
    accepted = _generate(project_id, template_id, pack_id)

    run = wait_generation(client, accepted["generation_id"])
    assert run["state"] == "completed"
    variant_id = accepted["variant_ids"][0]
    variant = client.get(f"/api/v1/variants/{variant_id}").json()
    assert variant["status"] == "completed"
    assert variant["audit_status"] == "completed"
    assert variant["metrics"]["editability_pei"] is not None

    deck = client.get(f"/api/v1/artifacts/{variant['deck_artifact_id']}/download")
    assert deck.status_code == 200 and deck.content[:2] == b"PK"

    # pipeline export (pptx + pdf + passport) is already attached to the
    # variant — with sha256/size, so the export screen needs no extra call
    export = client.get(f"/api/v1/exports/{variant['export_ids'][0]}").json()
    assert {a["format"] for a in export["artifacts"]} == {"pptx", "pdf", "quality_passport"}
    assert all(a["sha256"] and a["size_bytes"] for a in export["artifacts"])
    for artifact in export["artifacts"]:
        dl = client.get(f"/api/v1/artifacts/{artifact['artifact_id']}/download")
        assert dl.status_code == 200 and dl.content
        if artifact["format"] == "pdf":
            assert dl.content[:5] == b"%PDF-"


def test_real_audit_issues_and_export_endpoint(wait_generation):
    project_id, template_id, pack_id = _project_with_inputs()
    accepted = _generate(project_id, template_id, pack_id)
    wait_generation(client, accepted["generation_id"])
    variant_id = accepted["variant_ids"][0]

    # deterministic audit already ran inside the pipeline — POST audits serves it
    audit = client.post(f"/api/v1/variants/{variant_id}/audits", json={})
    assert audit.status_code == 202, audit.text
    audit_id = audit.json()["audit_id"]
    run = client.get(f"/api/v1/audits/{audit_id}").json()
    assert run["deterministic_status"] == "completed"
    assert run["issue_count"] > 0
    issues = client.get(f"/api/v1/audits/{audit_id}/issues").json()["items"]
    assert issues and issues[0]["rule_code"]

    # POST exports returns the real pipeline artifacts
    export = client.post(
        f"/api/v1/variants/{variant_id}/exports",
        json={"formats": ["pptx", "pdf", "quality_passport"]},
    )
    assert export.status_code == 202, export.text

    # html is built lazily from the variant's PDF: a zip with index.html
    html = client.post(f"/api/v1/variants/{variant_id}/exports", json={"formats": ["html"]})
    assert html.status_code == 202, html.text
    record = client.get(f"/api/v1/exports/{html.json()['export_id']}").json()
    artifact = record["artifacts"][0]
    assert artifact["format"] == "html" and artifact["mime_type"] == "application/zip"
    import io
    import zipfile

    body = client.get(artifact["download_url"]).content
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        assert "index.html" in zf.namelist()
        assert any(n.endswith(".png") for n in zf.namelist())

    # a format outside the API's set is still an honest 422, not a fake export
    bad = client.post(f"/api/v1/variants/{variant_id}/exports", json={"formats": ["docx"]})
    assert bad.status_code == 422


def test_variant_slides_match_plan(wait_generation):
    project_id, template_id, pack_id = _project_with_inputs()
    accepted = _generate(project_id, template_id, pack_id)
    wait_generation(client, accepted["generation_id"])
    variant_id = accepted["variant_ids"][0]
    slides = client.get(f"/api/v1/variants/{variant_id}/slides").json()["items"]
    assert len(slides) == 10
    assert all(s["slide_plan_id"] and s["purpose"] for s in slides)


def test_generation_requires_real_content_source():
    project = client.post("/api/v1/projects", json={"name": "no content"}).json()
    tpl = client.post(
        f"/api/v1/projects/{project['id']}/templates",
        files={"file": (TEMPLATE.name, TEMPLATE.read_bytes(), PPTX_MIME)},
    ).json()
    response = client.post(
        f"/api/v1/projects/{project['id']}/generations",
        json={
            "template_id": tpl["id"],
            "content_pack_id": "pack_missing",
            "brief": BRIEF,
            "variants": [{"strategy": "balanced"}],
        },
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
