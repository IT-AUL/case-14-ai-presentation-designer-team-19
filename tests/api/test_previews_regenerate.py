"""ADR-034 §5–7 через API: пробные слайды стиля, сборка по preview_id,
события генерации, пересборка одного слайда и панель «Почему так»."""

from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path

from deckdna.api.app import STORE, app
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "tests" / "fixtures" / "pptx" / "vk_tech_template.pptx"
CSV = ROOT / "examples" / "stage2" / "purchases.csv"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
A = "/api/v1"
client = TestClient(app)


def _events(job_id: str) -> list[dict]:
    text = client.get(f"{A}/jobs/{job_id}/events").text
    return [json.loads(line.split(":", 1)[1])
            for line in text.splitlines() if line.startswith("data:")]


def _wait_job(job_id: str, timeout: float = 240.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"{A}/jobs/{job_id}").json()
        if job["state"] in {"completed", "failed", "canceled"}:
            return job
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not finish")


def _project_with_plan() -> tuple[str, str, dict]:
    pid = client.post(f"{A}/projects", json={"name": "previews"}).json()["id"]
    tpl = client.post(f"{A}/projects/{pid}/templates",
                      files={"file": (TEMPLATE.name, TEMPLATE.read_bytes(), PPTX_MIME)})
    assert tpl.status_code == 201, tpl.text
    up = client.post(f"{A}/projects/{pid}/content-packs",
                     files=[("files", ("purchases.csv", CSV.read_bytes(), "text/csv"))],
                     data={"brief": json.dumps({"language": "ru"})})
    assert up.status_code == 202, up.text
    plans = client.post(f"{A}/projects/{pid}/plans", json={
        "content_pack_id": up.json()["content_pack"]["id"], "strategies": ["balanced"],
        "use_llm": False, "template_id": tpl.json()["id"],
        "brief": {"purpose": "Итоги продаж", "audience": "руководство", "language": "ru",
                  "target_slide_count": 7},
    })
    assert plans.status_code == 200, plans.text
    return pid, tpl.json()["id"], plans.json()


def _gen_base(plan_set: dict) -> dict:
    return {"content_pack_id": plan_set["content_pack_id"],
            "brief": plan_set["plans"][0]["deck_plan"]["brief"]}


def _slides_of(pptx: bytes) -> dict[str, bytes]:
    from deckdna.pptx.composing.slide_swap import slide_parts

    with zipfile.ZipFile(io.BytesIO(pptx)) as z:
        files = {n: z.read(n) for n in z.namelist()}
    return {p: files[p] for p in slide_parts(files)}


def test_style_previews_then_generation_by_preview_id_reuses_exemplars(
    wait_generation, monkeypatch,
):
    from deckdna.pptx.composing import minimal

    compiled = []
    original = minimal.generate_deck

    def observe(template, plan, *args, **kwargs):
        compiled.append([s.id for s in plan.slides])
        return original(template, plan, *args, **kwargs)

    monkeypatch.setattr(minimal, "generate_deck", observe)
    pid, tpl_id, plan_set = _project_with_plan()
    started = time.monotonic()
    accepted = client.post(f"{A}/plans/{plan_set['plan_id']}/style-previews",
                           json={"template_id": tpl_id})
    assert accepted.status_code == 202, accepted.text
    body = accepted.json()
    assert _wait_job(body["job_id"])["state"] == "completed"
    preview_seconds = time.monotonic() - started
    record = client.get(f"{A}/style-previews/{body['preview_id']}").json()
    assert [s["strategy"] for s in record["styles"]] == ["faithful", "balanced", "visual"]
    assert sum(s["recommended"] for s in record["styles"]) == 1 and record["assistant_note"]
    for style in record["styles"]:
        assert style["error"] is None and style["title"] and style["difference"]
        assert 2 <= len(style["previews"]) <= 3
        assert all(p["image_artifact_id"] for p in style["previews"])
    ready = [e for e in _events(body["job_id"]) if e["event"] == "preview.ready"]
    assert len(ready) == sum(len(s["previews"]) for s in record["styles"])
    # Count actual compiler inputs: previews must not build full decks and
    # merely crop their images. Renderer timings alone cannot prove this.
    assert len(compiled) == 3
    assert all(set(ids) == set(record["slide_ids"]) and len(ids) == len(record["slide_ids"])
               for ids in compiled)
    assert all(len(ids) < len(plan_set["plans"][0]["deck_plan"]["slides"]) for ids in compiled)

    started = time.monotonic()
    gen = client.post(f"{A}/projects/{pid}/generations", json={
        "template_id": tpl_id, "deck_plan_id": plan_set["plan_id"], **_gen_base(plan_set),
        "preview_id": body["preview_id"], "variants": [{"strategy": "balanced"}],
        "use_llm": False,
    })
    assert gen.status_code == 202, gen.text
    run = wait_generation(client, gen.json()["generation_id"])
    full_seconds = time.monotonic() - started
    assert run["state"] == "completed", run
    # Three preview renders and a warm full render are different workloads.
    # Keep real latency evidence; a 100ms scheduler difference is not a regression.
    print(f"preview_seconds={preview_seconds:.3f} full_seconds={full_seconds:.3f}")

    variant_id = gen.json()["variant_ids"][0]
    report = STORE.runs[gen.json()["generation_id"]].variant_reports[variant_id]
    used = {c["id"]: (c.get("exemplar") or {}).get("slide_part")
            for c in report["compose_report"]["slides"]}
    balanced = next(s for s in record["styles"] if s["strategy"] == "balanced")
    for slide_id, part in balanced["exemplars"].items():
        assert used[slide_id] == part

    events = _events(run["job_id"])
    kinds = [e["event"] for e in events]
    assert "slide.ready" in kinds and "variant.completed" in kinds
    assert kinds[-1] in {"job.completed", "job.state_changed"}
    assert any(e["event"] == "stage.started" and e.get("assistant_note") for e in events)


