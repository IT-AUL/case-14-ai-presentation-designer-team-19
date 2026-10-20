"""Minimal OPC package model (PPTX_COMPILER.md §2, stage 0/8 groundwork).

A .pptx is a ZIP of parts; every part may carry a ``.rels`` part holding
typed relationships. This module is the authoritative owner of that graph:
part bytes, relationship CRUD/parse, target resolution, transitive
dependency walks, content-type coverage and save. Higher layers
(cloning/composing) never touch zipfile or rels XML directly.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from deckdna.errors import DeckDNAError

PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"

ROOT_RELS = "_rels/.rels"
CONTENT_TYPES = "[Content_Types].xml"
PRESENTATION = "ppt/presentation.xml"

CORE_PROPS_REL = f"{PKG_REL_NS}/metadata/core-properties"
CORE_PROPS_PART = "docProps/core.xml"
CORE_PROPS_CT = "application/vnd.openxmlformats-package.core-properties+xml"
CP_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC_NS = "http://purl.org/dc/elements/1.1/"
DCTERMS_NS = "http://purl.org/dc/terms/"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

_SLIDE_PART_RE = re.compile(r"^ppt/slides/slide\d+\.xml$")
_RELS_SUFFIX = ".rels"

_SLIDE_REL = f"{OFFICE_REL_NS}/slide"

# hardening check #20: zip-bomb guard. A real PPTX/POTX carries at most a
# few thousand parts and rarely exceeds ~50x its compressed size once
# unpacked (mostly-already-compressed media + verbose but not
# pathological XML) -- both limits generous for any legitimate deck,
# tight enough to reject a crafted archive designed to exhaust memory on
# extraction. Checked from ZipInfo metadata (central directory), which
# costs nothing to read -- no member is decompressed yet at this point.
_MAX_PARTS = 5_000
_MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
# A very high ratio on a tiny XML part is common and harmless. Apply the
# ratio guard only to sizeable members, where a crafted repetitive payload
# can otherwise expand dramatically before the total-size cap is reached.
_MAX_COMPRESSION_RATIO = 100.0
_COMPRESSION_RATIO_MIN_BYTES = 1 * 1024 * 1024


def rels_name_for(part_name: str) -> str:
    """Name of the .rels part belonging to *part_name* ('' → package root)."""
    if part_name == "":
        return ROOT_RELS
    directory, _, base = part_name.rpartition("/")
    return f"{directory}/_rels/{base}{_RELS_SUFFIX}"


def is_rels_part(part_name: str) -> bool:
    return part_name.endswith(_RELS_SUFFIX)


def resolve_target(source_part: str, target: str) -> str:
    """Resolve a relationship *target* against the part owning the rel.

    Targets are relative to the source part's directory; absolute targets
    start with '/'. External targets are never passed here.
    """
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    base = posixpath.dirname(source_part)
    return posixpath.normpath(posixpath.join(base, target))


@dataclass(frozen=True)
class Relationship:
    """One <Relationship> entry of a .rels part."""

    id: str
    type: str
    target: str
    target_mode: str | None = None  # "External" or absent

    @property
    def is_external(self) -> bool:
        return self.target_mode == "External"

    @property
    def type_name(self) -> str:
        """Local name of the relationship type, e.g. 'slideLayout'."""
        return self.type.rsplit("/", 1)[-1]

    @property
    def is_slide(self) -> bool:
        return self.type == _SLIDE_REL


def _is_well_formed_xml(data: bytes) -> bool:
    try:
        etree.fromstring(data)
    except etree.XMLSyntaxError:
        return False
    return True


def parse_rels(xml: bytes) -> list[Relationship]:
    root = etree.fromstring(xml)
    return [
        Relationship(
            id=rel.get("Id", ""),
            type=rel.get("Type", ""),
            target=rel.get("Target", ""),
            target_mode=rel.get("TargetMode"),
        )
        for rel in root
    ]


def serialize_rels(rels: list[Relationship]) -> bytes:
    root = etree.Element(f"{{{PKG_REL_NS}}}Relationships", nsmap={None: PKG_REL_NS})
    for rel in rels:
        el = etree.SubElement(root, f"{{{PKG_REL_NS}}}Relationship")
        el.set("Id", rel.id)
        el.set("Type", rel.type)
        el.set("Target", rel.target)
        if rel.target_mode:
            el.set("TargetMode", rel.target_mode)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


class OpcPackage:
    """In-memory view of an OPC package: part name → raw bytes."""

    def __init__(self, parts: dict[str, bytes]):
        self.parts = parts

    # -- open/save -----------------------------------------------------

    @classmethod
    def open(cls, path: str | Path) -> OpcPackage:
        path = Path(path)
        try:
            with zipfile.ZipFile(path) as zf:
                infos = zf.infolist()
                if len(infos) > _MAX_PARTS:
                    raise DeckDNAError(
                        code="package_corrupt",
                        message=(
                            f"{path.name} has {len(infos)} zip entries, "
                            f"exceeds the {_MAX_PARTS} part limit"
                        ),
                        stage="opc.open",
                    )
                total_uncompressed = sum(i.file_size for i in infos)
                if total_uncompressed > _MAX_UNCOMPRESSED_BYTES:
                    raise DeckDNAError(
                        code="package_corrupt",
                        message=(
                            f"{path.name} expands to {total_uncompressed} bytes, "
                            f"exceeds the {_MAX_UNCOMPRESSED_BYTES} byte limit"
                        ),
                        stage="opc.open",
                    )
                for info in infos:
                    if info.file_size < _COMPRESSION_RATIO_MIN_BYTES:
                        continue
                    ratio = (
                        float("inf")
                        if info.compress_size == 0
                        else info.file_size / info.compress_size
                    )
                    if ratio > _MAX_COMPRESSION_RATIO:
                        raise DeckDNAError(
                            code="package_corrupt",
                            message=(
                                f"{path.name} member {info.filename} has a "
                                f"compression ratio of {ratio:.1f}x, exceeds the "
                                f"{_MAX_COMPRESSION_RATIO:.1f}x limit"
                            ),
                            stage="opc.open",
                        )
                bad = zf.testzip()
                if bad is not None:
                    raise DeckDNAError(
                        code="package_corrupt",
                        message=f"corrupt zip member: {bad}",
                        stage="opc.open",
                    )
                parts = {name: zf.read(name) for name in zf.namelist()}
        except (zipfile.BadZipFile, zlib.error, EOFError) as exc:
            # zf.testzip()/zf.read() decompress each member: a corrupted
            # (not just truncated) deflate stream raises zlib.error mid-
            # decompress, and a truncated one can raise EOFError -- both
            # escaped as raw, un-typed exceptions before this caught only
            # zipfile.BadZipFile (itself only raised for a broken central
            # directory / local header, not a broken compressed payload).
            raise DeckDNAError(
                code="package_corrupt",
                message=f"{path.name} is not a readable zip/OPC package",
                stage="opc.open",
            ) from exc
        for required in (CONTENT_TYPES, PRESENTATION):
            if required not in parts:
                raise DeckDNAError(
                    code="package_corrupt",
                    message=f"missing required part {required}",
                    stage="opc.open",
                )
        unparseable = [
            name
            for name, data in parts.items()
            if (name.endswith(".xml") or is_rels_part(name))
            and not _is_well_formed_xml(data)
        ]
        if unparseable:
            shown = "; ".join(sorted(unparseable)[:5])
            if len(unparseable) > 5:
                shown += f"; …{len(unparseable) - 5} more"
            raise DeckDNAError(
                code="package_corrupt",
                message=f"unparseable XML part(s): {shown}",
                stage="opc.open",
            )
        return cls(parts)

    def save(self, path: str | Path) -> None:
        """Write parts back to a zip; content types and root rels first."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        ordered = [n for n in (CONTENT_TYPES, ROOT_RELS) if n in self.parts]
        ordered += sorted(n for n in self.parts if n not in ordered)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in ordered:
                zf.writestr(name, self.parts[name])

    # -- relationships --------------------------------------------------

    def rels(self, part_name: str) -> list[Relationship]:
        name = rels_name_for(part_name)
        if name not in self.parts:
            return []
        return parse_rels(self.parts[name])

    def ordered_slide_parts(self) -> list[str]:
        """Physical part identities in author-visible presentation order.

        python-pptx renames slide parts in memory when accessing ``slides``;
        those names cannot identify parts in this unchanged raw package.
        """
        root = etree.fromstring(self.parts[PRESENTATION])
        relationships = {rel.id: rel for rel in self.rels(PRESENTATION)}
        result: list[str] = []
        ns = "http://schemas.openxmlformats.org/presentationml/2006/main"
        for node in root.findall(f"{{{ns}}}sldIdLst/{{{ns}}}sldId"):
            relationship = relationships.get(node.get(f"{{{OFFICE_REL_NS}}}id"))
            if relationship is None or relationship.is_external or not relationship.is_slide:
                raise DeckDNAError(code="package_corrupt", message="invalid slide relationship",
                                   stage="opc.slides")
            part = resolve_target(PRESENTATION, relationship.target)
            if part not in self.parts:
                raise DeckDNAError(code="package_corrupt", message="missing slide part",
                                   stage="opc.slides")
            result.append(part)
        return result

    def internal_dependencies(self, part_name: str) -> list[tuple[Relationship, str]]:
        """(rel, resolved_part_name) for every internal rel of a part."""
        out = []
        for rel in self.rels(part_name):
            if rel.is_external:
                continue
            out.append((rel, resolve_target(part_name, rel.target)))
        return out

    def walk_dependencies(
        self,
        seeds: list[str],
        skip_rel_types: frozenset[str] = frozenset(),
    ) -> set[str]:
        """Transitive closure of part names reachable from *seeds*.

        Follows internal relationships only; rel types in *skip_rel_types*
        are pruned (e.g. notes/comments the caller drops). A dangling
        internal target aborts the build — silently producing a broken
        deck is worse than failing loudly.
        """
        seen: set[str] = set()
        stack = list(seeds)
        while stack:
            part = stack.pop()
            if part in seen:
                continue
            if part not in self.parts:
                raise DeckDNAError(
                    code="package_corrupt",
                    message=f"referenced part does not exist: {part}",
                    stage="opc.walk",
                )
            seen.add(part)
            for rel, target in self.internal_dependencies(part):
                if rel.type_name in skip_rel_types:
                    continue
                stack.append(target)
        return seen

    # -- content types --------------------------------------------------

    def content_type_coverage(self, part_names: set[str]) -> list[str]:
        """Parts (excluding rels) without a Default/Override content type."""
        root = etree.fromstring(self.parts[CONTENT_TYPES])
        defaults = {
            el.get("Extension", "").lower()
            for el in root
            if etree.QName(el).localname == "Default"
        }
        overrides = {
            el.get("PartName", "").lstrip("/")
            for el in root
            if etree.QName(el).localname == "Override"
        }
        missing = []
        for name in part_names:
            if is_rels_part(name) or name in overrides:
                continue
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext not in defaults:
                missing.append(name)
        return missing

    def check_rel_integrity(self, part_names: set[str] | None = None) -> list[str]:
        """Internal rel targets that resolve outside *part_names*.

        With part_names=None checks the whole package: every internal
        relationship must land on an existing part.
        """
        scope = part_names if part_names is not None else set(self.parts)
        dangling = []
        for name in scope:
            if is_rels_part(name):
                continue
            for rel in self.rels(name):
                if rel.is_external:
                    continue
                target = resolve_target(name, rel.target)
                if target not in scope or target not in self.parts:
                    dangling.append(f"{name} -> {rel.type_name} {target}")
        return dangling

    # -- core properties ----------------------------------------------

    def set_core_properties(
        self,
        *,
        title: str,
        subject: str | None = None,
        creator: str | None = None,
    ) -> None:
        """Перезаписать dc:title/dc:subject/dc:creator пакета.

        Метаданные шаблона (чужой заголовок, автор исходного файла) не
        должны протекать в собранную колоду — выходной пакет описывает
        свою тему. Часть/rel/override создаются, если шаблон их не нёс.
        """
        part = CORE_PROPS_PART
        for rel in self.rels(""):
            if rel.type == CORE_PROPS_REL:
                part = resolve_target("", rel.target)
                break
        root = None
        if part in self.parts:
            try:
                root = etree.fromstring(self.parts[part])
            except etree.XMLSyntaxError:
                root = None
        if root is None:
            root = etree.Element(
                f"{{{CP_NS}}}coreProperties",
                nsmap={
                    "cp": CP_NS,
                    "dc": DC_NS,
                    "dcterms": DCTERMS_NS,
                    "xsi": XSI_NS,
                },
            )
        values = {"title": title, "subject": subject, "creator": creator}
        for tag, value in values.items():
            if not value:
                continue
            el = root.find(f"{{{DC_NS}}}{tag}")
            if el is None:
                el = etree.SubElement(root, f"{{{DC_NS}}}{tag}")
            el.text = str(value)
        if creator:
            el = root.find(f"{{{CP_NS}}}lastModifiedBy")
            if el is None:
                el = etree.SubElement(root, f"{{{CP_NS}}}lastModifiedBy")
            el.text = str(creator)
        self.parts[part] = etree.tostring(
            root, xml_declaration=True, encoding="UTF-8", standalone=True
        )
        rels = self.rels("")
        if not any(rel.type == CORE_PROPS_REL for rel in rels):
            used = {rel.id for rel in rels}
            rid, n = "rIdCoreProps", 1
            while rid in used:
                n += 1
                rid = f"rIdCoreProps{n}"
            rels.append(
                Relationship(id=rid, type=CORE_PROPS_REL, target=part)
            )
            self.parts[ROOT_RELS] = serialize_rels(rels)
        ct_root = etree.fromstring(self.parts[CONTENT_TYPES])
        overrides = {
            el.get("PartName", "").lstrip("/")
            for el in ct_root
            if etree.QName(el).localname == "Override"
        }
        if part not in overrides:
            el = etree.SubElement(ct_root, f"{{{CONTENT_TYPES_NS}}}Override")
            el.set("PartName", f"/{part}")
            el.set("ContentType", CORE_PROPS_CT)
            self.parts[CONTENT_TYPES] = etree.tostring(
                ct_root,
                xml_declaration=True,
                encoding="UTF-8",
                standalone=True,
            )
