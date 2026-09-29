"""DELETE /projects/{id} lifecycle coverage.

Deleting a project used to remove only the project record -- templates,
content packs, generation runs/variants, audits, issues, plans, exports,
provider sessions and their artifact bytes stayed in STORE forever,
reachable through the global (non-project-scoped) endpoints by their old
IDs. These tests pin the cascading-delete contract: old IDs 404 after
deletion, a still-active job is canceled rather than left to keep writing,
and identical uploads in two projects have independent pack identities.
"""

import shutil
import threading
from pathlib import Path

import pytest
from deckdna.api import app as app_module
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

needs_render = pytest.mark.skipif(
    shutil.which("soffice") is None or not TEMPLATE.exists(),
    reason="generation pipeline needs the fixture + soffice",
)


def _new_project(name: str) -> str:
    return client.post(
        "/api/v1/projects", json={"name": name, "target_slide_count": 10}
    ).json()["id"]


def _upload_template(project_id: str) -> str:
    resp = client.post(
        f"/api/v1/projects/{project_id}/templates",
        files={"file": (TEMPLATE.name, TEMPLATE.read_bytes(), PPTX_MIME)},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _upload_content(project_id: str, content: bytes, filename: str = "content.md") -> str:
    resp = client.post(
        f"/api/v1/projects/{project_id}/content-packs",
        files={"files": (filename, content, "text/markdown")},
    )
    assert resp.status_code == 202, resp.text
    return resp.json()["content_pack"]["id"]


def test_delete_project_revokes_template_and_content_pack_ids():
    if not TEMPLATE.exists():
        pytest.skip("organizer fixture not present")
    project_id = _new_project("to delete")
    template_id = _upload_template(project_id)
    unique_content = (
        f"# Unique {project_id}\n\nContent only this project uploads.\n"
    ).encode()
    pack_id = _upload_content(project_id, unique_content)
    template_artifact_id = STORE.templates[template_id].artifact_id

    resp = client.delete(f"/api/v1/projects/{project_id}")
    assert resp.status_code == 204, resp.text

    assert client.get(f"/api/v1/projects/{project_id}").status_code == 404
    assert client.get(f"/api/v1/templates/{template_id}").status_code == 404
    assert client.get(f"/api/v1/content-packs/{pack_id}").status_code == 404
    assert (
        client.get(f"/api/v1/artifacts/{template_artifact_id}/download").status_code
        == 404
    )

    # Re-deleting an already-gone project is a clean 404, not a crash.
    assert client.delete(f"/api/v1/projects/{project_id}").status_code == 404


def test_delete_project_keeps_other_projects_identical_upload():
    """Deleting one project never removes another's identical upload."""
    if not TEMPLATE.exists():
        pytest.skip("organizer fixture not present")
    shared_bytes = b"# Shared\n\nSame content, two projects.\n"
    project_a = _new_project("owner a")
    project_b = _new_project("owner b")
    pack_a = _upload_content(project_a, shared_bytes)
    pack_b = _upload_content(project_b, shared_bytes)
    assert pack_a != pack_b

    assert client.delete(f"/api/v1/projects/{project_a}").status_code == 204

    assert client.get(f"/api/v1/content-packs/{pack_a}").status_code == 404
    assert client.get(f"/api/v1/content-packs/{pack_b}").status_code == 200
    assert pack_a not in STORE.content_pack_projects
    assert STORE.content_pack_projects[pack_b] == {project_b}

    assert client.delete(f"/api/v1/projects/{project_b}").status_code == 204
    assert client.get(f"/api/v1/content-packs/{pack_b}").status_code == 404
    assert pack_b not in STORE.content_pack_projects


@needs_render
def test_delete_project_cancels_active_generation_job(monkeypatch):
    project_id = _new_project("delete mid-run")
    template_id = _upload_template(project_id)
    pack_id = _upload_content(project_id, CONTENT.read_bytes())

    started = threading.Event()
    release = threading.Event()
    real_generate = app_module.pipeline.generate

    def gated(*args, **kwargs):
        started.set()
        assert release.wait(timeout=120), "test never released the gate"
        return real_generate(*args, **kwargs)

    monkeypatch.setattr(app_module.pipeline, "generate", gated)

    accepted = client.post(
        f"/api/v1/projects/{project_id}/generations",
        json={
            "template_id": template_id,
            "content_pack_id": pack_id,
            "brief": BRIEF,
            "variants": [{"strategy": "balanced"}, {"strategy": "visual"}],
        },
    )
    assert accepted.status_code == 202, accepted.text
    run_id = accepted.json()["generation_id"]
    job_id = accepted.json()["job_id"]
    assert started.wait(timeout=60), "worker never picked up the job"

    assert client.delete(f"/api/v1/projects/{project_id}").status_code == 204
    assert client.get(f"/api/v1/generations/{run_id}").status_code == 404
    assert STORE.jobs[job_id].state == "canceled"

    # Unblock the in-flight variant so the gated thread can exit cleanly
    # rather than leaking into later tests.
    release.set()
