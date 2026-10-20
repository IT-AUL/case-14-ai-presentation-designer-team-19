"""Замена одного слайда готовой колоды (ADR-034 §7).

Новый слайд верстается отдельной мини-колодой из того же шаблона, затем
его часть переносится в колоду на место старого: XML слайда и его rels,
зависимые части (графики с книгами данных, картинки) — под новыми
свободными именами. Остальные части пакета не трогаются: слайды, их rels,
графики и картинки других слайдов остаются байт-в-байт. Заметки
докладчика старого слайда сохраняются (их rel переносится)."""

from __future__ import annotations

import io
import posixpath
import re
import zipfile

from lxml import etree

from deckdna.errors import DeckDNAError

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
_KEEP_TYPES = ("/slideLayout",)


def _rels_name(part: str) -> str:
    d, _, base = part.rpartition("/")
    return f"{d}/_rels/{base}.rels"


def _resolve(owner: str, target: str) -> str:
    return posixpath.normpath(posixpath.join(posixpath.dirname(owner), target))


def _relative(owner: str, target: str) -> str:
    return posixpath.relpath(target, posixpath.dirname(owner))


def slide_parts(files: dict[str, bytes]) -> list[str]:
    """Части слайдов в порядке показа."""
    pres = etree.fromstring(files["ppt/presentation.xml"])
    rels = etree.fromstring(files["ppt/_rels/presentation.xml.rels"])
    by_id = {r.get("Id"): r.get("Target") for r in rels}
    out = []
    for sld in pres.iterfind(f".//{{{P}}}sldId"):
        target = by_id.get(sld.get(f"{{{R}}}id"))
        if target:
            out.append(_resolve("ppt/presentation.xml", target))
    return out


def _free_name(files: dict[str, bytes], part: str) -> str:
    d, _, base = part.rpartition("/")
    stem, dot, ext = base.rpartition(".")
    stem = re.sub(r"\d+$", "", stem) or stem
    n = 1
    while True:
        name = f"{d}/{stem}{n}{dot}{ext}"
        if name not in files:
            return name
        n += 1


def _content_types(files: dict[str, bytes]) -> etree._Element:
    return etree.fromstring(files["[Content_Types].xml"])


def _ct_for(types: etree._Element, part: str) -> tuple[str, str] | None:
    """("override", type) | ("default", ext) — как задан тип части."""
    for el in types.iterfind(f"{{{CT}}}Override"):
        if el.get("PartName") == "/" + part:
            return ("override", el.get("ContentType"))
    ext = part.rsplit(".", 1)[-1].lower()
    for el in types.iterfind(f"{{{CT}}}Default"):
        if (el.get("Extension") or "").lower() == ext:
            return ("default", el.get("ContentType"))
    return None


def swap_slide(deck: bytes, mini: bytes, position: int, mini_position: int = 1) -> bytes:
    """Колода *deck* со слайдом №*position* (с 1), заменённым слайдом
    №*mini_position* из мини-колоды *mini* того же шаблона."""
    with zipfile.ZipFile(io.BytesIO(deck)) as z:
        files = {i.filename: z.read(i.filename) for i in z.infolist()}
        infos = {i.filename: i for i in z.infolist()}
        order = [i.filename for i in z.infolist()]
    with zipfile.ZipFile(io.BytesIO(mini)) as z:
        mfiles = {i.filename: z.read(i.filename) for i in z.infolist()}
    old_slides = slide_parts(files)
    new_slides = slide_parts(mfiles)
    if not 1 <= position <= len(old_slides) or not 1 <= mini_position <= len(new_slides):
        raise DeckDNAError("invalid_input", f"slide position out of range: {position}")
    target = old_slides[position - 1]
    source = new_slides[mini_position - 1]
    types = _content_types(files)
    mtypes = _content_types(mfiles)
    empty_rels = b"<Relationships xmlns='%s'/>" % PR.encode()
    old_rels = etree.fromstring(files.get(_rels_name(target), empty_rels))
    new_rels = etree.fromstring(mfiles.get(_rels_name(source), empty_rels))
    added: dict[str, bytes] = {}
    copied: dict[str, str] = {}  # часть мини-колоды → имя в колоде

    def copy_part(part: str) -> str:
        if part in copied:
            return copied[part]
        name = _free_name({**files, **added}, part)
        copied[part] = name
        data = mfiles[part]
        rels_name = _rels_name(part)
        if rels_name in mfiles:
            rels = etree.fromstring(mfiles[rels_name])
            for rel in rels:
                if rel.get("TargetMode") == "External":
                    continue
                dep = _resolve(part, rel.get("Target"))
                if dep in mfiles:
                    rel.set("Target", _relative(name, copy_part(dep)))
            added[_rels_name(name)] = etree.tostring(rels, xml_declaration=True,
                                                     encoding="UTF-8", standalone=True)
        added[name] = data
        kind = _ct_for(mtypes, part)
        if kind is not None:
            mode, ctype = kind
            if mode == "override" or _ct_for(types, name) is None:
                ov = etree.SubElement(types, f"{{{CT}}}Override")
                ov.set("PartName", "/" + name)
                ov.set("ContentType", ctype)
        return name

    out_rels = etree.Element(f"{{{PR}}}Relationships", nsmap={None: PR})
    for rel in new_rels:
        rtype = rel.get("Type") or ""
        if rtype.endswith("/notesSlide"):
            continue  # заметки — от старого слайда
        clone = etree.SubElement(out_rels, f"{{{PR}}}Relationship", dict(rel.attrib))
        if rel.get("TargetMode") == "External":
            continue
        dep = _resolve(source, rel.get("Target"))
        if rtype.endswith(_KEEP_TYPES):
            if dep not in files:
                raise DeckDNAError("composition_failed",
                                   f"layout {dep} of the new slide is not in the deck")
            clone.set("Target", _relative(target, dep))
            continue
        clone.set("Target", _relative(target, copy_part(dep)))
    used = {r.get("Id") for r in out_rels}
    for rel in old_rels:
        if (rel.get("Type") or "").endswith("/notesSlide"):
            rid = rel.get("Id")
            n = 1
            while rid in used:
                rid = f"rIdNotes{n}"
                n += 1
            etree.SubElement(out_rels, f"{{{PR}}}Relationship",
                             {**dict(rel.attrib), "Id": rid})
    files_out = dict(files)
    files_out[target] = mfiles[source]
    files_out[_rels_name(target)] = etree.tostring(
        out_rels, xml_declaration=True, encoding="UTF-8", standalone=True
    )
    files_out.update(added)
    files_out["[Content_Types].xml"] = etree.tostring(
        types, xml_declaration=True, encoding="UTF-8", standalone=True
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in [*order, *[n for n in files_out if n not in infos]]:
            if name in infos and files_out[name] is files.get(name):
                z.writestr(infos[name], files_out[name])
            else:
                z.writestr(name, files_out[name])
    return buf.getvalue()


__all__ = ["slide_parts", "swap_slide"]
