"""Standalone positioned HTML from PPTX objects, with no whole-slide rasterization."""

from __future__ import annotations

import hashlib
import io
import tempfile
import zipfile
from collections.abc import Sequence
from html import escape
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from deckdna.pptx.exporting.html_charts import chart_svg
from deckdna.pptx.exporting.html_style import A, R, TextRenderer, Theme, attr, child, shape_chain
from deckdna.pptx.exporting.html_tables import cell_regions, table_style, text_defaults
from deckdna.pptx.opc.normalize import normalize_template_bytes

CSS = """
*{box-sizing:border-box}body{margin:0;background:#eee;color:#222;font-family:Arial,sans-serif}
header,nav{max-width:1200px;margin:1rem auto;padding:0 1rem}h1{font-size:1.3rem}
nav{display:flex;gap:.7rem;flex-wrap:wrap}nav a{color:inherit}
main{max-width:1200px;margin:auto}.slide{margin:1rem 0;break-inside:avoid}
.slide-label{font-size:1rem;padding:.3rem 1rem;margin:0}.canvas{position:relative;
width:min(100%,calc((100vh - 96px)*var(--slide-ratio)));
max-height:calc(100vh - 96px);max-width:100%;object-fit:contain;
overflow:hidden;container-type:size;isolation:isolate;background:white}
.object{position:absolute;transform-origin:center}.object>svg,.picture,.background{
width:100%;height:100%;display:block}.background{position:absolute;inset:0;object-fit:fill}
.picture-window{position:absolute;inset:0;overflow:hidden}.picture{position:absolute;max-width:none}
.text-frame{position:absolute;inset:0;display:flex;flex-direction:column;overflow:hidden;
white-space:pre-wrap;overflow-wrap:normal;line-height:1.15}.text-frame p,
.text-frame ul,.text-frame ol{margin:0;flex-shrink:0;min-height:0}
.text-frame ul,.text-frame ol{padding-left:1.2em}.text-frame li{padding:0;text-indent:0}
.text-frame li::marker{color:var(--bullet-color,currentColor)}
.table{width:100%;height:100%;border-collapse:collapse;table-layout:fixed}
.table td{position:relative;padding:0;border:1px solid #aaa;vertical-align:top}
.cell{position:relative;min-height:100%}
.table .text-frame{position:relative;inset:auto;overflow:visible;min-height:100%}
@media(max-width:600px){header,nav{margin:.5rem auto}.slide{margin:.5rem 0}}
@media print{body{background:white}
header,nav,.slide-label{display:none}
main{max-width:none}.slide{margin:0;break-after:page}.slide:last-child{break-after:auto}
.canvas{width:100%;max-height:none}a{color:inherit;text-decoration:none}}
"""
SCRIPT = """
document.addEventListener('keydown',event=>{
 if(!['ArrowRight','ArrowLeft','PageDown','PageUp','Home','End'].includes(event.key))return;
 const slides=[...document.querySelectorAll('.slide')];if(!slides.length)return;
 let index=slides.findIndex(s=>s.getBoundingClientRect().bottom>80);
 if(index<0)index=slides.length-1;
 if(event.key==='Home')index=0;else if(event.key==='End')index=slides.length-1;
 else index=Math.max(0,Math.min(slides.length-1,
 index+(['ArrowRight','PageDown'].includes(event.key)?1:-1)));
 event.preventDefault();slides[index].scrollIntoView();history.replaceState(null,'','#'+slides[index].id);
});
"""


