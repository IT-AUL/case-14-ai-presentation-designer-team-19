"""ADR-034 §1 и ADR-033 §9 через API: «Закинь, что есть» раскладывает
файлы по типам, брендбук меняет цвета и шрифты колоды, но не графики."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from deckdna.api.app import STORE, app
from deckdna.template.brandbook import theme_of
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "tests" / "fixtures" / "pptx" / "vk_tech_template.pptx"
STAGE2 = ROOT / "examples" / "stage2"
BRAND = STAGE2 / "brandbook"
A = "/api/v1"
client = TestClient(app)


def _files(*paths: Path) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("files", (p.name, p.read_bytes(), "application/octet-stream")) for p in paths]


def _project() -> str:
    return client.post(f"{A}/projects", json={"name": "intake"}).json()["id"]


def test_intake_detects_kinds_and_builds_everything():
    pid = _project()
    resp = client.post(f"{A}/projects/{pid}/intake", files=_files(
        TEMPLATE, STAGE2 / "purchases.csv", STAGE2 / "report_with_table.txt",
        BRAND / "tokens.json", BRAND / "logo.png", BRAND / "guideline.pdf",
    ), data={"idea": "Итоги продаж"})
    assert resp.status_code == 202, resp.text
    body = resp.json()
    kinds = {f["name"]: f["detected_kind"] for f in body["files"]}
    assert kinds == {
        "vk_tech_template.pptx": "template", "purchases.csv": "data",
        "report_with_table.txt": "content", "tokens.json": "brandbook",
        "logo.png": "brandbook", "guideline.pdf": "brandbook",
    }
    assert all(f["accepted"] for f in body["files"])
    assert body["template_id"] and body["content_pack_id"] and body["brandbook_id"]
    assert body["assistant_note"].startswith("Принял")
    pack = STORE.content_packs[body["content_pack_id"]]
    assert {d.file_name for d in pack.datasets} >= {"purchases.csv"}

    tpl = client.get(f"{A}/templates/{body['template_id']}/summary").json()
    assert tpl["slides"] > 0 and tpl["palette"][0] == "#0077FF" and tpl["assistant_note"]

    bb = client.get(f"{A}/brandbooks/{body['brandbook_id']}/summary").json()
    assert bb["priority"] == "brandbook"
    assert any(c["field"] == "color.primary" and c["template"] == "#0077FF"
               and c["brandbook"] == "#E4572E" for c in bb["conflicts"])
    assert bb["logos"][0]["artifact_id"]
    flipped = client.patch(f"{A}/brandbooks/{body['brandbook_id']}",
                           json={"priority": "template"}).json()
    assert flipped["priority"] == "template"
    assert all(c["resolved"] == "template" for c in flipped["conflicts"])


def test_patch_file_kind_reroutes_and_idea_alone_makes_a_pack():
    pid = _project()
    body = client.post(f"{A}/projects/{pid}/intake",
                       files=_files(BRAND / "tokens.json")).json()
    assert body["brandbook_id"] and body["content_pack_id"] is None
    fixed = client.patch(f"{A}/projects/{pid}/intake/files/tokens.json",
                         json={"kind": "data"})
    assert fixed.status_code == 200, fixed.text
    fixed = fixed.json()
    assert fixed["brandbook_id"] is None
    assert fixed["files"][0]["kind"] == "data"
    bad = client.patch(f"{A}/projects/{pid}/intake/files/tokens.json",
                       json={"kind": "template"})
    assert bad.status_code == 422

    pid2 = _project()
    idea = client.post(f"{A}/projects/{pid2}/intake", data={"idea": "Как мы растём"}).json()
    assert idea["content_pack_id"] and idea["files"] == []
    assert client.post(f"{A}/projects/{pid2}/intake").status_code == 422


def _deck_and_types(pid: str, body: dict, wait_generation, **extra) -> tuple[bytes, list, dict]:
    gen = client.post(f"{A}/projects/{pid}/generations", json={
        "template_id": body["template_id"], "content_pack_id": body["content_pack_id"],
        "brief": {"purpose": "Итоги продаж", "audience": "руководство", "language": "ru",
                  "target_slide_count": 6},
        "variants": [{"strategy": "balanced"}], "use_llm": False, **extra,
    })
    assert gen.status_code == 202, gen.text
    run = wait_generation(client, gen.json()["generation_id"])
    assert run["state"] == "completed", run
    variant_id = gen.json()["variant_ids"][0]
    variant = client.get(f"{A}/variants/{variant_id}").json()
    deck = STORE.artifacts[variant["deck_artifact_id"]].data
    plan = STORE.runs[gen.json()["generation_id"]].variant_plans[variant_id]
    types = [(s.chart or {}).get("type") for s in plan.slides if s.chart]
    export = client.post(f"{A}/variants/{variant_id}/exports",
                         json={"formats": ["quality_passport"]})
    assert export.status_code in (200, 201, 202), export.text
    passport_id = next(a for a in STORE.runs[gen.json()["generation_id"]]
                       .variant_artifacts[variant_id].items() if a[0] == "quality_passport")[1]
    import json

    passport = json.loads(STORE.artifacts[passport_id].data)
    return deck, types, passport


def test_brandbook_changes_colors_and_fonts_not_chart_types(wait_generation):
    pid = _project()
    body = client.post(f"{A}/projects/{pid}/intake", files=_files(
        TEMPLATE, STAGE2 / "purchases.csv", BRAND / "tokens.json",
    )).json()
    branded, types_b, passport_b = _deck_and_types(pid, body, wait_generation)
    plain, types_p, passport_p = _deck_and_types(pid, body, wait_generation,
                                                 brand_priority="template")
    assert types_b == types_p and types_b

    def theme(data: bytes):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return theme_of({n: z.read(n) for n in z.namelist()})

    (colors_b, fonts_b), (colors_p, fonts_p) = theme(branded), theme(plain)
    assert colors_b["accent1"] == "#E4572E" and colors_p["accent1"] == "#0077FF"
    assert fonts_b["major"] == "DejaVu Serif" and fonts_p["major"] != fonts_b["major"]
    assert passport_b["brand"]["applied"] is True
    assert passport_b["brand"]["conflicts"]
    assert passport_p["brand"]["applied"] is False
    assert all(c["resolved"] == "template" for c in passport_p["brand"]["conflicts"])
