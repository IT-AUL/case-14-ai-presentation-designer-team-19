"""Build simple flow diagrams as SmartArt-like grouped shapes (OR-004).

Baseline: «SmartArt-like grouped editable shapes» — NOT real
OOXML SmartArt (dgm parts, a separate heavy format). Each `Diagram`
from ContentPack.diagrams becomes one `p:grpSp` holding a row of
`roundRect` step boxes with `rightArrow` connectors between them —
fully native and editable in PowerPoint.

Placement mirrors add_table (PR #78): the group lands in the bounding
box of the largest *emptied* text body of the slide — the slot the
positional fill left without content. When no host body is big enough
the unit honestly drops.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from lxml import etree

from deckdna.contracts.content_pack import Diagram
from deckdna.pptx.composing.table_fill import _empty_body_hosts, _next_shape_id

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"

_MAX_STEPS = 8  # beyond that step text doesn't fit a row anyway
_STEP_H_EMU = 640_080  # ~0.7"
_ARROW_W_EMU = 274_320  # ~0.3"
_MIN_HOST_W_EMU = 1_800_000  # ~2" — a flow needs real width


@dataclass
class DiagramFillReport:
    """What the fill actually built — surfaced into the compose report."""

    diagrams_created: int = 0
    steps_written: int = 0
    units_dropped: int = 0
    diagram_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "diagrams_created": self.diagrams_created,
            "steps_written": self.steps_written,
            "units_dropped": self.units_dropped,
            "diagram_ids": self.diagram_ids,
        }


def _step_sp(sid: int, name: str, x: int, y: int, cx: int, cy: int, text: str) -> str:
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="{escape(name)}"/>'
        f"<p:cNvSpPr/><p:nvPr/></p:nvSpPr>"
        f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/>'
        f'<a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        f'<a:prstGeom prst="roundRect"><a:avLst/></a:prstGeom>'
        f'<a:solidFill><a:schemeClr val="accent1"/></a:solidFill></p:spPr>'
        f'<p:txBody><a:bodyPr anchor="ctr" wrap="square"/>'
        f'<a:p><a:pPr algn="ctr"/><a:r>'
        f'<a:rPr lang="ru-RU"><a:solidFill><a:schemeClr val="lt1"/>'
        f"</a:solidFill></a:rPr>"
        f"<a:t>{escape(text)}</a:t></a:r></a:p></p:txBody></p:sp>"
    )


def _arrow_sp(sid: int, name: str, x: int, y: int, cx: int, cy: int) -> str:
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="{escape(name)}"/>'
        f"<p:cNvSpPr/><p:nvPr/></p:nvSpPr>"
        f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/>'
        f'<a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        f'<a:prstGeom prst="rightArrow"><a:avLst/></a:prstGeom>'
        f'<a:solidFill><a:schemeClr val="accent1"/></a:solidFill></p:spPr>'
        f"<p:txBody><a:bodyPr/><a:p/></p:txBody></p:sp>"
    )


def _build_diagram_group(
    gid: int, diagram: Diagram, x: int, y: int, cx: int, cy: int
) -> etree._Element:
    """One `p:grpSp`: len(steps) roundRect boxes left→right with
    rightArrow shapes between them, filling the host's bounding box."""
    steps = diagram.steps
    n = len(steps)
    arrow_w = min(_ARROW_W_EMU, max(1, int(cx * 0.05)))
    box_w = max(1, (cx - (n - 1) * arrow_w) // n)
    box_h = min(cy, _STEP_H_EMU)
    y0 = y + (cy - box_h) // 2

    children: list[str] = []
    sid = gid + 1
    for i, step in enumerate(steps):
        bx = x + i * (box_w + arrow_w)
        children.append(_step_sp(sid, f"Step {i + 1}", bx, y0, box_w, box_h, step))
        sid += 1
        if i < n - 1:
            ah = int(box_h * 0.35)
            children.append(
                _arrow_sp(sid, f"Arrow {i + 1}", bx + box_w, y0 + (box_h - ah) // 2, arrow_w, ah)
            )
            sid += 1

    xml = (
        f'<p:grpSp xmlns:p="{P}" xmlns:a="{A}">'
        f'<p:nvGrpSpPr><p:cNvPr id="{gid}" name="Diagram {escape(diagram.id)}"/>'
        f"<p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>"
        f'<p:grpSpPr><a:xfrm><a:off x="{x}" y="{y0}"/>'
        f'<a:ext cx="{cx}" cy="{box_h}"/>'
        f'<a:chOff x="{x}" y="{y0}"/>'
        f'<a:chExt cx="{cx}" cy="{box_h}"/></a:xfrm></p:grpSpPr>'
        + "".join(children)
        + "</p:grpSp>"
    )
    return etree.fromstring(xml)


def _add_diagram(
    root: etree._Element,
    diagram: Diagram,
    used_hosts: set[tuple[int, int, int, int]],
) -> bool:
    """Create the group inside the largest emptied text body of the
    slide (same host convention as add_table). False when no host."""
    # A partial flow silently changes the process. Leave the entire unit
    # unplaced when its steps cannot fit this one-row native layout.
    if not 1 <= len(diagram.steps) <= _MAX_STEPS:
        return False
    sp_tree = root.find(f".//{{{P}}}cSld/{{{P}}}spTree")
    if sp_tree is None:
        return False
    host = next(
        (
            h
            for h in _empty_body_hosts(root)
            if h[3] >= _MIN_HOST_W_EMU and h[1:5] not in used_hosts
        ),
        None,
    )
    if host is None:
        return False
    _, x, y, cx, cy, sp = host
    used_hosts.add(host[1:5])
    sp_tree.append(
        _build_diagram_group(_next_shape_id(root), diagram, x, y, cx, cy)
    )
    return True


def fill_slide_diagrams(
    slide_xml: bytes,
    diagrams: list[Diagram],
    used_hosts: set[tuple[int, int, int, int]] | None = None,
) -> tuple[bytes, DiagramFillReport]:
    """Build one SmartArt-like group per resolved Diagram, in plan
    order. Units beyond available hosts honestly drop."""
    report = DiagramFillReport()
    if not diagrams:
        return slide_xml, report
    root = etree.fromstring(slide_xml)
    hosts = used_hosts if used_hosts is not None else set()
    for diagram in diagrams:
        if _add_diagram(root, diagram, hosts):
            report.diagrams_created += 1
            report.steps_written += len(diagram.steps)
            report.diagram_ids.append(diagram.id)
        else:
            report.units_dropped += 1
    if not report.diagrams_created:
        return slide_xml, report
    return etree.tostring(root, xml_declaration=True, standalone=True), report
