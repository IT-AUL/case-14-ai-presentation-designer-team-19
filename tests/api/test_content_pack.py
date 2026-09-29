"""Content-pack contract test: POST /projects/{id}/content-packs must parse
the uploaded file via ingestion.content_parsers.parse_file and return its
real sections — never the old "Stub content pack" payload."""

import json
from pathlib import Path

import pytest
from deckdna.api.app import STORE, app
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


def _project() -> str:
    return client.post("/api/v1/projects", json={"name": "pack test"}).json()["id"]


def _upload(project_id: str, files: list[tuple[str, bytes, str]]) -> dict:
    response = client.post(
        f"/api/v1/projects/{project_id}/content-packs",
        files=[("files", f) for f in files],
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_content_pack_returns_real_parsed_sections():
    project_id = _project()
    accepted = _upload(
        project_id, [(CONTENT.name, CONTENT.read_bytes(), "text/markdown")]
    )

    pack = accepted["content_pack"]
    assert pack["id"]
    assert pack["language"] == "ru"  # cyrillic fixture -> detected
    assert pack["sections"], "parsed markdown must produce sections"

    # same pack through GET — and it is the real parse, not the stub
    fetched = client.get(f"/api/v1/content-packs/{pack['id']}").json()
    headings = [s["heading"] for s in fetched["sections"]]
    assert "Stub content pack" not in str(fetched)
    assert "Проблема" in headings and "Решение" in headings

    # blocks carry real text and point back at the uploaded artifact
    first = fetched["sections"][0]["blocks"][0]
    assert first["source_ref"]["artifact_id"].startswith("art_")


def test_identical_uploads_keep_independent_pack_and_source_ids():
    project_a, project_b = _project(), _project()
    source = CONTENT.read_bytes()
    pack_a = _upload(project_a, [(CONTENT.name, source, "text/markdown")])["content_pack"]
    pack_b = _upload(project_b, [(CONTENT.name, source, "text/markdown")])["content_pack"]
    assert pack_a["id"] != pack_b["id"]
    assert pack_a["id"].startswith("pack_")
    assert pack_b["id"].startswith("pack_")
    artifact_a = pack_a["sections"][0]["blocks"][0]["source_ref"]["artifact_id"]
    artifact_b = pack_b["sections"][0]["blocks"][0]["source_ref"]["artifact_id"]
    assert artifact_a != artifact_b
    assert STORE.artifacts[artifact_a].project_id == project_a
    assert STORE.artifacts[artifact_b].project_id == project_b
    assert STORE.artifacts[artifact_a].sha256 == STORE.artifacts[artifact_b].sha256
    assert STORE.artifacts[artifact_a].data == STORE.artifacts[artifact_b].data == source
    assert STORE.pack_artifacts[pack_a["id"]] == [artifact_a]
    assert STORE.pack_artifacts[pack_b["id"]] == [artifact_b]
    graph_a = client.get(f"/api/v1/content-packs/{pack_a['id']}/evidence-graph").json()
    graph_b = client.get(f"/api/v1/content-packs/{pack_b['id']}/evidence-graph").json()
    assert graph_a["id"] != graph_b["id"]


def test_json_supplied_pack_id_is_not_api_identity():
    payload = {
        "schema_version": "1.0",
        "id": "user-controlled-id",
        "language": "ru",
        "sections": [],
        "tables": [],
        "assets": [],
        "warnings": [],
    }
    source = json.dumps(payload).encode()
    pack_a = _upload(_project(), [("content.json", source, "application/json")])["content_pack"]
    pack_b = _upload(_project(), [("content.json", source, "application/json")])["content_pack"]
    assert pack_a["id"] != payload["id"]
    assert pack_b["id"] != payload["id"]
    assert pack_a["id"] != pack_b["id"]


@pytest.mark.parametrize(
    "extra_name,extra_mime",
    [
        ("data.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("image.png", "image/png"),
    ],
)
def test_content_pack_rejects_multiple_files_without_storing_anything(extra_name, extra_mime):
    project_id = _project()
    before_artifacts = set(STORE.artifacts)
    before_packs = set(STORE.content_packs)
    before_jobs = set(STORE.jobs)
    response = client.post(
        f"/api/v1/projects/{project_id}/content-packs",
        files=[
            ("files", (CONTENT.name, CONTENT.read_bytes(), "text/markdown")),
            ("files", (extra_name, b"content", extra_mime)),
        ],
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_input"
    assert "exactly one file" in response.json()["error"]["message"]
    assert set(STORE.artifacts) == before_artifacts
    assert set(STORE.content_packs) == before_packs
    assert set(STORE.jobs) == before_jobs
    assert STORE.projects[project_id].content_pack_id is None


def test_content_pack_rejects_unsupported_files():
    project_id = _project()
    response = client.post(
        f"/api/v1/projects/{project_id}/content-packs",
        files=[("files", ("image.png", b"\x89PNG\r\n\x1a\nfake", "image/png"))],
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"


@pytest.mark.parametrize("name", ["absolute", "traversal", "windows"])
def test_content_upload_rejects_filename_paths(name, tmp_path):
    project_id = _project()
    outside = tmp_path / "outside.md"
    filename = {
        "absolute": str(outside),
        "traversal": "../outside.md",
        "windows": r"..\outside.md",
    }[name]
    response = client.post(
        f"/api/v1/projects/{project_id}/content-packs",
        files=[("files", (filename, b"# controlled probe", "text/markdown"))],
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_input"
    assert not outside.exists()


def test_parsed_pack_still_feeds_generation(wait_generation):
    """generate() re-parses source_ids[0] — it must be the same file the
    pack was built from, or upload and generation would disagree."""
    project_id = _project()
    tpl = client.post(
        f"/api/v1/projects/{project_id}/templates",
        files={"file": (TEMPLATE.name, TEMPLATE.read_bytes(), PPTX_MIME)},
    )
    assert tpl.status_code == 201, tpl.text
    pack = _upload(
        project_id, [(CONTENT.name, CONTENT.read_bytes(), "text/markdown")]
    )
    pack_id = pack["content_pack"]["id"]

    gen = client.post(
        f"/api/v1/projects/{project_id}/generations",
        json={
            "template_id": tpl.json()["id"],
            "content_pack_id": pack_id,
            "brief": BRIEF,
            "variants": [{"strategy": "balanced"}],
        },
    )
    assert gen.status_code == 202, gen.text
    run = wait_generation(client, gen.json()["generation_id"])
    assert run["state"] == "completed"
    assert run["content_pack_id"] == pack_id
