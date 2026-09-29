"""analyze_template contract test: POST /templates/{id}/analyze must run the
real template autopsy on the uploaded bytes, and the surfaced numbers must
match analyze_template() on the same fixture — nothing invented."""

from pathlib import Path

import pytest
from deckdna.api import app as app_module
from deckdna.api.app import app
from deckdna.providers.mock import MockProvider
from deckdna.template.autopsy import analyze_template
from deckdna.template.dna import EXEMPLAR_DESCRIBE_PROMPT
from fastapi.testclient import TestClient

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "pptx" / "vk_tech_template.pptx"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

client = TestClient(app)


@pytest.fixture(scope="module")
def forensics():
    return analyze_template(FIXTURE)


def _upload_and_analyze(filename: str, data: bytes) -> tuple[str, dict]:
    project = client.post("/api/v1/projects", json={"name": "autopsy test"}).json()
    response = client.post(
        f"/api/v1/projects/{project['id']}/templates",
        files={"file": (filename, data, PPTX_MIME)},
    )
    assert response.status_code == 201, response.text
    template_id = response.json()["id"]
    accepted = client.post(f"/api/v1/templates/{template_id}/analyze", json={})
    assert accepted.status_code == 202, accepted.text
    return template_id, accepted.json()


@pytest.mark.parametrize("name", ["absolute", "traversal", "windows"])
def test_template_upload_rejects_filename_paths(name, tmp_path):
    filename = {
        "absolute": str(tmp_path / "outside.pptx"),
        "traversal": "../outside.pptx",
        "windows": r"..\outside.pptx",
    }[name]
    project = client.post("/api/v1/projects", json={"name": "path audit"}).json()
    response = client.post(
        f"/api/v1/projects/{project['id']}/templates",
        files={"file": (filename, FIXTURE.read_bytes(), PPTX_MIME)},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_input"


def test_analyze_runs_real_autopsy(forensics):
    template_id, accepted = _upload_and_analyze("vk_tech_template.pptx", FIXTURE.read_bytes())
    assert accepted["job_id"] and accepted["analysis_id"]

    detail = client.get(f"/api/v1/templates/{template_id}").json()
    inventory = detail["latest_analysis"]["package_inventory"]
    for key in (
        "slides",
        "masters",
        "layouts",
        "themes",
        "media",
        "charts",
        "embeddings",
        "tables",
        "layout_usage",
        "declared_fonts",
        "observed_fonts",
    ):
        assert inventory[key] == forensics.to_dict()[key], key
    assert inventory["dominant_layout"] == list(forensics.dominant_layout)


def test_design_dna_matches_autopsy(forensics):
    template_id, _ = _upload_and_analyze("vk_tech_template.pptx", FIXTURE.read_bytes())

    dna = client.get(f"/api/v1/templates/{template_id}/design-dna").json()
    declared = dna["declared"]
    assert len(declared["masters"]) == forensics.masters
    assert len(declared["layouts"]) == forensics.layouts
    assert len(declared["themes"]) == forensics.themes
    # Theme fonts are read per theme part; they must be a subset of the
    # autopsy declared-font list (or the honest "unspecified" marker).
    allowed = set(forensics.declared_fonts) | {"unspecified"}
    for theme in declared["themes"]:
        assert theme["major_font"] in allowed
        assert theme["minor_font"] in allowed

    observed = {f["value"]: f["frequency"] for f in dna["observed"]["fonts"]}
    assert observed == forensics.observed_fonts

    # Slide census is real and every semantic field is now computed from the
    # package itself (contract D1): no "unresolved" markers anywhere.
    assert len(dna["exemplars"]) == forensics.slides
    assert "unresolved" not in str(dna)
    assert all(e["role"] and e["cluster_id"] for e in dna["exemplars"])
    assert dna["slide_size"]["width_emu"] > 0
    assert dna["slide_size"]["height_emu"] > 0
    assert dna["observed"]["font_sizes"], "font scale is observed from real runs"
    assert dna["slide_roles"], "slide roles are inferred"
    assert dna["anchors"], "recurring corner element is detected as an anchor"
    assert any(lay["placeholders"] for lay in declared["layouts"])
    assert all(m["layout_parts"] for m in declared["masters"])
    # role slide_ids resolve against GET /templates/{id}/slides
    slide_ids = {
        i["id"]
        for i in client.get(f"/api/v1/templates/{template_id}/slides?limit=200").json()["items"]
    }
    assert {sid for r in dna["slide_roles"] for sid in r["slide_ids"]} <= slide_ids


def test_template_slides_use_real_part_names(forensics):
    template_id, _ = _upload_and_analyze("vk_tech_template.pptx", FIXTURE.read_bytes())
    items = client.get(f"/api/v1/templates/{template_id}/slides?limit=200").json()["items"]
    assert len(items) == forensics.slides
    assert all(i["part"].startswith("ppt/slides/slide") for i in items)


def test_analyze_default_leaves_content_description_empty():
    """No use_llm, no provider_session_id -> ADR-018 enrichment stays
    off (same auto semantics as generation: offline MockProvider alone
    never fabricates content -- see _server_provider_ready)."""
    template_id, _ = _upload_and_analyze("vk_tech_template.pptx", FIXTURE.read_bytes())
    dna = client.get(f"/api/v1/templates/{template_id}/design-dna").json()
    assert all(e.get("content_description") is None for e in dna["exemplars"])


def test_analyze_use_llm_fills_content_description(monkeypatch):
    """ADR-018 end-to-end: use_llm=true routes analyze through
    describe_exemplars, and the result lands in every exemplar the
    fixture answered for -- GET design-dna reflects it, cached for the
    template, no re-computation needed on future reads."""

    def _fixture(payload: dict) -> dict:
        return {
            "slides": [
                {
                    "index": s["index"],
                    "description": f"описание {s['index']}",
                    "is_entity_specific": s["index"] == 12,  # slide13.xml only
                }
                for s in payload["slides"]
            ]
        }

    gateway = MockProvider(fixtures={EXEMPLAR_DESCRIBE_PROMPT: _fixture})
    monkeypatch.setattr(app_module, "build_gateway", lambda config=None: gateway)

    project = client.post("/api/v1/projects", json={"name": "adr018 test"}).json()
    upload = client.post(
        f"/api/v1/projects/{project['id']}/templates",
        files={"file": ("vk_tech_template.pptx", FIXTURE.read_bytes(), PPTX_MIME)},
    )
    template_id = upload.json()["id"]
    accepted = client.post(
        f"/api/v1/templates/{template_id}/analyze", json={"use_llm": True}
    )
    assert accepted.status_code == 202, accepted.text

    dna = client.get(f"/api/v1/templates/{template_id}/design-dna").json()
    described = [e for e in dna["exemplars"] if e.get("content_description")]
    assert described  # at least some exemplars got a real description
    assert all(e["content_description"].startswith("описание ") for e in described)
    assert gateway.calls  # the gateway really was invoked

    # ADR-018 v1.1.0: is_entity_specific rides along with content_description
    slide13 = next(e for e in dna["exemplars"] if e["part"] == "ppt/slides/slide13.xml")
    assert slide13["content_is_entity_specific"] is True
    others = [e for e in described if e["part"] != "ppt/slides/slide13.xml"]
    assert all(e.get("content_is_entity_specific") is False for e in others)


def test_upload_rejects_corrupt_package_before_analyze():
    """A corrupt package must not reach
    validation_status="valid" — upload itself opens and validates the
    OPC structure now, instead of surfacing corruption only at /analyze."""
    project = client.post("/api/v1/projects", json={"name": "corrupt"}).json()
    response = client.post(
        f"/api/v1/projects/{project['id']}/templates",
        files={"file": ("broken.pptx", b"not a zip at all", PPTX_MIME)},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "package_corrupt"
