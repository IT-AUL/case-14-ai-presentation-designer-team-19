"""OOXML theme, color and text inheritance for the standalone HTML export."""

from __future__ import annotations

import json
import re
from html import escape

from lxml import etree

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"a": A, "p": P}


def child(node, path):
    return node.find(path, NS) if node is not None else None


def attr(nodes, name, default=None):
    return next((n.get(name) for n in nodes if n is not None and n.get(name) is not None), default)


def first(nodes, path):
    return next((v for n in nodes if (v := child(n, path)) is not None), None)


def css_string(value):
    # A CSS string, escaped again by the caller when used in an HTML attribute.
    return json.dumps(value, ensure_ascii=False).replace("<", "\\3c ")


class Theme:
    def __init__(self, slide):
        self.slide = slide
        master = slide.slide_layout.slide_master
        self.root = None
        for rel in master.part.rels.values():
            if rel.reltype.endswith("/theme") and not rel.is_external:
                self.root = etree.fromstring(rel.target_part.blob)
                break
        self.palette = {}
        scheme = child(self.root, ".//a:clrScheme")
        if scheme is not None:
            for slot in scheme:
                self.palette[etree.QName(slot).localname] = self.color(slot, "#000000")
        self.cmap = {"tx1": "dk1", "bg1": "lt1", "tx2": "dk2", "bg2": "lt2"}
        cmap = child(master.element, "p:clrMap")
        if cmap is not None:
            self.cmap.update(cmap.attrib)
        for owner in (slide.slide_layout, slide):
            override = child(owner.element, "p:clrMapOvr/a:overrideClrMapping")
            if override is not None:
                self.cmap.update(override.attrib)

    def color(self, fill, default="transparent"):
        if fill is None or len(fill) == 0:
            return default
        el = fill[0]
        tag = etree.QName(el).localname
        if tag == "srgbClr":
            value = "#" + el.get("val", "")
        elif tag == "sysClr":
            value = "#" + el.get("lastClr", "000000")
        elif tag == "schemeClr":
            name = el.get("val", "")
            value = self.palette.get(self.cmap.get(name, name), default)
        else:
            return default
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            return default
        rgb = [int(value[i : i + 2], 16) for i in (1, 3, 5)]
        alpha = 1.0
        for mod in el:
            kind = etree.QName(mod).localname
            factor = float(mod.get("val", "100000")) / 100000
            if kind in ("lumMod", "shade"):
                rgb = [c * factor for c in rgb]
            elif kind == "lumOff":
                rgb = [c + 255 * factor for c in rgb]
            elif kind == "tint":
                rgb = [c * factor + 255 * (1 - factor) for c in rgb]
            elif kind == "alpha":
                alpha = factor
        rgb = [round(max(0, min(255, c))) for c in rgb]
        return (
            ("#" + "".join(f"{c:02X}" for c in rgb))
            if alpha == 1
            else (f"rgba({rgb[0]},{rgb[1]},{rgb[2]},{alpha:.4f})")
        )

    def font(self, face=None, title=False):
        face = face or ("+mj-lt" if title else "+mn-lt")
        if face.startswith(("+mj", "+mn")):
            family = "majorFont" if face.startswith("+mj") else "minorFont"
            node = child(self.root, f".//a:fontScheme/a:{family}/a:latin")
            face = node.get("typeface") if node is not None else None
        return f"{css_string(face or 'Arial')},Carlito,Arial,sans-serif"

    def style_fill(self, reference, background=False):
        if reference is None:
            return None
        idx = int(reference.get("idx", "0"))
        bg = background or idx >= 1001
        nodes = child(self.root, ".//a:fmtScheme/a:" + ("bgFillStyleLst" if bg else "fillStyleLst"))
        idx -= 1001 if bg else 1
        if nodes is None or not 0 <= idx < len(nodes):
            return None
        node = etree.fromstring(etree.tostring(nodes[idx]))
        for scheme in node.iter(f"{{{A}}}schemeClr"):
            if scheme.get("val") == "phClr" and len(reference):
                scheme.getparent().replace(scheme, etree.fromstring(etree.tostring(reference[0])))
        return node


def shape_chain(shape, slide):
    chain = [shape]
    if not shape.is_placeholder:
        return chain
    idx = shape.placeholder_format.idx
    kind = shape.placeholder_format.type
    for owner in (slide.slide_layout, slide.slide_layout.slide_master):
        match = next(
            (
                p
                for p in owner.placeholders
                if (
                    p.placeholder_format.idx == idx
                    if owner == slide.slide_layout
                    else p.placeholder_format.type == kind
                )
            ),
            None,
        )
        if match is not None:
            chain.append(match)
    return chain


