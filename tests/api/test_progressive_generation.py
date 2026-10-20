"""ADR-036: real per-slide renders through the production API/compiler."""

from __future__ import annotations

import json
import time
from pathlib import Path

from deckdna.api import app as api
from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
A = "/api/v1"
client = TestClient(api.app)


def test_drafts_arrive_between_compiler_slides_and_final_render_replaces_them(wait_generation):
    pid = client.post(f"{A}/projects", json={"name": "progressive"}).json()["id"]
    template = ROOT / "tests/fixtures/pptx/vk_tech_template.pptx"
    template_id = client.post(
        f"{A}/projects/{pid}/templates",
        files={"file": (template.name, template.read_bytes(), api._PPTX_MIME)},
    ).json()["id"]
    content = (ROOT / "tests/fixtures/content/poc_article.md").read_bytes()
    pack = client.post(
        f"{A}/projects/{pid}/content-packs",
        files=[("files", ("article.md", content, "text/markdown"))],
        data={"brief": json.dumps({"language": "ru"})},
    ).json()["content_pack"]["id"]
    analysis_needed = template_id not in api.STORE.design_dna
    response = client.post(f"{A}/projects/{pid}/generations", json={
        "template_id": template_id, "content_pack_id": pack,
        "brief": {"purpose": "Обзор", "audience": "команда", "language": "ru",
                  "target_slide_count": 5},
        "variants": [{"strategy": "balanced"}], "use_llm": False,
        "progressive_previews": True,
    })
    assert response.status_code == 202, response.text
    accepted = response.json()
    variant_id, job_id = accepted["variant_ids"][0], accepted["job_id"]
    deadline = time.monotonic() + 120
    first = None
    while time.monotonic() < deadline:
        cards = client.get(f"{A}/variants/{variant_id}/slide-drafts").json()
        first = next((s for s in cards["slides"] if s.get("preview_artifact_id")), None)
        if first:
            break
        time.sleep(0.05)
    assert first and first["status"] == "draft"
    assert api.STORE.jobs[job_id].state == api.JobState.running
    from io import BytesIO

    png = client.get(f"{A}/artifacts/{first['preview_artifact_id']}/download")
    with Image.open(BytesIO(png.content)) as image:
        assert image.width > 100 and image.height > 100
    run = wait_generation(client, accepted["generation_id"])
    assert run["state"] == "completed", run
    events = api.STORE.job_events[job_id]
    analysis = [e["event"] for e in events if e.get("stage") == "template_analysis"]
    assert analysis == (["stage.started", "stage.completed"] if analysis_needed else [])
    compiled = [e for e in events if e["event"] == "slide.compiled"]
    drafts = [e for e in events if e["event"] == "slide.ready" and e["status"] == "draft"]
    final = [e for e in events if e["event"] == "slide.ready" and e["status"] == "rendered"]
    assert len(compiled) == len(drafts) == len(final) >= 2
    for previous, following in zip(drafts, compiled[1:], strict=False):
        assert previous["sequence"] < following["sequence"]
    assert max(e["sequence"] for e in drafts) < min(e["sequence"] for e in final)
    for early, late in zip(drafts, final, strict=True):
        assert early["slide_key"] == late["slide_key"]
        assert early["slide_id"] == late["slide_id"]
        assert early["revision"] == late["revision"] == 1
        assert early["preview_revision"] == 1 and late["preview_revision"] == 2
    before = len(events)
    cards = client.get(f"{A}/variants/{variant_id}/slide-drafts").json()
    assert all(s["status"] == "rendered" for s in cards["slides"])
    assert len(api.STORE.job_events[job_id]) == before
    slides = client.get(f"{A}/variants/{variant_id}/slides").json()
    assert [s["preview_artifact_id"] for s in slides["items"]] == [
        e["preview_artifact_id"] for e in final
    ]
