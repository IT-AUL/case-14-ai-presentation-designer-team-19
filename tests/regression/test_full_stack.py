"""Сквозная регрессия: все 4 шаблона через generate -> repair -> export.

3 organizer-фикстуры + synthetic_unseen. Проверяет инварианты, а не
золотые числа: пайплайн не падает, repair не увеличивает число issues,
экспорты существуют. Остаточные issues возможны честно — те, что не
исправимы end-to-end сегодня (нет маппинга в planner или требуют
нереализованный action_type: shorten_text/native_rebuild/...).

Тяжёлый тест (~30с): реальный soffice-рендер на каждом шаблоне.
"""

import shutil
import sys
from collections import Counter
from pathlib import Path

import pytest
from deckdna.audit.basic import audit_deck
from deckdna.generation.pipeline import generate
from deckdna.pptx.exporting.render import render_html, render_pdf
from deckdna.repair.apply import _IMPLEMENTED, apply_repairs
from deckdna.repair.planner import _RULE_HANDLERS, plan_repairs

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from full_stack_regression import BRIEF, CONTENT, TEMPLATES  # noqa: E402

HAS_SOFFICE = shutil.which("soffice") is not None
HAS_PDFTOPPM = shutil.which("pdftoppm") is not None

pytestmark = pytest.mark.skipif(
    not (HAS_SOFFICE and HAS_PDFTOPPM),
    reason="needs soffice + pdftoppm for real export",
)


@pytest.fixture(scope="module", params=TEMPLATES, ids=lambda t: t.stem)
def stack(request, tmp_path_factory):
    template = request.param
    if not template.exists():
        pytest.skip(f"fixture missing: {template.name}")
    out_dir = tmp_path_factory.mktemp(f"stack_{template.stem}")
    report = generate(template, CONTENT, dict(BRIEF), out_dir / "gen")
    deck = Path(report["artifacts"]["pptx"])
    before = audit_deck(deck)
    actions = plan_repairs(before)
    repaired = out_dir / "repaired.pptx"
    apply_report = apply_repairs(deck, actions, repaired)
    after = audit_deck(repaired)
    pdf = render_pdf(repaired, out_dir / "repaired.pdf")
    html = render_html(repaired, out_dir / "html")
    return {
        "report": report,
        "before": before,
        "after": after,
        "apply": apply_report,
        "repaired": repaired,
        "pdf": pdf,
        "html": html,
    }


def test_full_stack_no_errors(stack):
    report = stack["report"]
    assert report["slides_out"] == BRIEF["target_slide_count"]
    apply_report = stack["apply"]
    assert apply_report.failed == 0
    assert stack["repaired"].exists()
    assert stack["pdf"].exists() and stack["pdf"].stat().st_size > 0
    assert Path(stack["html"]).exists()


def test_repair_does_not_add_issues(stack):
    before, after = len(stack["before"]), len(stack["after"])
    assert after <= before, (
        f"repair worsened audit: {before} -> {after} "
        f"({Counter(i.rule_code for i in stack['after'])})"
    )
    # residual issues — только те, у которых нет end-to-end исправления:
    # либо rule_code без маппинга в planner (unresolved), либо хотя бы
    # одно из предложенных исправлений требует action_type, который
    # executor ещё не реализует (not_implemented). Issue, честно
    # исправимое сегодня, но оставшееся — это реальная регрессия.
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
