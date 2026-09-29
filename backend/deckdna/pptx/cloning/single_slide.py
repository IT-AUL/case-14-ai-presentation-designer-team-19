"""Clone slides + dependency graphs into a fresh multi-slide deck.

The riskiest slice of the Constraint Compiler (see docs/ARCHITECTURE.md) made
minimal: instead of mutating the template in place we emit a *new* package
containing only the transitive closure of the cloned slides — layouts,
masters, themes, media, charts, embedded fonts, presentation-level
plumbing — with `sldIdLst`, rels files and `[Content_Types].xml` rewritten
to match.

Cloned slides are emitted under canonical names `ppt/slides/slideN.xml`
in output order — not their source names — so the same exemplar can back
several output slides without part-name collisions.

Design rule (spec): notes/comments are dropped by default; unknown rel
types are preserved byte-for-byte together with their target parts.
"""

from __future__ import annotations

from lxml import etree

from deckdna.errors import DeckDNAError
from deckdna.pptx.opc.package import (
    CONTENT_TYPES,
    CONTENT_TYPES_NS,
    OFFICE_REL_NS,
    PRESENTATION,
    OpcPackage,
    Relationship,
    is_rels_part,
    rels_name_for,
    resolve_target,
    serialize_rels,
)

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R_ID = f"{{{OFFICE_REL_NS}}}id"
_SLIDE_REL = f"{OFFICE_REL_NS}/slide"

# Rel types dropped from cloned output per the compile contract (notes and
# comments of the source slide don't belong in a generated deck).
DROP_REL_TYPES = frozenset({"notesSlide", "comments", "commentAuthors"})

# presentation.xml → slide rels are handled explicitly via sldIdLst seeds;
# letting the dependency walk follow them would pull the whole source deck
# into the closure. Slides are only ever emitted via explicit seeds.
_NO_FOLLOW_REL_TYPES = frozenset({"slide"})


def _slide_content_type(pkg: OpcPackage) -> str:
    """Content type of an existing slide part — reused for emitted clones."""
    ct_root = etree.fromstring(pkg.parts[CONTENT_TYPES])
    for el in ct_root:
        if etree.QName(el).localname == "Override" and el.get(
            "PartName", ""
        ).lstrip("/").startswith("ppt/slides/"):
            return el.get("ContentType", "")
    return (
        "application/vnd.openxmlformats-officedocument"
        ".presentationml.slide+xml"
    )


