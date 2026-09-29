"""Сквозная регрессия: generate -> audit -> repair -> re-audit -> export.

Для каждого из 4 шаблонов (3 organizer + synthetic_unseen) гоняет полный
пайплайн и печатает сводную таблицу: время, issues до/после repair,
статусы действий, ошибки по стадиям. Ошибка на одном шаблоне не роняет
остальные.

Usage: .venv/bin/python scripts/full_stack_regression.py [--work-dir DIR]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from deckdna.audit.basic import audit_deck  # noqa: E402
from deckdna.generation.pipeline import generate  # noqa: E402
from deckdna.pptx.exporting.render import render_html, render_pdf  # noqa: E402
from deckdna.repair.apply import apply_repairs  # noqa: E402
from deckdna.repair.planner import plan_repairs  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "pptx"
TEMPLATES = [
    FIXTURES / "vk_tech_template.pptx",
    FIXTURES / "vk_workspace.pptx",
    FIXTURES / "vk_education.pptx",
    FIXTURES / "synthetic_unseen.pptx",
]
CONTENT = ROOT / "tests" / "fixtures" / "content" / "poc_article.md"
BRIEF = {
    "purpose": "Сквозная регрессия пайплайна",
    "audience": "эксперты",
    "language": "ru",
    "target_slide_count": 12,
}


def run_template(template: Path, work_root: Path) -> dict:
    row: dict = {"template": template.name, "error": None}
    out_dir = work_root / template.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        t0 = time.monotonic()
        report = generate(template, CONTENT, dict(BRIEF), out_dir / "gen")
        row["generate_s"] = round(time.monotonic() - t0, 2)
        row["slides"] = report["slides_out"]
        deck = Path(report["artifacts"]["pptx"])
        row["issues_in_report"] = len(report["audit_issues"])

        issues_before = audit_deck(deck)
        row["issues_before"] = len(issues_before)
        row["rules_before"] = dict(
            Counter(i.rule_code for i in issues_before).most_common()
        )

        actions = plan_repairs(issues_before)
        repaired = out_dir / "repaired.pptx"
        apply_report = apply_repairs(deck, actions, repaired)
        row["actions"] = {
            "total": len(apply_report.results),
            "applied": apply_report.applied,
            "skipped": apply_report.skipped,
            "failed": apply_report.failed,
            "not_implemented": apply_report.not_implemented,
        }

        issues_after = audit_deck(repaired)
        row["issues_after"] = len(issues_after)
        row["rules_after"] = dict(
            Counter(i.rule_code for i in issues_after).most_common()
        )

        t0 = time.monotonic()
        row["pdf"] = str(render_pdf(repaired, out_dir / "repaired.pdf"))
        row["html"] = str(render_html(repaired, out_dir / "html"))
        row["export_s"] = round(time.monotonic() - t0, 2)
        row["total_s"] = round(row["generate_s"] + row["export_s"], 2)
    except Exception as exc:  # noqa: BLE001 — изоляция per-template
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["trace"] = traceback.format_exc()
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work-dir", type=Path, default=ROOT / "out" / "regression")
    ap.add_argument("--json-out", type=Path, default=None)
    args = ap.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)

    rows = [run_template(t, args.work_dir) for t in TEMPLATES]

    print("\n=== FULL-STACK REGRESSION ===")
    hdr = (
        f"{'template':<28} {'gen_s':>7} {'slides':>6} {'before':>6} "
        f"{'after':>6} {'appl':>5} {'fail':>5} {'n/i':>4} {'export_s':>8}  status"
    )
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        if r["error"]:
            blanks = " " * 54  # ширина колонок gen_s..export_s + разделители
            print(f"{r['template']:<28}{blanks}  ERROR: {r['error'][:60]}")
        else:
            a = r["actions"]
            print(
                f"{r['template']:<28} {r['generate_s']:>7} {r['slides']:>6} "
                f"{r['issues_before']:>6} {r['issues_after']:>6} {a['applied']:>5} "
                f"{a['failed']:>5} {a['not_implemented']:>4} {r['export_s']:>8}  ok"
            )
    print("\nper-rule issues (before -> after):")
    for r in rows:
        if not r["error"]:
            rb = json.dumps(r["rules_before"], ensure_ascii=False)
            ra = json.dumps(r["rules_after"], ensure_ascii=False)
            print(f"  {r['template']}: {rb} -> {ra}")

    if args.json_out:
        args.json_out.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
        print(f"\njson: {args.json_out}")
    return 1 if any(r["error"] for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