class HtmlDeck:
    def __init__(self, path):
        self.path = Path(path)
        self.prs = Presentation(io.BytesIO(normalize_template_bytes(self.path.read_bytes())))
        self.width, self.height = self.prs.slide_width, self.prs.slide_height
        self.assets = {}
        self._pdf = None
        self._temporary = None
        self.slide_index = 0

    def asset(self, blob, ext):
        ext = ext.lower().lstrip(".")
        if ext not in ("png", "jpg", "jpeg", "gif", "svg", "webp", "bmp", "ttf", "otf"):
            # Convert TIFF and other Pillow-supported package images for browsers.
            from PIL import Image

            with Image.open(io.BytesIO(blob)) as im:
                buffer = io.BytesIO()
                im.convert("RGBA").save(buffer, "PNG")
                blob, ext = buffer.getvalue(), "png"
        name = f"assets/{hashlib.sha256(blob).hexdigest()[:20]}.{ext}"
        self.assets[name] = blob
        return name

    def image(self, fill, part):
        blip = child(fill, ".//a:blip")
        rid = blip.get(f"{{{R}}}embed") if blip is not None else None
        if not rid:
            return ""
        rel = part.rels.get(rid)
        if rel is None or rel.is_external:
            return ""
        image = rel.target_part
        ext = Path(str(image.partname)).suffix
        return self.asset(image.blob, ext)

    def fill(self, props, part, theme):
        if props is None:
            return "", ""
        solid = child(props, "a:solidFill")
        if solid is not None:
            return theme.color(solid), ""
        if child(props, "a:noFill") is not None:
            return "transparent", ""
        blip = child(props, "a:blipFill")
        if blip is not None:
            src = self.image(blip, part)
            return "", f'<img class="background" src="{src}" alt="">' if src else ""
        gradient = child(props, "a:gradFill")
        if gradient is not None:
            stops = [
                f"{theme.color(stop)} {int(stop.get('pos', '0')) / 1000:g}%"
                for stop in gradient.findall("a:gsLst/a:gs", {"a": A})
            ]
            if stops:
                lin = child(gradient, "a:lin")
                angle = float(lin.get("ang", 0)) / 60000 + 90 if lin is not None else 90
                return f"linear-gradient({angle:g}deg,{','.join(stops)})", ""
        return "", ""

    def background(self, slide, theme):
        for owner in (slide, slide.slide_layout, slide.slide_layout.slide_master):
            bg = child(owner.element, "p:cSld/p:bg")
            if bg is None:
                continue
            props = child(bg, "p:bgPr")
            if props is not None:
                return self.fill(props, owner.part, theme)
            ref = child(bg, "p:bgRef")
            fill = theme.style_fill(ref, background=True)
            if fill is not None:
                wrapper = etree.Element("fill")
                wrapper.append(fill)
                return self.fill(wrapper, owner.part, theme)
            if ref is not None:
                return theme.color(ref, "white"), ""
        return "white", ""

    def fallback(self, bbox, label):
        """Lazy exact PDF crop of a single unsupported object, never of a slide."""
        import pymupdf

        from deckdna.pptx.exporting.render import render_pdf

        if self._pdf is None:
            self._temporary = tempfile.TemporaryDirectory(prefix="deckdna-html-object-")
            pdf = render_pdf(self.path, Path(self._temporary.name) / "deck.pdf")
            self._pdf = pymupdf.open(pdf)
        x, y, w, h = bbox
        page = self._pdf[self.slide_index]
        rect = (
            pymupdf.Rect(
                x / self.width * page.rect.width,
                y / self.height * page.rect.height,
                (x + w) / self.width * page.rect.width,
                (y + h) / self.height * page.rect.height,
            )
            & page.rect
        )
        if rect.is_empty:
            return f"<span>{escape(label)}</span>"
        png = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=rect, alpha=True).tobytes("png")
        src = self.asset(png, "png")
        return (
            f'<img class="picture" src="{src}" alt="{escape(label, quote=True)}" '
            'data-fallback="object">'
        )

    def geometry(self, shape, props, theme, bbox):
        """Decorative shape geometry stays vector; text is rendered separately."""
        w, h = max(1, bbox[2]), max(1, bbox[3])
        color, image = self.fill(props, shape.part, theme)
        ref = child(shape.element, "p:style/a:fillRef")
        if not color and not image and ref is not None:
            fill = theme.style_fill(ref)
            wrapper = etree.Element("fill")
            if fill is not None:
                wrapper.append(fill)
                color, image = self.fill(wrapper, shape.part, theme)
        if image:
            return image
        line = child(props, "a:ln")
        stroke = theme.color(child(line, "a:solidFill"), "none")
        line_ref = child(shape.element, "p:style/a:lnRef")
        if line is None and line_ref is not None and line_ref.get("idx", "0") != "0":
            stroke = theme.color(line_ref, "none")
        if child(line, "a:noFill") is not None:
            stroke = "none"
        sw = float(line.get("w", 12700)) if line is not None else 12700
        preset = child(props, "a:prstGeom")
        kind = preset.get("prst", "") if preset is not None else ""
        paint = f'fill="{color or "none"}" stroke="{stroke}" stroke-width="{sw}"'
        if color.startswith("linear-gradient"):
            # Gradient CSS goes beneath geometry; text is still native DOM.
            return f'<div class="background" style="background:{color}"></div>'
        if kind == "ellipse":
            geom = f'<ellipse cx="{w / 2}" cy="{h / 2}" rx="{w / 2}" ry="{h / 2}" {paint}/>'
        elif kind == "line" or shape.shape_type == MSO_SHAPE_TYPE.LINE:
            geom = f'<path d="M0,0 L{w},{h}" {paint}/>'
        elif kind in ("triangle", "rtTriangle", "diamond"):
            points = {
                "triangle": f"{w / 2},0 {w},{h} 0,{h}",
                "rtTriangle": f"0,0 {w},{h} 0,{h}",
                "diamond": f"{w / 2},0 {w},{h / 2} {w / 2},{h} 0,{h / 2}",
            }
            geom = f'<polygon points="{points[kind]}" {paint}/>'
        else:
            paths = child(props, "a:custGeom/a:pathLst")
            geom = ""
            if paths is not None:
                for path in paths:
                    commands = []
                    for cmd in path:
                        tag = etree.QName(cmd).localname
                        pts = [(p.get("x", "0"), p.get("y", "0")) for p in cmd]
                        op = {"moveTo": "M", "lnTo": "L", "cubicBezTo": "C", "quadBezTo": "Q"}.get(
                            tag
                        )
                        if op and all(v.lstrip("-").isdigit() for pt in pts for v in pt):
                            commands.append(op + " ".join(",".join(pt) for pt in pts))
                        elif tag == "close":
                            commands.append("Z")
                    pw, ph = float(path.get("w", w)), float(path.get("h", h))
                    geom += (
                        f'<path d="{" ".join(commands)}" '
                        f'transform="scale({w / max(1, pw)},{h / max(1, ph)})" '
                        + (
                            paint
                            if path.get("fill") != "none"
                            else paint.replace(f'fill="{color or "none"}"', 'fill="none"')
                        )
                        + "/>"
                    )
            else:
                radius = min(w, h) * 0.15 if kind.startswith("round") else 0
                geom = f'<rect width="{w}" height="{h}" rx="{radius}" {paint}/>'
        return (
            f'<svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {w} {h}" preserveAspectRatio="none">{geom}</svg>'
        )

    def table(self, table, theme, text):
        out = ['<table class="table"><colgroup>']
        total = sum(c.width for c in table.columns)
        out.extend(f'<col style="width:{c.width / total * 100:.5f}%">' for c in table.columns)
        out.append("</colgroup><tbody>")
        rh = sum(r.height for r in table.rows)
        embedded_style = table_style(table)
        for row_index, row in enumerate(table.rows):
            out.append(f'<tr style="height:{row.height / rh * 100:.5f}%">')
            for col_index, cell in enumerate(row.cells):
                if cell.is_spanned:
                    continue
                props = cell._tc.tcPr
                color, image = self.fill(props, table._graphic_frame.part, theme)
                regions = cell_regions(embedded_style, table, row_index, col_index)
                if not color and not image:
                    for region in regions:
                        color, image = self.fill(
                            child(region, "a:tcStyle/a:fill"), table._graphic_frame.part, theme
                        )
                        if color or image:
                            break
                style = f"background:{color};" if color else ""
                anchor = attr([props], "anchor", "t")
                style += (
                    "vertical-align:" + {"ctr": "middle", "b": "bottom"}.get(anchor, "top") + ";"
                )
                for side, css_side in (
                    ("L", "left"),
                    ("R", "right"),
                    ("T", "top"),
                    ("B", "bottom"),
                ):
                    line = child(props, "a:ln" + side)
                    if line is None:
                        line = next(
                            (
                                node
                                for region in regions
                                if (node := child(region, f"a:tcStyle/a:tcBdr/a:{css_side}/a:ln"))
                                is not None
                            ),
                            None,
                        )
                    if line is not None:
                        stroke = theme.color(child(line, "a:solidFill"), "transparent")
                        style += (
                            f"border-{css_side}:{text.length(line.get('w', 12700))} solid {stroke};"
                        )
                # Cell margins belong to tcPr, not bodyPr. Apply them without mutation.
                pad = " ".join(
                    text.length(attr([props], k, v))
                    for k, v in (("marT", 45720), ("marR", 91440), ("marB", 45720), ("marL", 91440))
                )
                content = text.frame(cell.text_frame, extra_defaults=text_defaults(regions))
                content = content.replace(
                    'class="text-frame" style="',
                    f'class="text-frame" style="padding:{pad} !important;',
                )
                out.append(
                    f'<td rowspan="{cell.span_height}" colspan="{cell.span_width}" style="{style}">'
                    f'<div class="cell">{image}{content}</div></td>'
                )
            out.append("</tr>")
        return "".join(out) + "</tbody></table>"

    def shapes(self, shapes, slide, theme, text, transform=(0, 0, 1, 1), inherited=False):
        result = []
        ox, oy, sx, sy = transform
        for shape in shapes:
            if inherited and shape.is_placeholder:
                continue
            hidden = child(shape.element, ".//p:cNvPr")
            if hidden is not None and hidden.get("hidden") in ("1", "true"):
                continue
            if None in (shape.left, shape.top, shape.width, shape.height):
                continue
            x, y = ox + shape.left * sx, oy + shape.top * sy
            w, h = shape.width * sx, shape.height * sy
            if shape.has_table:
                # Office lays out tables by tblGrid/tr sizes. The enclosing
                # graphicFrame extent can be stale after editing or cloning.
                w = sum(c.width for c in shape.table.columns) * sx
                h = sum(r.height for r in shape.table.rows) * sy
            bbox = (x, y, w, h)
            style = (
                f"left:{x / self.width * 100:.5f}%;top:{y / self.height * 100:.5f}%;"
                f"width:{w / self.width * 100:.5f}%;height:{h / self.height * 100:.5f}%;"
            )
            xfrm = child(shape.element, "p:spPr/a:xfrm")
            rotate = shape.rotation
            transforms = []
            if rotate:
                transforms.append(f"rotate({rotate:g}deg)")
            if xfrm is not None:
                if xfrm.get("flipH") in ("1", "true"):
                    transforms.append("scaleX(-1)")
                if xfrm.get("flipV") in ("1", "true"):
                    transforms.append("scaleY(-1)")
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                xf = child(shape.element, "p:grpSpPr/a:xfrm")
                off, ext = child(xf, "a:chOff"), child(xf, "a:chExt")
                cx, cy = (
                    (float(off.get("x", 0)), float(off.get("y", 0))) if off is not None else (0, 0)
                )
                cw, ch = (
                    (float(ext.get("cx", w)), float(ext.get("cy", h)))
                    if ext is not None
                    else (w, h)
                )
                nsx, nsy = w / max(1, cw), h / max(1, ch)
                content = self.shapes(
                    shape.shapes, slide, theme, text, (x - cx * nsx, y - cy * nsy, nsx, nsy)
                )
                # Children are already in slide coordinates. Rotate around group center.
                if rotate:
                    center_x = (x + w / 2) / self.width * 100
                    center_y = (y + h / 2) / self.height * 100
                    content = (
                        f'<div style="position:absolute;inset:0;'
                        f"transform:rotate({rotate:g}deg);"
                        f'transform-origin:{center_x}% {center_y}%">{content}</div>'
                    )
                result.append(content)
                continue
            if transforms:
                style += f"transform:{' '.join(transforms)};"
            local_text = text if sy == 1 else TextRenderer(theme, self.height / sy)
            content = ""
            if shape.has_chart:
                content = chart_svg(shape.chart, theme, w, h)
                if content is None:
                    label = f"График {shape.name} (неподдерживаемый тип OOXML)"
                    content = self.fallback(bbox, label)
            elif shape.has_table:
                content = self.table(shape.table, theme, local_text)
            elif (
                shape.shape_type == MSO_SHAPE_TYPE.PICTURE
                or child(shape.element, "p:blipFill") is not None
            ):
                try:
                    src = self.image(child(shape.element, "p:blipFill"), shape.part)
                except (OSError, ValueError):
                    content = self.fallback(bbox, shape.name)
                else:
                    if src:
                        crop = [
                            getattr(shape, "crop_" + k, 0)
                            for k in ("left", "top", "right", "bottom")
                        ]
                        left, top, right, bottom = crop
                        iw, ih = max(0.001, 1 - left - right), max(0.001, 1 - top - bottom)
                        picstyle = (
                            f"left:{-left / iw * 100:.5f}%;"
                            f"top:{-top / ih * 100:.5f}%;"
                            f"width:{100 / iw:.5f}%;height:{100 / ih:.5f}%;"
                        )
                        alt = hidden.get("descr", shape.name) if hidden is not None else shape.name
                        content = (
                            f'<div class="picture-window"><img class="picture" '
                            f'src="{src}" alt="{escape(alt, quote=True)}" '
                            f'style="{picstyle}"></div>'
                        )
            else:
                props = child(shape.element, "p:spPr")
                content = self.geometry(shape, props, theme, bbox)
                if shape.has_text_frame:
                    content += local_text.frame(shape.text_frame, shape_chain(shape, slide), shape)
            result.append(
                f'<div class="object" data-shape-id="{shape.shape_id}" '
                f'style="{style}">{content}</div>'
            )
        return "".join(result)

    def document(self, title=None, slide_titles=None):
        heading = escape(title or self.path.stem)
        slides, links = [], []
        try:
            for i, slide in enumerate(self.prs.slides):
                self.slide_index = i
                theme = Theme(slide)
                text = TextRenderer(theme, self.height)
                bg, image = self.background(slide, theme)
                label = f"Слайд {i + 1}"
                if slide_titles and i < len(slide_titles) and slide_titles[i]:
                    label += " — " + slide_titles[i]
                body = image
                layout, master = slide.slide_layout, slide.slide_layout.slide_master
                if slide.element.get("showMasterSp", "1") not in ("0", "false"):
                    if layout.element.get("showMasterSp", "1") not in ("0", "false"):
                        body += self.shapes(master.shapes, slide, theme, text, inherited=True)
                    body += self.shapes(layout.shapes, slide, theme, text, inherited=True)
                body += self.shapes(slide.shapes, slide, theme, text)
                slides.append(
                    f'<section class="slide" id="slide-{i + 1}" '
                    f'aria-label="{escape(label, quote=True)}">'
                    f'<h2 class="slide-label">{escape(label)}</h2><div class="canvas" '
                    f'style="--slide-ratio:{self.width / self.height};'
                    f"aspect-ratio:{self.width}/{self.height};"
                    f'background:{bg or "white"}">{body}</div></section>'
                )
                links.append(f'<a href="#slide-{i + 1}">{i + 1}</a>')
        finally:
            if self._pdf is not None:
                self._pdf.close()
            if self._temporary is not None:
                self._temporary.cleanup()
        # Ship the same OFL fonts used by the PDF renderer if installed in the repo.
        from deckdna.pptx.exporting.render import _FONT_DIRS

        fonts = []
        for root in _FONT_DIRS:
            if root.is_dir():
                from PIL import ImageFont

                for path in sorted(root.rglob("*")):
                    if path.name == "OFL.txt":
                        license_data = path.read_bytes()
                        digest = hashlib.sha256(license_data).hexdigest()[:20]
                        self.assets[f"assets/font-license-{digest}.txt"] = license_data
                        continue
                    if path.suffix.lower() not in (".ttf", ".otf"):
                        continue
                    face, variant = ImageFont.truetype(str(path), 16).getname()
                    src = self.asset(path.read_bytes(), path.suffix)
                    weight = "700" if "bold" in variant.lower() else "400"
                    italic = "italic" if "italic" in variant.lower() else "normal"
                    fonts.append(
                        f'@font-face{{font-family:{css_font(face)};src:url("{src}");font-weight:{weight};font-style:{italic}}}'
                    )
                break
        print_page = f"@page{{size:{self.width / 12700:g}pt {self.height / 12700:g}pt;margin:0}}"
        return (
            '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{heading}</title><style>{CSS}{print_page}{''.join(fonts)}</style></head><body>"
            f'<header><h1>{heading}</h1></header><nav aria-label="Слайды">{" ".join(links)}</nav>'
            f"<main>{''.join(slides)}</main><script>{SCRIPT}</script></body></html>"
        )


def css_font(face):
    from deckdna.pptx.exporting.html_style import css_string

    return css_string(face)


def html_files(
    pptx_path: str | Path,
    *,
    title: str | None = None,
    slide_titles: Sequence[str | None] | None = None,
) -> dict[str, bytes]:
    deck = HtmlDeck(pptx_path)
    document = deck.document(title, slide_titles)
    return {"index.html": document.encode("utf-8"), **deck.assets}


def html_zip(
    pptx_path: str | Path,
    *,
    title: str | None = None,
    slide_titles: Sequence[str | None] | None = None,
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in html_files(pptx_path, title=title, slide_titles=slide_titles).items():
            archive.writestr(name, data)
    return buf.getvalue()
