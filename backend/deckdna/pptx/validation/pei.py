"""PEI (Presentation Editability Index) assessment on a generated .pptx.

Rubric (docs/AUDIT.md — слой оценки качества):

- L0 — package unopenable or every slide is a raster-only bitmap;
- L1 — some native editable text exists;
- L2 — text AND vector shapes native (raster only for true imagery);
- L3 — all primary elements native, incl. grouped shapes; no raster-only slide;
- L4 — L3 + native tables/charts (editable data objects, not pictures);
- L5 — L4 + semantic structure: slides use template placeholders/layouts.

Each gate maps to a concrete package fact the export validation already
measures: OPC openability, slide XML shape census, graphicFrame chart/
table detection, placeholder usage, layout rels.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
NS = {"p": P, "a": A, "c": C}


@dataclass
class SlideFacts:
    index: int
    text_shapes: int = 0
    vector_shapes: int = 0
    pictures: int = 0
    groups: int = 0
    tables: int = 0
    charts: int = 0
    placeholders: int = 0
    has_layout_rel: bool = False

    @property
    def raster_only(self) -> bool:
        """Slide whose entire content is bitmap picture(s) with no editable
        text or vector shape — fails the TZ 'native objects' requirement."""
        return self.pictures > 0 and self.text_shapes == 0 and self.vector_shapes == 0


@dataclass
class PeiReport:
    path: str
    openable: bool
    level: int
    slides: list[SlideFacts] = field(default_factory=list)
    raster_only_slides: list[int] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "openable": self.openable,
            "pei_level": self.level,
            "raster_only_slides": self.raster_only_slides,
            "reasons": self.reasons,
            "slide_count": len(self.slides),
        }


def _slide_facts(xml: bytes, index: int, has_layout_rel: bool) -> SlideFacts:
    root = etree.fromstring(xml)
    facts = SlideFacts(index=index, has_layout_rel=has_layout_rel)
    for sp in root.iter(f"{{{P}}}sp"):
        nv = sp.find(f".//{{{P}}}nvSpPr/{{{P}}}nvPr/{{{P}}}ph", NS)
        if nv is not None:
            facts.placeholders += 1
        if sp.findall(f".//{{{A}}}t"):
            facts.text_shapes += 1
        if sp.find(f".//{{{A}}}prstGeom", NS) is not None or (
            sp.find(f".//{{{A}}}custGeom", NS) is not None
        ):
            facts.vector_shapes += 1
    facts.pictures = len(root.findall(f".//{{{P}}}pic"))
    facts.groups = len(root.findall(f".//{{{P}}}grpSp"))
    facts.tables = len(root.findall(f".//{{{A}}}tbl"))
    facts.charts = len(root.findall(f".//{{{C}}}chart"))
    return facts


def assess_pptx(path: str | Path) -> PeiReport:
    """Compute the PEI level of a .pptx package. Pure stdlib+lxml — no
    LibreOffice dependency, safe to run inside the export stage."""
    path = Path(path)
    report = PeiReport(path=str(path), openable=False, level=0)
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        report.reasons.append("package cannot be opened as OPC zip")
        return report

    names = set(zf.namelist())
    slide_names = sorted(
        n for n in names if n.startswith("ppt/slides/slide") and n.endswith(".xml")
    )
    if "[Content_Types].xml" not in names or not slide_names:
        report.reasons.append("missing [Content_Types].xml or slides")
        return report

    report.openable = True
    for name in slide_names:
        idx = int(name.removeprefix("ppt/slides/slide").removesuffix(".xml"))
        rels_name = f"ppt/slides/_rels/slide{idx}.xml.rels"
        has_layout = False
        if rels_name in names:
            rels = etree.fromstring(zf.read(rels_name))
            has_layout = any(
                r.get("Type", "").endswith("/slideLayout") for r in rels.iter()
            )
        report.slides.append(_slide_facts(zf.read(name), idx, has_layout))

    report.raster_only_slides = [s.index for s in report.slides if s.raster_only]
    n = len(report.slides)

    if all(s.raster_only for s in report.slides):
        report.reasons.append("every slide is raster-only")
        return report
    if not any(s.text_shapes for s in report.slides):
        report.reasons.append("no editable text in any slide")
        return report

    report.level = 1
    if any(s.vector_shapes for s in report.slides):
        report.level = 2
    else:
        report.reasons.append("no vector shapes — text only")
        return report

    if report.raster_only_slides:
        report.reasons.append(
            f"raster-only slides block L3: {report.raster_only_slides}"
        )
        return report

    native_primary = all(
        (s.vector_shapes or s.text_shapes or s.groups or s.tables or s.charts)
        for s in report.slides
    )
    if native_primary:
        report.level = 3
    else:
        report.reasons.append("some slides have no native primary elements")
        return report

    if any(s.tables or s.charts for s in report.slides):
        report.level = 4
    else:
        report.reasons.append("no native table/chart — capped at L3")
        return report

    placeholders_used = sum(s.placeholders for s in report.slides)
    all_bound = all(s.has_layout_rel for s in report.slides)
    if placeholders_used > 0 and all_bound:
        report.level = 5
    else:
        report.reasons.append(
            "no placeholder/layout semantic binding — capped at L4"
        )
    _ = n  # keep signature symmetrical for future per-slide weighting
    return report
