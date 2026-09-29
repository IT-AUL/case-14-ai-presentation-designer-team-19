"""Контракт API под фронтенд (docs/contracts/API.md): B1–B6, D2–D11,
C1, Q2, Q4 — на реальном шаблоне и реальном конвейере (без моков)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from deckdna.api.app import app
from deckdna.audit import catalog
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TEMPLATE = FIXTURES / "pptx" / "vk_tech_template.pptx"
CONTENT = FIXTURES / "content" / "deckdna_pitch_rich.md"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
A = "/api/v1"

client = TestClient(app)

BRIEF = {
    "purpose": "product",
    "audience": "руководство",
    "language": "ru",
    "target_slide_count": 12,
}


def _project_inputs() -> tuple[str, str, str]:
    pid = client.post(f"{A}/projects", json={"name": "handoff"}).json()["id"]
    tpl = client.post(
        f"{A}/projects/{pid}/templates",
        files={"file": (TEMPLATE.name, TEMPLATE.read_bytes(), PPTX_MIME)},
    )
    assert tpl.status_code == 201, tpl.text
    pack = client.post(
        f"{A}/projects/{pid}/content-packs",
        files=[("files", (CONTENT.name, CONTENT.read_bytes(), "text/markdown"))],
        data={"brief": json.dumps({"language": "ru"})},
    )
    assert pack.status_code == 202, pack.text
    return pid, tpl.json()["id"], pack.json()["content_pack"]["id"]


def _generate(wait_generation, strategies=("balanced",), **extra) -> dict:
    pid, tid, cp = _project_inputs()
    body = {
        "template_id": tid,
        "content_pack_id": cp,
        "brief": BRIEF,
        "variants": [{"strategy": s} for s in strategies],
        **extra,
    }
    gen = client.post(f"{A}/projects/{pid}/generations", json=body)
    assert gen.status_code == 202, gen.text
    run = wait_generation(client, gen.json()["generation_id"])
    assert run["state"] == "completed", run
    return {
        "pid": pid,
        "tid": tid,
        "cp": cp,
        "run": run,
        "variant_ids": gen.json()["variant_ids"],
    }


def _audit(variant_id: str) -> tuple[str, list[dict]]:
    audit_id = client.post(f"{A}/variants/{variant_id}/audits", json={}).json()["audit_id"]
    return audit_id, _issues(audit_id)


def _issues(audit_id: str, **params) -> list[dict]:
    r = client.get(f"{A}/audits/{audit_id}/issues", params={"limit": 500, **params})
    assert r.status_code == 200, r.text
    return r.json()["items"]


@pytest.fixture(scope="module")
def shared(request):
    """Один общий read-only прогон на модуль (3 варианта) — дорогой."""
    import time

    def wait(c, run_id, timeout=180.0):
        deadline = time.monotonic() + timeout
        while True:
            run = c.get(f"{A}/generations/{run_id}").json()
            if run.get("state") in {"completed", "failed", "canceled"}:
                return run
            if time.monotonic() > deadline:
                raise TimeoutError(run_id)
            time.sleep(0.2)

    return _generate(wait, strategies=("faithful", "balanced", "visual"))


# ---------------------------------------------------------------- B1 / B4 / B5


def test_dismiss_then_status_filter_returns_200(shared):
    audit_id, issues = _audit(shared["variant_ids"][0])
    target = issues[0]["id"]
    r = client.post(f"{A}/issues/{target}/dismiss", json={"reason": "Так задумано"})
    assert r.status_code == 200
    for status in ("open", "dismissed", "fixed", "unresolved"):
        resp = client.get(f"{A}/audits/{audit_id}/issues", params={"status": status})
        assert resp.status_code == 200, (status, resp.text)
    dismissed = _issues(audit_id, status="dismissed")
    assert [i["id"] for i in dismissed] == [target]
    assert target not in {i["id"] for i in _issues(audit_id, status="open")}
    bad = client.get(f"{A}/audits/{audit_id}/issues", params={"status": "nonsense"})
    assert bad.status_code == 422


def test_issue_revision_matches_audit_revision(shared):
    audit_id, issues = _audit(shared["variant_ids"][0])
    rev = client.get(f"{A}/audits/{audit_id}").json()["deck_revision"]
    assert rev == 1
    assert issues and {i["deck_revision"] for i in issues} == {rev}


def test_repairable_only_where_planner_can_fix(shared):
    _, issues = _audit(shared["variant_ids"][0])
    fixable = catalog.repairable_rules()
    for issue in issues:
        assert issue["repairable"] == (issue["rule_code"] in fixable), issue["rule_code"]
        assert (issue["fix_preview"] is not None) == issue["repairable"]


# ------------------------------------------------------------------- Q2 / Q4


def test_safe_rules_are_fixed_before_revision_one(shared):
    for vid in shared["variant_ids"]:
        _, issues = _audit(vid)
        rules = {i["rule_code"] for i in issues}
        assert not rules & {
            "template.color_palette",
            "image.aspect_ratio",
            "accessibility.contrast",
        }, rules


def test_auto_fixes_are_disclosed_in_the_passport(shared):
    vid = shared["variant_ids"][0]
    export = client.post(f"{A}/variants/{vid}/exports", json={"formats": ["quality_passport"]})
    record = client.get(f"{A}/exports/{export.json()['export_id']}").json()
    passport = client.get(record["artifacts"][0]["download_url"]).json()
    fixes = [f for f in passport["fallbacks"] or [] if f["strategy"] == "auto_fix"]
    assert fixes, "auto-fixes must be recorded in the passport"
    assert passport["issues_summary"]["fixed"] >= sum(
        1 for _ in fixes
    )


def test_clipped_bbox_is_inside_the_slide(shared):
    _, issues = _audit(shared["variant_ids"][0])
    boxes = [i["clipped_bbox"] for i in issues if i["clipped_bbox"]]
    assert boxes
    for b in boxes:
        assert 0 <= b["x"] <= 1 and 0 <= b["y"] <= 1
        assert b["x"] + b["w"] <= 1.0001 and b["y"] + b["h"] <= 1.0001


# ------------------------------------------------------------------- D3


def test_passport_carries_every_handoff_metric(shared):
    vid = shared["variant_ids"][0]
    export = client.post(f"{A}/variants/{vid}/exports", json={"formats": ["quality_passport"]})
    record = client.get(f"{A}/exports/{export.json()['export_id']}").json()
    m = client.get(record["artifacts"][0]["download_url"]).json()["metrics"]
    assert set(m["style_fidelity"]) == {
        "palette_compliance",
        "font_compliance",
        "layout_origin_compliance",
        "anchor_compliance",
    }
    assert all(0 <= v <= 1 for v in m["style_fidelity"].values())
    assert [s["stage"] for s in m["timings"]["per_stage"]] == [
        "content",
        "plan",
        "compose",
        "audit",
        "render",
    ]
    # детерминированный путь: usage — нули, а не null
    assert m["usage"]["total_tokens"] == 0 and m["usage"]["model_calls"] == 0
    assert m["content_support"]["numbers_verified"] is not None
    assert m["content_support"]["numbers_failed"] is not None
    assert 0 < m["readability"]["avg_occupancy"] <= 1


def test_variant_card_carries_style_fidelity(shared):
    for v in shared["run"]["variants"]:
        assert 0 <= v["metrics"]["style_fidelity"] <= 1
        assert v["metrics"]["auto_fixed"] >= 0


# ------------------------------------------------------------------- D4


def test_variant_lifecycle_fields(shared):
    for v in shared["run"]["variants"]:
        assert v["status"] == "completed"
        assert v["started_at"] and v["finished_at"] and v["stage"] is None
        assert v["started_at"] <= v["finished_at"]


# ------------------------------------------------------------------- D6


def test_png_previews_and_montage(shared):
    vid = shared["variant_ids"][0]
    variant = client.get(f"{A}/variants/{vid}").json()
    assert variant["montage_artifact_id"]
    montage = client.get(f"{A}/artifacts/{variant['montage_artifact_id']}/download")
    assert montage.content[:8] == b"\x89PNG\r\n\x1a\n"
    slides = client.get(f"{A}/variants/{vid}/slides").json()["items"]
    assert slides and all(s["preview_artifact_id"] for s in slides)
    preview = client.get(f"{A}/slides/{slides[0]['id']}/preview")
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"


# ------------------------------------------------------------------- D8 / D9


def test_rule_catalog_endpoint(shared):
    rules = client.get(f"{A}/audit/rules").json()
    assert {r["code"] for r in rules} == set(catalog.RULES)
    assert len(rules) == 34
    for r in rules:
        assert r["title_ru"] and r["category"] in catalog.CATEGORIES
        assert r["repairable"] == (r["fix_title_ru"] is not None)
    by_code = {r["code"]: r for r in rules}
    assert by_code["text.overflow"]["threshold"] == pytest.approx(0.05)
    assert by_code["content.source_support"]["deterministic"] is False


def test_capabilities_features_block():
    cap = client.get(f"{A}/capabilities").json()
    features = cap["features"]
    assert set(features) == {
        "html_export",
        "plan_only",
        "png_previews",
        "async_generation",
        "sse_progress",
        "contextual_audit",
        "pdf_after_repair",
        "repair_dry_run",
        "style_fidelity",
        "model_text_fit",
        "model_auto",
    }
    assert all(isinstance(v, bool) for v in features.values())
    assert features["html_export"] and "html" in cap["exporters"]
    assert cap["contextual_audit_mode"] in {"session", "server", "mock", "none"}


# ------------------------------------------------------------------- D10 / D11


def test_project_reports_latest_run_and_lists_generations(shared):
    project = client.get(f"{A}/projects/{shared['pid']}").json()
    assert project["latest_run_id"] == shared["run"]["id"]
    assert project["latest_run_state"] == "completed"
    listing = client.get(f"{A}/projects/{shared['pid']}/generations").json()["items"]
    assert [g["id"] for g in listing] == [shared["run"]["id"]]
    assert listing[0]["variant_ids"] == shared["variant_ids"]
    rows = client.get(f"{A}/projects").json()["items"]
    assert next(p for p in rows if p["id"] == shared["pid"])["latest_run_id"]


def test_variant_axes_and_rationale(shared):
    by_strategy = {v["strategy"]: v for v in shared["run"]["variants"]}
    for v in by_strategy.values():
        assert v["rationale"]
        assert set(v["axes"]) == {"text_density", "layout_diversity", "visualization"}
    assert (
        by_strategy["visual"]["axes"]["visualization"]
        > by_strategy["faithful"]["axes"]["visualization"]
    )
    assert (
        by_strategy["faithful"]["axes"]["layout_diversity"]
        < by_strategy["balanced"]["axes"]["layout_diversity"]
    )


# ------------------------------------------------------------------- C1


def test_issue_pagination_cursor_walks_every_issue(shared):
    audit_id, issues = _audit(shared["variant_ids"][0])
    seen: list[str] = []
    cursor = None
    while True:
        params = {"limit": 7, **({"cursor": cursor} if cursor else {})}
        page = client.get(f"{A}/audits/{audit_id}/issues", params=params).json()
        seen += [i["id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == [i["id"] for i in issues]
    bad = client.get(f"{A}/audits/{audit_id}/issues", params={"cursor": "###"})
    assert bad.status_code == 422


def test_download_supports_range_and_etag(shared):
    vid = shared["variant_ids"][0]
    export = client.post(f"{A}/variants/{vid}/exports", json={"formats": ["pdf"]})
    art = client.get(f"{A}/exports/{export.json()['export_id']}").json()["artifacts"][0]
    url = art["download_url"]
    full = client.get(url)
    assert full.headers["accept-ranges"] == "bytes"
    assert full.headers["etag"] == f'"{art["sha256"]}"'
    part = client.get(url, headers={"Range": "bytes=0-9"})
    assert part.status_code == 206
    assert part.content == full.content[:10] == b"%PDF-"[:5] + full.content[5:10]
    assert part.headers["content-range"] == f"bytes 0-9/{len(full.content)}"
    tail = client.get(url, headers={"Range": "bytes=-4"})
    assert tail.status_code == 206 and tail.content == full.content[-4:]
    assert client.get(url, headers={"If-None-Match": full.headers["etag"]}).status_code == 304
    unsat = client.get(url, headers={"Range": f"bytes={len(full.content) + 5}-"})
    assert unsat.status_code == 416


def test_export_record_from_generation_lists_pptx_with_checksum(shared):
    vid = shared["variant_ids"][0]
    variant = client.get(f"{A}/variants/{vid}").json()
    record = client.get(f"{A}/exports/{variant['export_ids'][0]}").json()
    pptx = next(a for a in record["artifacts"] if a["format"] == "pptx")
    assert pptx["artifact_id"] == variant["deck_artifact_id"]
    assert len(pptx["sha256"]) == 64 and pptx["size_bytes"] > 0


# ---------------------------------------------------- D2 / B2 / B6 (mutating)


def test_repair_reports_typed_outcomes_that_match_reality(wait_generation):
    g = _generate(wait_generation)
    vid = g["variant_ids"][0]
    audit_id, issues = _audit(vid)
    overflow = [i for i in issues if i["rule_code"] == "text.overflow"][:5]
    assert overflow

    dry = client.post(
        f"{A}/audits/{audit_id}/repairs",
        params={"dry_run": "true"},
        json={"selected_issue_ids": [i["id"] for i in overflow]},
    )
    assert dry.status_code == 200
    assert {o["status"] for o in dry.json()["outcomes"]} <= {"planned", "skipped"}
    assert client.get(f"{A}/audits/{audit_id}").json()["deck_revision"] == 1
    assert len(_issues(audit_id)) == len(issues), "dry_run must not change anything"

    repair = client.post(
        f"{A}/audits/{audit_id}/repairs",
        json={"selected_issue_ids": [i["id"] for i in overflow]},
    )
    assert repair.status_code == 202
    assert repair.json()["deck_revision"] == 2
    job = client.get(f"{A}/jobs/{repair.json()['job_id']}").json()
    result = job["result"]
    assert all(isinstance(result[k], int) for k in ("applied", "skipped", "failed", "unresolved"))
    assert len(result["outcomes"]) == len(overflow)
    assert (
        result["applied"] + result["failed"] + result["skipped"] + result["unresolved"]
        == len(overflow)
    )
    # B6: «применено» совпадает с фактом — столько проблем реально исчезло
    after = _issues(audit_id)
    fixed_fps = {o["fingerprint"] for o in result["outcomes"] if o["status"] == "fixed"}
    still = {i["fingerprint"] for i in after}
    assert not fixed_fps & still
    assert result["applied"] == len(fixed_fps)
    assert result["applied"] >= 1
    before_n = sum(1 for i in issues if i["rule_code"] == "text.overflow")
    after_n = sum(1 for i in after if i["rule_code"] == "text.overflow")
    assert before_n - after_n >= result["applied"]
    assert {i["deck_revision"] for i in after} == {2}


def test_dismissal_survives_repair_by_fingerprint(wait_generation):
    g = _generate(wait_generation)
    audit_id, issues = _audit(g["variant_ids"][0])
    keep = next(i for i in issues if not i["repairable"])
    client.post(f"{A}/issues/{keep['id']}/dismiss", json={"reason": "ok"})
    repairable = [i for i in issues if i["repairable"]][:2]
    client.post(
        f"{A}/audits/{audit_id}/repairs",
        json={"selected_issue_ids": [i["id"] for i in repairable]},
    )
    dismissed = _issues(audit_id, status="dismissed")
    assert keep["fingerprint"] in {i["fingerprint"] for i in dismissed}


def test_pdf_passport_and_html_exist_for_the_repaired_revision(wait_generation):
    g = _generate(wait_generation)
    vid = g["variant_ids"][0]
    audit_id, issues = _audit(vid)
    picked = [i for i in issues if i["repairable"]][:3]
    client.post(
        f"{A}/audits/{audit_id}/repairs",
        json={"selected_issue_ids": [i["id"] for i in picked]},
    )
    variant = client.get(f"{A}/variants/{vid}").json()
    assert variant["montage_artifact_id"] is None, "old previews are dropped on repair"

    export = client.post(
        f"{A}/variants/{vid}/exports",
        json={"formats": ["pptx", "pdf", "quality_passport", "html"]},
    )
    assert export.status_code == 202, export.text
    record = client.get(f"{A}/exports/{export.json()['export_id']}").json()
    assert record["deck_revision"] == 2
    formats = {a["format"]: a for a in record["artifacts"]}
    assert set(formats) == {"pptx", "pdf", "quality_passport", "html"}
    assert formats["pptx"]["artifact_id"] == variant["deck_artifact_id"]
    for art in formats.values():
        assert len(art["sha256"]) == 64 and art["size_bytes"] > 0
    pdf = client.get(formats["pdf"]["download_url"]).content
    assert pdf[:5] == b"%PDF-"
    passport = client.get(formats["quality_passport"]["download_url"]).json()
    assert any(f["strategy"] == "user_repair" for f in passport["fallbacks"] or []) or (
        passport["issues_summary"]["fixed"]
    )
    with zipfile.ZipFile(io.BytesIO(client.get(formats["html"]["download_url"]).content)) as zf:
        assert "index.html" in zf.namelist()
    # the rebuilt passport describes the CURRENT deck's issues
    audit_now = client.get(f"{A}/audits/{audit_id}").json()
    assert passport["issues_summary"]["unresolved"] == audit_now["issue_count"]
    # previews come back lazily for the new revision
    slides = client.get(f"{A}/variants/{vid}/slides").json()["items"]
    resp = client.get(f"{A}/slides/{slides[0]['id']}/preview")
    assert resp.status_code == 200
    assert client.get(f"{A}/slides/{slides[0]['id']}").json()["revision"] == 2


# ------------------------------------------------------------------- D7


def test_plan_only_then_generate_from_the_plan(wait_generation):
    pid, tid, cp = _project_inputs()
    plans = client.post(
        f"{A}/projects/{pid}/plans",
        json={"content_pack_id": cp, "brief": BRIEF, "strategies": ["balanced", "visual"]},
    )
    assert plans.status_code == 200, plans.text
    plan_set = plans.json()
    assert [p["strategy"] for p in plan_set["plans"]] == ["balanced", "visual"]
    balanced = next(p for p in plan_set["plans"] if p["strategy"] == "balanced")["deck_plan"]
    assert balanced["slides"]

    gen = client.post(
        f"{A}/projects/{pid}/generations",
        json={
            "template_id": tid,
            "content_pack_id": cp,
            "brief": BRIEF,
            "variants": [{"strategy": "balanced"}],
            "deck_plan_id": plan_set["plan_id"],
        },
    )
    assert gen.status_code == 202, gen.text
    run = wait_generation(client, gen.json()["generation_id"])
    assert run["state"] == "completed"
    assert run["deck_plan_id"] == balanced["id"], "built from the supplied plan, not re-planned"
    assert run["variants"][0]["planner"] == balanced["provenance"]["planner"]


def test_user_edited_plan_is_used_and_marked(wait_generation):
    pid, tid, cp = _project_inputs()
    plan_set = client.post(
        f"{A}/projects/{pid}/plans",
        json={"content_pack_id": cp, "brief": BRIEF, "strategies": ["balanced"]},
    ).json()
    plan = plan_set["plans"][0]["deck_plan"]
    plan["slides"] = plan["slides"][:4]
    for i, s in enumerate(plan["slides"]):
        s["index"] = i
    gen = client.post(
        f"{A}/projects/{pid}/generations",
        json={
            "template_id": tid,
            "content_pack_id": cp,
            "brief": BRIEF,
            "variants": [{"strategy": "balanced"}],
            "deck_plan": plan,
        },
    )
    assert gen.status_code == 202, gen.text
    run = wait_generation(client, gen.json()["generation_id"])
    assert run["state"] == "completed"
    assert run["variants"][0]["planner"].startswith("user_edited:")
    slides = client.get(f"{A}/variants/{gen.json()['variant_ids'][0]}/slides").json()["items"]
    assert len(slides) == 4


def test_plan_inputs_are_validated():
    pid, tid, cp = _project_inputs()
    plan_set = client.post(
        f"{A}/projects/{pid}/plans",
        json={"content_pack_id": cp, "brief": BRIEF, "strategies": ["balanced"]},
    ).json()
    base = {
        "template_id": tid,
        "content_pack_id": cp,
        "brief": BRIEF,
        "variants": [{"strategy": "balanced"}],
    }
    both = client.post(
        f"{A}/projects/{pid}/generations",
        json={
            **base,
            "deck_plan_id": plan_set["plan_id"],
            "deck_plan": plan_set["plans"][0]["deck_plan"],
        },
    )
    assert both.status_code == 422
    wrong_strategy = client.post(
        f"{A}/projects/{pid}/generations",
        json={**base, "variants": [{"strategy": "visual"}], "deck_plan_id": plan_set["plan_id"]},
    )
    assert wrong_strategy.status_code == 422
    missing = client.post(
        f"{A}/projects/{pid}/generations", json={**base, "deck_plan_id": "plan_nope"}
    )
    assert missing.status_code == 404