def test_regenerate_one_slide_keeps_the_others_byte_identical(wait_generation):
    pid, tpl_id, plan_set = _project_with_plan()
    gen = client.post(f"{A}/projects/{pid}/generations", json={
        "template_id": tpl_id, "deck_plan_id": plan_set["plan_id"], **_gen_base(plan_set),
        "variants": [{"strategy": "balanced"}], "use_llm": False,
    })
    assert gen.status_code == 202, gen.text
    assert wait_generation(client, gen.json()["generation_id"])["state"] == "completed"
    variant_id = gen.json()["variant_ids"][0]
    variant = client.get(f"{A}/variants/{variant_id}").json()
    before = STORE.artifacts[variant["deck_artifact_id"]].data
    slides = client.get(f"{A}/variants/{variant_id}/slides").json()
    slides = slides.get("items", slides) if isinstance(slides, dict) else slides
    plan = STORE.runs[gen.json()["generation_id"]].variant_plans[variant_id]
    chart_ids = {s.id for s in plan.slides if (s.chart or {}).get("alternatives")}
    target = next(s for s in slides if s["slide_plan_id"] in chart_ids)

    resp = client.post(f"{A}/variants/{variant_id}/slides/{target['id']}/regenerate",
                       json={"action": "other_chart"})
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["deck_revision"] == 2 and out["assistant_note"]
    assert out["slide"]["revision"] == 2 and out["preview_artifact_id"]
    after = STORE.artifacts[out["deck_artifact_id"]].data
    old, new = _slides_of(before), _slides_of(after)
    assert list(old) == list(new)
    changed = [p for p in old if old[p] != new[p]]
    assert changed == [list(old)[target["index"] - 1]]
    new_plan = STORE.runs[gen.json()["generation_id"]].variant_plans[variant_id]
    card = next(s for s in new_plan.slides if s.id == target["slide_plan_id"]).chart
    old_card = next(s for s in plan.slides if s.id == target["slide_plan_id"]).chart
    assert card["type"] != old_card["type"]

    layout = client.post(f"{A}/variants/{variant_id}/slides/{target['id']}/regenerate",
                         json={"action": "other_layout"})
    assert layout.status_code == 200, layout.text
    assert layout.json()["deck_revision"] == 3
    assert layout.json()["exemplar"] != out["exemplar"]

    bad = client.post(f"{A}/variants/{variant_id}/slides/{target['id']}/regenerate",
                      json={"action": "explode"})
    assert bad.status_code == 422

    explain = client.get(f"{A}/slides/{target['id']}/explain")
    assert explain.status_code == 200, explain.text
    exp = explain.json()
    assert exp["dataset"]["file"] == "purchases.csv" and exp["profile_summary"]
    cover = client.get(f"{A}/slides/{slides[0]['id']}/explain").json()
    assert cover["dataset"] is None