def build_multi_slide_deck(
    pkg: OpcPackage,
    slides: list[tuple[str, bytes]],
    drop_rel_types: frozenset[str] = DROP_REL_TYPES,
) -> OpcPackage:
    """Emit a new package holding the cloned *slides* in order.

    *slides* is a list of ``(source_slide_part, slide_xml)`` pairs — the
    exemplar part to clone and the (already content-substituted) XML it
    should carry. Output slides are emitted as ``ppt/slides/slide1.xml``
    .. ``slideN.xml``; everything else they depend on is pulled in once
    through the shared dependency closure.
    """
    if not slides:
        raise DeckDNAError(
            code="invalid_input",
            message="no slides to emit",
            stage="cloning.build_deck",
        )
    for source_part, _ in slides:
        if source_part not in pkg.parts:
            raise DeckDNAError(
                code="invalid_input",
                message=f"slide part not in package: {source_part}",
                stage="cloning.build_deck",
            )

    # Seeds: the exemplar slides, every non-slide presentation dependency
    # (masters, notes master, theme, presProps/viewProps, embedded fonts)
    # and every package-level dependency (docProps, presentation itself).
    seeds = [src for src, _ in slides]
    for rel in pkg.rels(""):
        if not rel.is_external:
            seeds.append(resolve_target("", rel.target))
    pres_slide_targets: set[str] = set()
    for rel, target in pkg.internal_dependencies(PRESENTATION):
        if rel.is_slide:
            pres_slide_targets.add(target)
            continue
        seeds.append(target)
    unregistered = [src for src, _ in slides if src not in pres_slide_targets]
    if unregistered:
        raise DeckDNAError(
            code="composition_failed",
            message=f"{unregistered[0]} is not registered in presentation rels",
            stage="cloning.build_deck",
        )

    closure = pkg.walk_dependencies(
        seeds, skip_rel_types=drop_rel_types | _NO_FOLLOW_REL_TYPES
    )

    out_parts: dict[str, bytes] = {name: pkg.parts[name] for name in closure}
    source_slide_parts = {src for src, _ in slides}
    for src in source_slide_parts:
        out_parts.pop(src, None)  # re-emitted below under canonical names

    # Renamed slide parts and their .rels (owner dir stays ppt/slides, so
    # relative targets inside the rels file resolve identically).
    rename: dict[str, str] = {}  # output part name -> source part name
    out_slide_names: list[str] = []
    for i, (src, xml) in enumerate(slides, start=1):
        out_name = f"ppt/slides/slide{i}.xml"
        out_parts[out_name] = xml
        rename[out_name] = src
        out_slide_names.append(out_name)

    # presentation.xml: rebuild sldIdLst in output order with fresh ids,
    # and drop constructs that reference the old slide set (custom shows
    # by r:id, sections by sldId).
    pres = etree.fromstring(out_parts[PRESENTATION])
    sld_id_lst = pres.find(f".//{{{P}}}sldIdLst")
    if sld_id_lst is None:
        raise DeckDNAError(
            code="package_corrupt",
            message="presentation.xml has no sldIdLst",
            stage="cloning.build_deck",
        )
    for el in list(sld_id_lst):
        sld_id_lst.remove(el)
    for tag in ("custShowLst", "sectionLst"):
        for el in pres.findall(f".//{{{P}}}{tag}"):
            el.getparent().remove(el)
    for i, _name in enumerate(out_slide_names):
        el = etree.SubElement(sld_id_lst, f"{{{P}}}sldId")
        el.set("id", str(256 + i))
        el.set(R_ID, f"rIdNewSlide{i + 1}")  # placeholder, finalized below
    out_parts[PRESENTATION] = etree.tostring(
        pres, xml_declaration=True, encoding="UTF-8", standalone=True
    )

    # Re-emit every .rels part filtered to relationships whose target
    # survived in the output (external rels always pass through).
    for owner in ["", *sorted(n for n in out_parts if not is_rels_part(n))]:
        rels_name = rels_name_for(owner)
        source_owner = rename.get(owner, owner)
        original = pkg.rels(source_owner)
        if not original and rels_name not in pkg.parts:
            continue
        kept = [
            rel
            for rel in original
            if rel.is_external
            or resolve_target(source_owner, rel.target) in out_parts
        ]
        if owner == PRESENTATION:
            used = {rel.id for rel in kept}
            new_rids = []
            next_id = 1
            for _ in out_slide_names:
                while f"rId{next_id}" in used:
                    next_id += 1
                new_rids.append(f"rId{next_id}")
                used.add(f"rId{next_id}")
                next_id += 1
            kept.extend(
                Relationship(
                    id=rid,
                    type=_SLIDE_REL,
                    target=out_name.removeprefix("ppt/"),
                )
                for rid, out_name in zip(new_rids, out_slide_names, strict=True)
            )
            # Point the rebuilt sldIdLst at the allocated rIds.
            pres = etree.fromstring(out_parts[PRESENTATION])
            for el, rid in zip(
                pres.find(f".//{{{P}}}sldIdLst"), new_rids, strict=True
            ):
                el.set(R_ID, rid)
            out_parts[PRESENTATION] = etree.tostring(
                pres, xml_declaration=True, encoding="UTF-8", standalone=True
            )
        if kept or rels_name in pkg.parts or owner in rename:
            out_parts[rels_name] = serialize_rels(kept)

    # [Content_Types].xml: all Defaults stay; Overrides only for emitted
    # parts, plus explicit slide Overrides for the renamed clones.
    ct_root = etree.fromstring(pkg.parts[CONTENT_TYPES])
    for el in list(ct_root):
        if etree.QName(el).localname == "Override":
            if el.get("PartName", "").lstrip("/") not in out_parts:
                ct_root.remove(el)
    slide_ct = _slide_content_type(pkg)
    existing_overrides = {
        el.get("PartName", "").lstrip("/")
        for el in ct_root
        if etree.QName(el).localname == "Override"
    }
    for out_name in out_slide_names:
        if out_name in existing_overrides:
            continue
        el = etree.SubElement(ct_root, f"{{{CONTENT_TYPES_NS}}}Override")
        el.set("PartName", f"/{out_name}")
        el.set("ContentType", slide_ct)
    out_parts[CONTENT_TYPES] = etree.tostring(
        ct_root, xml_declaration=True, encoding="UTF-8", standalone=True
    )

    out = OpcPackage(out_parts)

    # Hard gates (package validity): full content-type coverage and
    # zero dangling relationships before anything is written to disk.
    missing_ct = out.content_type_coverage(
        {n for n in out_parts if n != CONTENT_TYPES}
    )
    if missing_ct:
        raise DeckDNAError(
            code="composition_failed",
            message=f"parts without content type: {missing_ct[:5]}",
            stage="cloning.build_deck",
        )
    dangling = out.check_rel_integrity()
    if dangling:
        raise DeckDNAError(
            code="composition_failed",
            message=f"dangling relationships: {dangling[:5]}",
            stage="cloning.build_deck",
        )
    return out


def build_single_slide_deck(
    pkg: OpcPackage,
    slide_part: str,
    slide_xml: bytes | None = None,
    drop_rel_types: frozenset[str] = DROP_REL_TYPES,
) -> OpcPackage:
    """Emit a new package holding *slide_part* and everything it needs."""
    return build_multi_slide_deck(
        pkg,
        [(slide_part, slide_xml if slide_xml is not None else pkg.parts[slide_part])],
        drop_rel_types=drop_rel_types,
    )