class TextRenderer:
    def __init__(self, theme, height):
        self.theme = theme
        self.height = height

    def length(self, emu):
        return f"{float(emu) / self.height * 100:.5f}cqh"

    def rstyle(self, nodes, title=False):
        font = first(nodes, "a:latin")
        face = self.theme.font(font.get("typeface") if font is not None else None, title)
        size = float(attr(nodes, "sz", 3200 if title else 1800)) * 127
        style = [
            f"font-size:{self.length(size)}",
            f"font-family:{face}",
        ]
        for key, prop, yes, no in [
            ("b", "font-weight", "700", "400"),
            ("i", "font-style", "italic", "normal"),
        ]:
            val = attr(nodes, key)
            if val is not None:
                style.append(f"{prop}:{yes if val in ('1', 'true', 'on') else no}")
        if attr(nodes, "u", "none") != "none":
            style.append("text-decoration:underline")
        fill = first(nodes, "a:solidFill")
        if fill is not None:
            style.append("color:" + self.theme.color(fill, "#000000"))
        spacing = attr(nodes, "spc")
        if spacing:
            style.append("letter-spacing:" + self.length(float(spacing) * 127))
        return ";".join(style)

    def frame(self, frame, chain=(), shape=None, extra_defaults=()):
        bodies = [frame._txBody, *(s.text_frame._txBody for s in chain[1:] if s.has_text_frame)]
        body_props = [child(b, "a:bodyPr") for b in bodies]
        title = False
        style_nodes = []
        if shape is not None:
            ph = child(shape.element, ".//p:ph")
            ptype = ph.get("type", "obj") if ph is not None else "other"
            title = ptype in ("title", "ctrTitle")
            sty = (
                "titleStyle" if title else "bodyStyle" if ptype in ("body", "obj") else "otherStyle"
            )
            master = self.theme.slide.slide_layout.slide_master
            style_nodes = [child(master.element, f"p:txStyles/p:{sty}")]
        default_style = child(
            self.theme.slide.part.package.presentation_part._element, "p:defaultTextStyle"
        )
        paragraphs = []
        numbering = {}
        for para in frame.paragraphs:
            level = para.level
            props = [child(para._p, "a:pPr")]
            for b in bodies:
                props.append(child(b, f"a:lstStyle/a:lvl{level + 1}pPr"))
            # Layout/master first paragraph can declare inherited text properties.
            props.extend(child(b, "a:p/a:pPr") for b in bodies[1:])
            local_count = len(props)
            props.extend(child(s, f"a:lvl{level + 1}pPr") for s in [*style_nodes, default_style])
            defaults = [child(p, "a:defRPr") for p in props[:local_count]]
            defaults += list(extra_defaults)
            defaults += [child(p, "a:defRPr") for p in props[local_count:]]
            align = {"l": "left", "ctr": "center", "r": "right", "just": "justify"}.get(
                attr(props, "algn"), "left"
            )
            pstyle = [f"text-align:{align}", self.rstyle(defaults, title)]
            # CSS paragraph struts must follow the actual text size. Otherwise
            # small explicit runs inherit an 18pt line box and double row heights.
            visible_props = [child(para._p, "a:r/a:rPr"), child(para._p, "a:endParaRPr")]
            visible_size = attr(visible_props, "sz")
            if visible_size is not None:
                pstyle.append("font-size:" + self.length(float(visible_size) * 127))
            for key, prop in [
                ("marL", "margin-left"),
                ("marR", "margin-right"),
                ("indent", "text-indent"),
            ]:
                val = attr(props, key)
                if val is not None:
                    pstyle.append(f"{prop}:{self.length(val)}")
            for key, prop in [
                ("spcBef", "margin-top"),
                ("spcAft", "margin-bottom"),
                ("lnSpc", "line-height"),
            ]:
                node = first(props, f"a:{key}")
                pts, pct = child(node, "a:spcPts"), child(node, "a:spcPct")
                if pts is not None:
                    pstyle.append(f"{prop}:{self.length(float(pts.get('val')) * 127)}")
                elif pct is not None:
                    value = float(pct.get("val")) / 100000
                    pstyle.append(f"{prop}:{value if key == 'lnSpc' else str(value) + 'em'}")
            runs = []
            for el in para._p:
                tag = etree.QName(el).localname
                if tag == "br":
                    runs.append("<br>")
                elif tag in ("r", "fld"):
                    text = "".join(el.itertext()) if tag == "fld" else (child(el, "a:t").text or "")
                    # Fields have only a:t as visible content (not their metadata).
                    if tag == "fld":
                        text = "".join(t.text or "" for t in el.findall("a:t", NS))
                    run_style = self.rstyle([child(el, "a:rPr"), *defaults], title)
                    runs.append(
                        f'<span style="{escape(run_style, quote=True)}">{escape(text)}</span>'
                    )
            content = "".join(runs) or "<br>"
            bullet = next(
                (
                    c
                    for p in props
                    if p is not None
                    for c in p
                    if etree.QName(c).localname in ("buNone", "buChar", "buAutoNum")
                ),
                None,
            )
            tag, attrs = "p", ""
            if bullet is not None and etree.QName(bullet).localname != "buNone":
                bullet_color = first(props, "a:buClr")
                if bullet_color is not None:
                    pstyle.append(
                        "--bullet-color:" + self.theme.color(bullet_color, "currentColor")
                    )
                numbered = etree.QName(bullet).localname == "buAutoNum"
                tag = "ol" if numbered else "ul"
                if numbered:
                    start = int(bullet.get("startAt", numbering.get(level, 0) + 1))
                    numbering[level] = start
                    attrs = f' start="{start}"'
                else:
                    numbering.clear()
                    pstyle.append("list-style-type:" + css_string(bullet.get("char", "•") + " "))
                content = f"<li>{content}</li>"
            else:
                numbering.clear()
            paragraphs.append(
                f'<{tag}{attrs} style="{escape(";".join(pstyle), quote=True)}">{content}</{tag}>'
            )
        anchor = {"ctr": "center", "b": "flex-end"}.get(attr(body_props, "anchor"), "flex-start")
        padding = " ".join(
            self.length(attr(body_props, k, default))
            for k, default in (("tIns", 45720), ("rIns", 91440), ("bIns", 45720), ("lIns", 91440))
        )
        return (
            f'<div class="text-frame" style="justify-content:{anchor};padding:{padding}">'
            + "".join(paragraphs)
            + "</div>"
        )
