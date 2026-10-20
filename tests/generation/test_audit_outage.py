"""F-72a: final VLM failure preserves real generated artifacts and coverage."""

import json
from pathlib import Path

from deckdna.contracts.deck_plan import Brief
from deckdna.errors import DeckDNAError
from deckdna.generation.pipeline import generate
from deckdna.ingestion.content_parsers import parse_file
from deckdna.planning.story_director import plan_deck
from deckdna.providers.mock import MockProvider
from pptx import Presentation


def test_audit_provider_outage_keeps_deck_and_partial_passport(tmp_path):
    content = tmp_path / "content.md"
    content.write_text(
        "# Итоги пилота\n\nВ пилоте проверен один результат. Гарантий повторения пока нет.\n",
        encoding="utf-8",
    )
    brief = Brief(purpose="Итоги пилота", audience="команда", language="ru", target_slide_count=3)
    pack = parse_file(content)
    plan = plan_deck(pack, brief)

    def unavailable(payload):
        raise DeckDNAError(
            code="provider_unavailable", message="provider HTTP 500", stage="providers"
        )

    gateway = MockProvider(fixtures={"slide_checks": unavailable})
    report = generate(
        "tests/fixtures/pptx/synthetic_unseen.pptx", content, brief, tmp_path / "generated",
        deck_plan=plan, content_pack=pack, gateway=gateway,
    )
    pptx = Path(report["artifacts"]["pptx"])
    assert len(Presentation(pptx).slides) == report["slides_out"]
    assert Path(report["artifacts"]["pdf"]).read_bytes().startswith(b"%PDF")
    audit = report["contextual_audit"]
    assert audit["ran"] and audit["completed_slides"] == 0
    assert len(audit["unverified_slides"]) == audit["expected_slides"] >= 1
    assert set(audit["unverified_slides"]) <= set(range(report["slides_out"]))
    passport = json.loads(Path(report["artifacts"]["quality_passport"]).read_text())
    coverage = next(f for f in passport["fallbacks"] if f["feature"] == "audit.contextual.coverage")
    assert coverage["strategy"] == "partial"
    assert json.loads(coverage["disclosure"])["completed_slides"] == 0
