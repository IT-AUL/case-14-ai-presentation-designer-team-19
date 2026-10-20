"""Рендер-проверка брендбука (ADR-033 §9, блок F): одна и та же колода с
брендбуком ``examples/stage2/brandbook`` и без него на трёх шаблонах.

Проверяет, что типы графиков совпадают, а цвета и шрифты темы разные;
складывает пары «без бренда / с брендом» в ``--out``. Модель не нужна.

    uv run python scripts/brand_render_check.py --out /tmp/brand
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from deckdna.generation.pipeline import generate  # noqa: E402
from deckdna.template.brandbook import apply_brandbook, build_brandbook, theme_of  # noqa: E402

BRAND = ROOT / "examples" / "stage2" / "brandbook"
TEMPLATES = ["vk_tech_template.pptx", "vk_education.pptx", "vk_workspace.pptx"]
BRIEF = {"purpose": "Итоги продаж", "audience": "руководство", "language": "ru",
         "target_slide_count": 6}


def _row(pdf: Path, tmp: Path) -> object:
    from PIL import Image

    # Fixed Poppler command and argv; no shell or executable supplied by the input.
    subprocess.run(  # noqa: S603
        ["pdftoppm", "-r", "30", "-png", str(pdf), str(tmp)], check=True,  # noqa: S607
    )
    pages = sorted(tmp.parent.glob(tmp.name + "-*.png"))
    ims = [Image.open(p) for p in pages]
    w, h = ims[0].size
    row = Image.new("RGB", (w * len(ims), h), "white")
    for i, im in enumerate(ims):
        row.paste(im, (i * w, 0))
    for p in pages:
        p.unlink()
    return row


def _theme(pptx: Path) -> tuple[dict, dict]:
    with zipfile.ZipFile(io.BytesIO(pptx.read_bytes())) as z:
        return theme_of({n: z.read(n) for n in z.namelist()})


def main() -> int:
    from PIL import Image

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--data", type=Path, default=ROOT / "examples" / "stage2" / "purchases.csv")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    book = build_brandbook([(f.name, f.read_bytes()) for f in sorted(BRAND.iterdir())], "brand")
    failed = 0
    summary = []
    for name in TEMPLATES:
        src = ROOT / "tests" / "fixtures" / "pptx" / name
        branded, report = apply_brandbook(src.read_bytes(), book)
        tpl = args.out / f"{src.stem}_brand.pptx"
        tpl.write_bytes(branded)
        rows, types, themes = [], [], []
        for tag, template in (("plain", src), ("brand", tpl)):
            run = generate(template, args.data, BRIEF, args.out / f"{src.stem}_{tag}")
            types.append([(s.get("chart") or {}).get("type")
                          for s in run["deck_plan"]["slides"] if s.get("chart")])
            themes.append(_theme(Path(run["artifacts"]["pptx"])))
            rows.append(_row(Path(run["artifacts"]["pdf"]), args.out / f"{src.stem}_{tag}"))
        ok = (types[0] == types[1] and themes[0][0].get("accent1") != themes[1][0].get("accent1")
              and themes[0][1] != themes[1][1])
        failed += not ok
        row = {"template": name, "chart_types": types[1], "same_types": types[0] == types[1],
               "accent1": [themes[0][0].get("accent1"), themes[1][0].get("accent1")],
               "fonts": [themes[0][1], themes[1][1]],
               "conflicts": [c.field for c in report.conflicts], "ok": ok}
        summary.append(row)
        print(json.dumps(row, ensure_ascii=False))
        sheet = Image.new("RGB", (rows[0].size[0], rows[0].size[1] * 2 + 6), "white")
        sheet.paste(rows[0], (0, 0))
        sheet.paste(rows[1], (0, rows[0].size[1] + 6))
        sheet.save(args.out / f"brand_{src.stem}.png", optimize=True)
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
    print(f"templates failing the check: {failed} of {len(summary)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
