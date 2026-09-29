"""Skill manifest contract test: GET /api/v1/skill/manifest must serve the
repo's skill/manifest.yaml parsed to JSON, with a typed error when the file
is absent."""

from pathlib import Path

import yaml
from deckdna.api import app as api_module
from deckdna.api.app import app
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]

client = TestClient(app)


def test_skill_manifest_returns_real_yaml():
    response = client.get("/api/v1/skill/manifest")
    assert response.status_code == 200

    data = response.json()
    expected = yaml.safe_load(
        (REPO_ROOT / "skill" / "manifest.yaml").read_text(encoding="utf-8")
    )
    assert data == expected
    assert data["id"] == expected["id"]
    assert data["version"] == expected["version"]
    assert isinstance(data["tools"], list) and data["tools"]
    assert {i["name"] for i in data["inputs"]}
    assert {o["name"] for o in data["outputs"]}


def test_skill_manifest_missing_file_is_typed_error(tmp_path, monkeypatch):
    monkeypatch.setattr(api_module, "_REPO_ROOT", tmp_path)
    response = client.get("/api/v1/skill/manifest")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_version_and_capabilities_report_real_skill_version():
    expected = yaml.safe_load(
        (REPO_ROOT / "skill" / "manifest.yaml").read_text(encoding="utf-8")
    )

    version = client.get("/api/v1/version").json()
    assert version["skill_version"] == str(expected["version"])
    assert version["schema_version"]

    capabilities = client.get("/api/v1/capabilities").json()
    assert capabilities["skill"]["name"] == str(expected["id"])
    assert capabilities["skill"]["version"] == str(expected["version"])


def test_capabilities_advertises_only_real_support():
    from deckdna.audit import basic as audit_basic
    from deckdna.ingestion import content_parsers

    capabilities = client.get("/api/v1/capabilities").json()

    expected_parsers = sorted(
        suffix.lstrip(".") for suffix in content_parsers._PARSERS
    )
    assert capabilities["parsers"] == expected_parsers

    expected_rules = sorted(
        value
        for name, value in vars(audit_basic).items()
        if name.startswith("RULE_")
    )
    assert capabilities["audit_rules"] == expected_rules
    assert expected_rules  # audit actually has rules

    # Formats the API actually serves: html (zip viewer built from the PDF),
    # pdf, pptx and the quality passport.
    assert capabilities["exporters"] == ["html", "pdf", "pptx", "quality_passport"]
