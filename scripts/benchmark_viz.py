"""Бенчмарк выбора визуализации (ADR-033 §10).

Каждый случай из ``tests/analysis/benchmark/labels.yaml`` размечен
эталоном «задача + допустимые типы». Считается главное представление
датасета. Печатает точность задачи, топ-1 (первый тип в ``best``) и топ-3
(в тройке есть тип из ``best`` или ``ok``) и строки с ошибками.

    uv run python scripts/benchmark_viz.py [--json report.json]
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from deckdna.analysis import analyze_dataset  # noqa: E402
from deckdna.ingestion.content_parsers import parse_file  # noqa: E402

LABELS = ROOT / "tests" / "analysis" / "benchmark" / "labels.yaml"


def run(labels: Path = LABELS) -> dict:
    cases = yaml.safe_load(labels.read_text(encoding="utf-8"))["cases"]
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for case in cases:
            if "file" in case:
                path = ROOT / case["file"]
                name = Path(case["file"]).name
            else:
                name = case["name"]
                path = Path(tmp) / f"{name}.csv"
                path.write_text(case["csv"].replace("\\n", "\n"), encoding="utf-8")
            pack = parse_file(path)
            dataset = pack.datasets[0]
            if case.get("context"):
                dataset = dataset.model_copy(update={"context_text": case["context"]})
            analysis = analyze_dataset(dataset)
            va = analysis.primary
            ranked = [r.chart_type for r in va.rec.ranked]
            allowed = set(case["best"]) | set(case.get("ok") or [])
            rows.append(
                {
                    "case": name,
                    "task": va.task.task,
                    "task_expected": case["task"],
                    "ranked": ranked,
                    "best": case["best"],
                    "task_ok": va.task.task == case["task"],
                    "top1": bool(ranked) and ranked[0] in case["best"],
                    "top3": bool(set(ranked[:3]) & allowed),
                }
            )
    n = len(rows)
    return {
        "cases": n,
        "task_accuracy": round(sum(r["task_ok"] for r in rows) / n, 3),
        "top1_accuracy": round(sum(r["top1"] for r in rows) / n, 3),
        "top3_accuracy": round(sum(r["top3"] for r in rows) / n, 3),
        "rows": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    report = run()
    print(f"cases: {report['cases']}")
    print(f"task accuracy: {report['task_accuracy']:.0%}")
    print(f"top-1 chart accuracy: {report['top1_accuracy']:.0%}")
    print(f"top-3 chart accuracy: {report['top3_accuracy']:.0%}")
    for r in report["rows"]:
        if not (r["task_ok"] and r["top1"]):
            print(
                f"  miss: {r['case']}: task {r['task']} (want {r['task_expected']}), "
                f"ranked {r['ranked']} (want {r['best']})"
            )
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
