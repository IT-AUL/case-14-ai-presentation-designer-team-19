"""Layout placeholder defaults are editor prompts, not generated slide content."""

import posixpath
from dataclasses import replace

from lxml import etree

from deckdna.pptx.composing.text_replace import _DONOR_CONTACT_RE, _TEMPLATE_CREDIT_RE
from deckdna.pptx.opc.package import (
    CONTENT_TYPES,
    CONTENT_TYPES_NS,
    OpcPackage,
    Relationship,
    rels_name_for,
    serialize_rels,
)

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def register_layout_clone(pkg: OpcPackage, layout: str) -> None:
    """Register both sides of the master/layout graph, not just slide → layout.

    LibreOffice discovers layouts via the master's registry. A layout with
    only an incoming slide relationship can lose theme resolution and inherit
    an unrelated layout's prompts, despite being readable by python-pptx.
    """
    master = next(
        (
            t
            for rel, t in pkg.internal_dependencies(layout)
            if rel.type_name == "slideMaster" and t in pkg.parts
        ),
        None,
    )
    if master is None:
        return  # broken input is handled by the package-integrity precheck
    root = etree.fromstring(pkg.parts[master])
    registry = root.find(f"{{{P}}}sldLayoutIdLst")
    if registry is None:
        registry = etree.Element(f"{{{P}}}sldLayoutIdLst")
        clr = root.find(f"{{{P}}}clrMap")
        root.insert(list(root).index(clr) + 1 if clr is not None else 1, registry)
    rels = pkg.rels(master)
    relation = next(
        (
            rel
            for rel, t in pkg.internal_dependencies(master)
            if rel.type_name == "slideLayout" and t == layout
        ),
        None,
    )
    if relation is None:
        numbers = [
            int(rel.id[3:]) for rel in rels if rel.id.startswith("rId") and rel.id[3:].isdigit()
        ]
        relation = Relationship(
            id=f"rId{max(numbers, default=0) + 1}",
            type=f"{R}/slideLayout",
            target=posixpath.relpath(layout, posixpath.dirname(master)),
        )
        rels.append(relation)
        pkg.parts[rels_name_for(master)] = serialize_rels(rels)
    if not any(entry.get(f"{{{R}}}id") == relation.id for entry in registry):
        ids = [int(entry.get("id")) for entry in registry if entry.get("id", "").isdigit()]
        etree.SubElement(
            registry,
            f"{{{P}}}sldLayoutId",
            {"id": str(max([2147483647, *ids]) + 1), f"{{{R}}}id": relation.id},
        )
        pkg.parts[master] = etree.tostring(root, encoding="UTF-8", xml_declaration=True)


def clear_layout_prompts(pkg: OpcPackage, protected_indices: set[int]) -> int:
    """Убрать только подсказки макетов, оставляя стиль и реальный текст.

    У сгенерированных слайдов — своя копия макета. Защищённые слайды и
    исходные макеты не меняются. Пустой ph сам по себе недостаточен:
    LibreOffice может рисовать defaults даже без hasCustomPrompt и сквозь
    пустой локальный ph. System fields и обычные брендовые shapes сохраняются.
    """
    clones: dict[str, str | None] = {}
    removed = 0
    ct = etree.fromstring(pkg.parts[CONTENT_TYPES])
    for part in sorted(list(pkg.parts)):
        if not part.startswith("ppt/slides/slide") or not part.endswith(".xml"):
            continue
        try:
            index = int(part.rsplit("slide", 1)[-1][:-4]) - 1
        except ValueError:
            continue
        if index in protected_indices:
            continue
        rels = pkg.rels(part)
        for rel, target in pkg.internal_dependencies(part):
            if rel.type_name != "slideLayout" or target not in pkg.parts:
                continue
            if target not in clones:
                root = etree.fromstring(pkg.parts[target])
                count = 0
                for shape in list(root.iter(f"{{{P}}}sp")):
                    ph = shape.find(f"{{{P}}}nvSpPr/{{{P}}}nvPr/{{{P}}}ph")
                    texts = list(shape.iter(f"{{{A}}}t"))
                    whole = "".join(text.text or "" for text in texts).strip()
                    if whole and (
                        _TEMPLATE_CREDIT_RE.search(whole) or _DONOR_CONTACT_RE.search(whole)
                    ):
                        if ph is None and shape.getparent() is not None:
                            shape.getparent().remove(shape)
                        else:
                            if ph is not None:
                                ph.attrib.pop("hasCustomPrompt", None)
                            for text in texts:
                                text.text = ""
                        count += 1
                        continue
                    if ph is None:
                        continue
                    if ph.get("type") in {"sldNum", "dt", "ftr", "hdr"}:
                        continue
                    if ph.get("hasCustomPrompt") not in {"1", "true"} and not any(
                        (text.text or "").strip() for text in texts
                    ):
                        continue
                    ph.attrib.pop("hasCustomPrompt", None)
                    for text in texts:
                        text.text = ""
                    count += 1
                clones[target] = None
                if count:
                    base = target[:-4] + "_no_prompt"
                    clone, suffix = base + ".xml", 1
                    while clone in pkg.parts:
                        clone = f"{base}{suffix}.xml"
                        suffix += 1
                    pkg.parts[clone] = etree.tostring(root, encoding="UTF-8", xml_declaration=True)
                    if rels_name_for(target) in pkg.parts:
                        pkg.parts[rels_name_for(clone)] = pkg.parts[rels_name_for(target)]
                    register_layout_clone(pkg, clone)
                    etree.SubElement(
                        ct,
                        f"{{{CONTENT_TYPES_NS}}}Override",
                        PartName=f"/{clone}",
                        ContentType="application/vnd.openxmlformats-officedocument."
                        "presentationml.slideLayout+xml",
                    )
                    clones[target] = clone
                    removed += count
            clone = clones[target]
            if clone:
                rels = [
                    replace(r, target=posixpath.relpath(clone, posixpath.dirname(part)))
                    if r.id == rel.id
                    else r
                    for r in rels
                ]
                pkg.parts[rels_name_for(part)] = serialize_rels(rels)
    if removed:
        pkg.parts[CONTENT_TYPES] = etree.tostring(ct, encoding="UTF-8", xml_declaration=True)
    return removed
