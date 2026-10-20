"""Reproducible offline render review; inputs stay local and unchanged.

PYTHONPATH=backend .venv/bin/python scripts/review_external_templates.py \
  --template path/to/template.pptx --content path/to/source.md \
  --content examples/stage2/purchases.csv --out artifacts/external-review \
  --preserve-source-slide 16

The optional zero-based attribution slide moves to position zero in a test
copy, so its position can be protected even when the result is shorter than
the source. This preparation is explicit in the manifest, not a runtime rule.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from deckdna.generation.pipeline import generate
from deckdna.pptx.exporting.previews import montage_png, pdf_page_pngs
from deckdna.pptx.exporting.render import render_pdf
from deckdna.pptx.opc.package import OpcPackage
from pptx import Presentation


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--content", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--preserve-source-slide", type=int)
    args = parser.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    original_hash = digest(args.template)
    original_pdf = render_pdf(args.template, out / "original.pdf")
    (out / "original.png").write_bytes(
        montage_png(pdf_page_pngs(original_pdf), columns=3, thumb_width_px=480)
    )
    template = args.template
    protected = []
    preserved_xml = None
    if args.preserve_source_slide is not None:
        raw = OpcPackage.open(template)
        order = raw.ordered_slide_parts()
        index = args.preserve_source_slide
        if not 0 <= index < len(order):
            parser.error("--preserve-source-slide is outside the source presentation")
        prs = Presentation(template)
        ids = prs.slides._sldIdLst
        ids.insert(0, ids[index])
        template = out / "attribution-first.pptx"
        prs.save(template)
        prepared = OpcPackage.open(template)
        preserved_xml = prepared.parts[prepared.ordered_slide_parts()[0]]
        protected = [0]
    manifest = {
        "commit": subprocess.check_output(  # noqa: S603 — fixed read-only Git arguments
            [shutil.which("git") or "/usr/bin/git", "rev-parse", "HEAD"], text=True,
        ).strip(),
        "template": str(args.template), "template_sha256": original_hash,
        "prepared_sha256": digest(template),
        "preserve_source_slide": args.preserve_source_slide,
        "output_protected_indices": protected,
        "provider": "none", "design_accepted": False, "cases": [],
    }
    for index, source in enumerate(args.content):
        case_out = out / f"case-{index+1}"
        report = generate(template, source, {
            "purpose": "Результаты пилота" if source.suffix == ".md" else "Анализ данных",
            "audience": "команда", "language": "ru", "target_slide_count": 6,
        }, case_out, protected_slide_indices=protected)
        if preserved_xml is not None:
            actual = OpcPackage.open(report["artifacts"]["pptx"])
            if actual.parts["ppt/slides/slide1.xml"] != preserved_xml:
                raise RuntimeError("requested attribution slide was not preserved verbatim")
        (case_out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        (case_out / "contact.png").write_bytes(montage_png(
            pdf_page_pngs(report["artifacts"]["pdf"]), columns=2, thumb_width_px=640,
        ))
        manifest["cases"].append({
            "source": str(source), "source_sha256": digest(source),
            "slides": report["slides_out"], "seconds": report["duration_seconds"],
            "artifacts": report["artifacts"],
            "sha256": {key: digest(Path(path)) for key, path in report["artifacts"].items()},
            "issues": report["audit_issues"], "contact": str(case_out / "contact.png"),
        })
        (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        print(f"{source.name}: {report['slides_out']} slides, {report['duration_seconds']}s")
    if digest(args.template) != original_hash:
        raise RuntimeError("source template changed during review")


if __name__ == "__main__":
    main()
