"""Сквозная регрессия по форматам контента: docx/pdf/xlsx x 4 шаблона.

Тот же прогон что test_full_stack (generate -> audit -> repair ->
re-audit -> export), но с бинарными content-фикстурами вместо markdown.
Проверяет инварианты, не золотые числа: пайплайн не падает ни на одной
комбинации, repair не увеличивает issues, экспорты существуют. Остатки —
честные (accessibility.contrast/layout.edge_margin не имеют исправителя).

Тяжёлый тест (~2-3 мин): 12 комбинаций x soffice-рендер.
"""

import shutil
import sys
from collections import Counter
from pathlib import Path

import pytest
from deckdna.audit.basic import audit_deck
from deckdna.generation.pipeline import generate
from deckdna.pptx.exporting.render import render_pdf
from deckdna.repair.apply import _IMPLEMENTED, apply_repairs
from deckdna.repair.planner import _RULE_HANDLERS, plan_repairs

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from full_stack_regression import BRIEF, TEMPLATES  # noqa: E402

CONTENTS = [
    ROOT / "tests" / "fixtures" / "content" / "poc_report.docx",
    ROOT / "tests" / "fixtures" / "content" / "poc_report.pdf",
    ROOT / "tests" / "fixtures" / "content" / "poc_tables.xlsx",
]

HAS_SOFFICE = shutil.which("soffice") is not None

pytestmark = pytest.mark.skipif(
    not HAS_SOFFICE,
    reason="needs soffice for real export",
)

CASES = [(t, c) for t in TEMPLATES for c in CONTENTS]


@pytest.fixture(scope="module", params=CASES, ids=lambda c: f"{c[0].stem}__{c[1].suffix[1:]}")
def stack(request, tmp_path_factory):
    template, content = request.param
    if not template.exists():
        pytest.skip(f"fixture missing: {template.name}")
    if not content.exists():
        pytest.skip(
            f"content fixture missing: {content.name} "
            "(run scripts/build_content_fixtures.py)"
        )
    out_dir = tmp_path_factory.mktemp(f"fmt_{template.stem}_{content.suffix[1:]}")
    report = generate(template, content, dict(BRIEF), out_dir / "gen")
    deck = Path(report["artifacts"]["pptx"])
    before = audit_deck(deck)
    actions = plan_repairs(before)
    repaired = out_dir / "repaired.pptx"
    apply_report = apply_repairs(deck, actions, repaired)
    after = audit_deck(repaired)
    pdf = render_pdf(repaired, out_dir / "repaired.pdf")
    return {
        "report": report,
        "before": before,
        "after": after,
        "apply": apply_report,
        "repaired": repaired,
        "pdf": pdf,
    }


def test_no_errors_any_format(stack):
    report = stack["report"]
    assert report["slides_out"] == BRIEF["target_slide_count"]
    assert stack["apply"].failed == 0
    assert stack["repaired"].exists()
    assert stack["pdf"].exists() and stack["pdf"].stat().st_size > 0


def test_repair_does_not_add_issues(stack):
    before, after = len(stack["before"]), len(stack["after"])
    assert after <= before, (
        f"repair worsened audit: {before} -> {after} "
        f"({Counter(i.rule_code for i in stack['after'])})"
    )
    for i in stack["after"]:
        proposed = set(i.proposed_actions or [])
        fixable_now = (
            i.rule_code in _RULE_HANDLERS
            and bool(proposed)
            and proposed <= _IMPLEMENTED
        )
        assert not fixable_now, (
            f"неожиданный residual issue: {i.rule_code} slide {i.slide_id} "
            f"(все proposed_actions реализованы: {sorted(proposed)})"
        )
