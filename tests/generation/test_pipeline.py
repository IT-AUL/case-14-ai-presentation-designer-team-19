"""End-to-end pipeline: content file -> ContentPack -> DeckPlan -> deck ->
audit -> pdf -> QualityPassport.

Runs the real generation pipeline on the organizer template and a
markdown fixture; verifies every artifact exists and the passport
validates against schemas/quality-passport.schema.json.
"""

import json
import shutil
import zipfile
from pathlib import Path

import pytest
from deckdna.errors import DeckDNAError
from deckdna.generation.pipeline import generate
from jsonschema import validate

FIXTURE = Path("tests/fixtures/pptx/vk_tech_template.pptx")
CONTENT = Path("tests/fixtures/content/poc_article.md")
SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] / "schemas" / "quality-passport.schema.json").read_text()
)

BRIEF = {
    "purpose": "Показать сквозной пайплайн DeckDNA",
    "audience": "эксперты VK Tech",
    "language": "ru",
    "target_slide_count": 12,
}

HAS_SOFFICE = shutil.which("soffice") is not None


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    if not FIXTURE.exists():
        pytest.skip("organizer fixture not present")
    if not HAS_SOFFICE:
        pytest.skip("soffice not installed — pdf stage needs it")
    out_dir = tmp_path_factory.mktemp("pipeline")
    return generate(FIXTURE, CONTENT, dict(BRIEF), out_dir), out_dir


def test_all_artifacts_exist(report):
    result, _ = report
    for key in ("pptx", "pdf", "quality_passport"):
        path = Path(result["artifacts"][key])
        assert path.exists(), f"missing artifact: {path}"
    assert zipfile.is_zipfile(result["artifacts"]["pptx"])
    assert Path(result["artifacts"]["pdf"]).read_bytes()[:5] == b"%PDF-"


def test_deck_matches_plan_size(report):
    result, _ = report
    assert result["slides_out"] == BRIEF["target_slide_count"]
    assert result["compose_report"]["slides_out"] == BRIEF["target_slide_count"]


def test_quality_passport_validates_against_schema(report):
    result, _ = report
    validate(result["quality_passport"], SCHEMA)
    metrics = result["quality_passport"]["metrics"]
    assert metrics["validity"]["opens_cleanly"] is True
    assert metrics["editability"]["pei_level"] is not None
    assert metrics["timings"]["total_seconds"] > 0


def test_audit_issues_are_dicts_with_fields(report):
    result, _ = report
    for issue in result["audit_issues"]:
        assert issue["rule_code"]
        assert issue["severity"] in {"blocker", "error", "warning", "info"}


def test_unsupported_content_format_typed(tmp_path):
    weird = tmp_path / "content.xyz"
    weird.write_text("whatever", encoding="utf-8")
    with pytest.raises(DeckDNAError) as excinfo:
        generate(FIXTURE, weird, dict(BRIEF), tmp_path / "out")
    assert excinfo.value.code == "invalid_input"
    assert excinfo.value.stage == "content_ingestion"


def test_bad_slide_count_typed(tmp_path):
    if not FIXTURE.exists():
        pytest.skip("organizer fixture not present")
    # Brief.target_slide_count is ge=3/le=40 (deck_plan.py) -- 3 is a
    # deliberately supported short-deck size now ("any input, any
    # template", 29.09), not below-minimum. Use a value actually outside
    # the contract's bounds instead.
    brief = dict(BRIEF, target_slide_count=2)
    with pytest.raises(DeckDNAError) as excinfo:
        generate(FIXTURE, CONTENT, brief, tmp_path / "out")
    assert excinfo.value.code == "invalid_input"
    assert excinfo.value.stage == "planning"
