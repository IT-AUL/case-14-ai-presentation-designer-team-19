"""Структурные правки готовой колоды (ADR-058): удалить, переставить или
вставить слайд, не трогая XML остальных слайдов.

Вставка создаёт пустой слайд на макете соседа; содержимое затем ставит
``swap_slide`` из мини-колоды того же шаблона."""

from __future__ import annotations

import io
import zipfile

from lxml import etree

from deckdna.errors import DeckDNAError
from deckdna.pptx.composing.slide_swap import (
    CT,
    PR,
    P,
    R,
    _content_types,
    _free_name,
    _relative,
    _rels_name,
    _resolve,
    slide_parts,
)

_PRES = "ppt/presentation.xml"
_PRES_RELS = "ppt/_rels/presentation.xml.rels"
_SLIDE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
_LAYOUT_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout"
_SLIDE_CT = "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_BLANK = (
    f'<p:sld xmlns:a="{_A}" xmlns:r="{R}" xmlns:p="{P}"><p:cSld><p:spTree>'
    '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
    '<p:grpSpPr/></p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'
)


def _xml(node: etree._Element) -> bytes:
    return etree.tostring(node, xml_declaration=True, encoding="UTF-8", standalone=True)


def _read(deck: bytes) -> tuple[dict[str, bytes], list[str]]:
    with zipfile.ZipFile(io.BytesIO(deck)) as z:
        return {i.filename: z.read(i.filename) for i in z.infolist()}, [
            i.filename for i in z.infolist()]


def _write(files: dict[str, bytes], order: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in [*[n for n in order if n in files], *[n for n in files if n not in order]]:
            z.writestr(name, files[name])
    return buf.getvalue()


def _sld_list(pres: etree._Element) -> etree._Element:
    lst = pres.find(f"{{{P}}}sldIdLst")
    if lst is None:
        raise DeckDNAError("package_corrupt", "presentation has no slide list")
    return lst


def _check(position: int, count: int) -> None:
    if not 1 <= position <= count:
        raise DeckDNAError("invalid_input", f"slide position out of range: {position}",
                           http_status=422)


def remove_slide(deck: bytes, position: int) -> bytes:
    """Колода без слайда №*position* (с 1) и его заметок."""
    files, order = _read(deck)
    pres = etree.fromstring(files[_PRES])
    rels = etree.fromstring(files[_PRES_RELS])
    entries = list(_sld_list(pres))
    _check(position, len(entries))
    if len(entries) <= 1:
        raise DeckDNAError("invalid_input", "the last slide cannot be removed", http_status=422)
    entry = entries[position - 1]
    rid = entry.get(f"{{{R}}}id")
    rel = next((r for r in rels if r.get("Id") == rid), None)
    if rel is None:
        raise DeckDNAError("package_corrupt", "slide relationship is missing")
    part = _resolve(_PRES, rel.get("Target"))
    doomed = {part, _rels_name(part)}
    if _rels_name(part) in files:
        for dep in etree.fromstring(files[_rels_name(part)]):
            if (dep.get("Type") or "").endswith("/notesSlide"):
                notes = _resolve(part, dep.get("Target"))
                doomed |= {notes, _rels_name(notes)}
    entry.getparent().remove(entry)
    rels.remove(rel)
    types = _content_types(files)
    for ov in list(types):
        if ov.get("PartName", "").lstrip("/") in doomed:
            types.remove(ov)
    out = {k: v for k, v in files.items() if k not in doomed}
    out[_PRES] = _xml(pres)
    out[_PRES_RELS] = _xml(rels)
    out["[Content_Types].xml"] = _xml(types)
    return _write(out, order)


def move_slide(deck: bytes, position: int, to: int) -> bytes:
    """Слайд №*position* переезжает на место №*to*; части не меняются."""
    files, order = _read(deck)
    pres = etree.fromstring(files[_PRES])
    lst = _sld_list(pres)
    entries = list(lst)
    _check(position, len(entries))
    _check(to, len(entries))
    entry = entries.pop(position - 1)
    entries.insert(to - 1, entry)
    for e in list(lst):
        lst.remove(e)
    for e in entries:
        lst.append(e)
    files[_PRES] = _xml(pres)
    return _write(files, order)


def insert_blank_slide(deck: bytes, position: int, *, layout_of: int) -> bytes:
    """Пустой слайд на месте №*position* (остальные сдвигаются) с макетом
    слайда №*layout_of* исходной колоды."""
    files, order = _read(deck)
    parts = slide_parts(files)
    _check(layout_of, len(parts))
    if not 1 <= position <= len(parts) + 1:
        raise DeckDNAError("invalid_input", f"slide position out of range: {position}",
                           http_status=422)
    source = parts[layout_of - 1]
    src_rels = etree.fromstring(files[_rels_name(source)])
    layout_rel = next((r for r in src_rels if r.get("Type") == _LAYOUT_REL), None)
    if layout_rel is None:
        raise DeckDNAError("package_corrupt", "slide has no layout")
    layout = _resolve(source, layout_rel.get("Target"))
    name = _free_name(files, "ppt/slides/slide1.xml")
    rels = etree.Element(f"{{{PR}}}Relationships", nsmap={None: PR})
    etree.SubElement(rels, f"{{{PR}}}Relationship", {
        "Id": "rId1", "Type": _LAYOUT_REL, "Target": _relative(name, layout)})
    files[name] = _BLANK.encode()
    files[_rels_name(name)] = _xml(rels)
    types = _content_types(files)
    ov = etree.SubElement(types, f"{{{CT}}}Override")
    ov.set("PartName", "/" + name)
    ov.set("ContentType", _SLIDE_CT)
    files["[Content_Types].xml"] = _xml(types)
    pres_rels = etree.fromstring(files[_PRES_RELS])
    used = {r.get("Id") for r in pres_rels}
    n = len(used) + 1
    while f"rId{n}" in used:
        n += 1
    rid = f"rId{n}"
    etree.SubElement(pres_rels, f"{{{PR}}}Relationship", {
        "Id": rid, "Type": _SLIDE_REL, "Target": _relative(_PRES, name)})
    files[_PRES_RELS] = _xml(pres_rels)
    pres = etree.fromstring(files[_PRES])
    lst = _sld_list(pres)
    ids = [int(e.get("id")) for e in lst]
    entry = etree.Element(f"{{{P}}}sldId")
    entry.set("id", str(max([255, *ids]) + 1))
    entry.set(f"{{{R}}}id", rid)
    lst.insert(position - 1, entry)
    files[_PRES] = _xml(pres)
    return _write(files, order)


__all__ = ["insert_blank_slide", "move_slide", "remove_slide"]
