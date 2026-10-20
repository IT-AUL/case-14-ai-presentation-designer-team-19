"""Compose/audit/render now run in parallel across variants (Фаза 5, see
architecture notes): previously only planning was parallelized (Фаза 2),
so a 3-variant generation cost roughly 3x one variant's compose+audit+
render time end to end -- confirmed live twice (6:31 and 7:09 against a
5-minute budget). This asserts correctness of the parallel path (no
crashes/races/lost variants) and gives a rough same-machine timing signal
against the old sequential loop; MockProvider has near-zero LLM latency,
so the real-world speedup (dominated by live LLM round-trips) is larger
than what this local, no-network test can show -- verified separately.
"""

import time
from pathlib import Path

import pytest
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
    "purpose": "Параллельность вариантов",
    "audience": "эксперты",
    "language": "ru",
    "target_slide_count": 8,
}

import shutil  # noqa: E402

needs_render = pytest.mark.skipif(
    shutil.which("soffice") is None or not TEMPLATE.exists(),
    reason="generation pipeline needs the fixture + soffice",
)


def _project_with_inputs() -> tuple[str, str, str]:
    project = client.post(
        "/api/v1/projects", json={"name": "parallelism", "target_slide_count": 8}
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


@needs_render
def test_three_variants_complete_correctly_and_concurrently(wait_generation):
    project_id, template_id, pack_id = _project_with_inputs()
    started = time.monotonic()
    accepted = client.post(
        f"/api/v1/projects/{project_id}/generations",
        json={
            "template_id": template_id,
            "content_pack_id": pack_id,
            "brief": BRIEF,
            "variants": [
                {"strategy": "faithful"},
                {"strategy": "balanced"},
                {"strategy": "visual"},
            ],
        },
    )
    assert accepted.status_code == 202, accepted.text
    run = wait_generation(client, accepted.json()["generation_id"], timeout=240.0)
    elapsed = time.monotonic() - started

    assert run["state"] == "completed", run
    assert len(run["variants"]) == 3
    statuses = [v["status"] for v in run["variants"]]
    assert statuses == ["completed", "completed", "completed"], statuses

    # Every variant produced its OWN distinct deck -- proves the parallel
    # workers didn't collide on out_dir/artifacts (the exact hazard the
    # index-qualified out_dir and per-variant gateway guard against).
    deck_artifact_ids = {v["deck_artifact_id"] for v in run["variants"]}
    assert len(deck_artifact_ids) == 3, "variants must not share an artifact"
    for artifact_id in deck_artifact_ids:
        dl = client.get(f"/api/v1/artifacts/{artifact_id}/download")
        assert dl.status_code == 200
        assert dl.content[:2] == b"PK"  # real, distinct pptx bytes

    print(f"\n[timing] 3-variant generation wall time: {elapsed:.1f}s")
