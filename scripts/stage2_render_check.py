"""Рендер-проверка второй ступени (ADR-033, блок D): каждый датасет
``examples/stage2/`` на трёх организаторских шаблонах.

Печатает по каждой паре тип графика на слайдах с данными и проблемы
аудита ``text.overflow`` / ``chart.metadata`` / ``layout.out_of_bounds`` на
них; складывает монтажи PNG в ``--out``. Модель не используется.

    uv run python scripts/stage2_render_check.py --out /tmp/stage2
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from deckdna.generation.pipeline import generate  # noqa: E402

STAGE2 = ROOT / "examples" / "stage2"
TEMPLATES = ["vk_tech_template.pptx", "vk_education.pptx", "vk_workspace.pptx"]
DATASETS = [
    "purchases.csv",
    "sales_monthly.csv",
    "regions.xlsx",
    "channels_share.json",
    "order_values.csv",
    "products_reference.xlsx",
    "nps_with_gaps.csv",
    "report_with_table.txt",
    "ad_spend_vs_sales.csv",
    "kpi.json",
]
BAD = ("text.overflow", "chart.metadata", "layout.out_of_bounds")


def _montage(pdf: Path, png: Path) -> None:
    from PIL import Image

    tmp = png.with_suffix("")
    # Fixed Poppler executable and argument vector, no shell or executable from input.
    subprocess.run(  # noqa: S603
        ["pdftoppm", "-r", "36", "-png", str(pdf), str(tmp)], check=True,  # noqa: S607
    )
    pages = sorted(tmp.parent.glob(tmp.name + "-*.png"))
    ims = [Image.open(p) for p in pages]
    w, h = ims[0].size
    cols = 4
    rows = (len(ims) + cols - 1) // cols
    sheet = Image.new("RGB", (w * cols, h * rows), "white")
    for i, im in enumerate(ims):
        sheet.paste(im, ((i % cols) * w, (i // cols) * h))
    sheet.save(png, optimize=True)
    for p in pages:
        p.unlink()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--datasets", nargs="*", default=DATASETS)
    ap.add_argument("--templates", nargs="*", default=TEMPLATES)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    summary = []
    failed = 0
    for name in args.datasets:
        for template in args.templates:
            out = args.out / f"{Path(name).stem}__{Path(template).stem}"
            report = generate(
                ROOT / "tests" / "fixtures" / "pptx" / template,
                STAGE2 / name,
                {
                    "purpose": "Итоги по данным",
                    "audience": "руководство",
                    "language": "ru",
                    "target_slide_count": 6,
                },
                out,
            )
            slides = report["deck_plan"]["slides"]
            data = {s["index"] for s in slides if s["purpose"] == "data"}
            types = [
                (s.get("chart") or {}).get("type")
                or next(
                    (u["kind"] for u in s["content_units"] if u["kind"] in ("table", "chart")),
                    "text",
                )
                for s in slides
                if s["index"] in data
            ]
            bad = [
                (i["slide_index"], i["rule_code"])
                for i in report["audit_issues"]
                if i["slide_index"] in data and i["rule_code"] in BAD
            ]
            failed += bool(bad)
            row = {"dataset": name, "template": template, "types": types, "bad": bad}
            summary.append(row)
            print(json.dumps(row, ensure_ascii=False))
            _montage(Path(report["artifacts"]["pdf"]), args.out / f"{out.name}.png")
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"pairs with issues on data slides: {failed} of {len(summary)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
