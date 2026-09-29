"""Fill picture placeholders with real image bytes from ContentPack assets.

ContentPack.assets deliberately stores no bytes (metadata only — see the
content_parsers docstring), so this module re-reads them at fill time from
the user's original content file via ``Asset.artifact_id``:

- ``report.docx#word/media/image1.png`` → member of the OPC/zip package at
  *content_path* (the outer file name is informational — the package itself
  is what we have);
- ``https://…`` (Asset.source_url, or a markdown ``src`` that is a URL) →
  best-effort fetch with a short timeout, public hosts only (SSRF guard:
  private/loopback/link-local/reserved destinations and redirects are
  refused; responses are capped at 10 MiB);
- anything else → path confined to the content file's directory (markdown
  ``![alt](images/pic.png)``). Parent traversal, absolute paths outside
  that directory and escaping symlinks are dropped.

Filling: a picture placeholder is a ``p:pic`` (or any shape) whose
``a:blip/@r:embed`` points at an image relationship of the slide. We walk
``a:blip`` embeds in document order, resolve each rel to its media part,
and replace the part's raw bytes. Two output slides cloned from the
same image-bearing exemplar share one physical media part (the
dependency closure is a set); ``unshare_image_parts`` duplicates the
part for the later slide so each carries its own bytes — media is a
leaf part (no .rels of its own), so duplication is just the binary
plus a content-type Override when the source declared one.

Format is detected by magic bytes, not file extension. When the detected
type differs from the part's declared content type (e.g. JPEG bytes into
an ``image1.png`` part), an explicit ``Override`` entry is added to
``[Content_Types].xml`` — the honest OOXML way, not a silent mismatch.

Honest bounds: bytes that can't be re-read (file moved, fetch failed,
member absent) drop the unit — counted, never fatal.
"""

from __future__ import annotations

import ipaddress
import posixpath
import re
import socket
import urllib.request
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import urlparse

from lxml import etree

from deckdna.contracts.content_pack import Asset, Kind1
from deckdna.pptx.composing.table_fill import _empty_body_hosts, _next_shape_id
from deckdna.pptx.opc.package import (
    CONTENT_TYPES,
    CONTENT_TYPES_NS,
    Relationship,
    parse_rels,
    rels_name_for,
    resolve_target,
    serialize_rels,
)

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_IMAGE_REL = f"{R_NS}/image"

_EXT_BY_TYPE = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/gif": "gif",
    "image/bmp": "bmp",
    "image/tiff": "tiff",
    "image/webp": "webp",
}
_MEDIA_RE = re.compile(r"ppt/media/image(\d+)\.")
_EMPTY_RELS = (
    b"<Relationships xmlns='http://schemas.openxmlformats.org/"
    b"package/2006/relationships'/>"
)
_MAX_REMOTE_IMAGE_BYTES = 10 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a provider-controlled Location into an internal URL."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _fetch_public_image(url: str) -> bytes | None:
    opener = urllib.request.build_opener(_NoRedirect())
    with opener.open(url, timeout=5) as resp:
        content_length = resp.headers.get("Content-Length")
        if content_length is not None and int(content_length) > _MAX_REMOTE_IMAGE_BYTES:
            return None
        data = resp.read(_MAX_REMOTE_IMAGE_BYTES + 1)
        return data if len(data) <= _MAX_REMOTE_IMAGE_BYTES else None

_SIGNATURES: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
]


def detect_image_content_type(data: bytes) -> str | None:
    """Raster format by magic bytes; None when unrecognized."""
    for sig, ct in _SIGNATURES:
        if data.startswith(sig):
            return ct
    if len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


@dataclass
class ResolvedImage:
    """An Asset whose bytes were re-read from the content source."""

    asset: Asset
    data: bytes


@dataclass
class ImageFillReport:
    images_found: int = 0  # blip-embed parts on the slide
    images_filled: int = 0
    images_created: int = 0  # new p:pic figures built from scratch
    units_dropped: int = 0
    format_unrecognized: int = 0
    content_type_overrides: int = 0
    asset_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "images_found": self.images_found,
            "images_filled": self.images_filled,
            "images_created": self.images_created,
            "units_dropped": self.units_dropped,
            "format_unrecognized": self.format_unrecognized,
            "content_type_overrides": self.content_type_overrides,
            "asset_ids": self.asset_ids,
        }


def _allowed_remote_host(host: str) -> bool:
    """SSRF guard: *host* must resolve to public IPs only.

    Rejects literals and any DNS answer in private/loopback/link-local/
    multicast/reserved ranges (incl. cloud metadata 169.254.169.254).
    """
    try:
        ips = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            ips = [
                ipaddress.ip_address(info[4][0])
                for info in socket.getaddrinfo(host, None)
            ]
        except (OSError, ValueError):
            return False
    return bool(ips) and not any(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        for ip in ips
    )


def resolve_asset_bytes(asset: Asset, content_path: Path | None) -> bytes | None:
    """Re-read the asset's bytes from the user's content source.

    Returns None when bytes are unreachable — a counted drop, never
    an exception.
    """
    if content_path is None:
        return None
    content_path = Path(content_path)
    ref = asset.artifact_id
    url = asset.source_url or (
        ref if ref.startswith(("http://", "https://")) else None
    )
    try:
        if url:
            parsed = urlparse(url)
            host = parsed.hostname
            if (
                parsed.scheme not in ("http", "https")
                or not host
                or not _allowed_remote_host(host)
            ):
                return None
            return _fetch_public_image(url)
        if "#" in ref:
            # OPC-package member: "doc.docx#word/media/image1.png" — the
            # member lives inside *content_path* itself, whatever the
            # recorded outer name says.
            inner = ref.split("#", 1)[1].lstrip("/")
            with zipfile.ZipFile(content_path) as zf:
                return zf.read(inner)
        root = content_path.parent.resolve()
        path = Path(ref)
        if not path.is_absolute():
            path = root / path
        path = path.resolve()
        if not path.is_relative_to(root):
            return None
        if path.is_file():
            return path.read_bytes()
    except (OSError, RuntimeError, ValueError, KeyError, zipfile.BadZipFile):
        return None
    return None


def _match_asset(ref: str, assets: list[Asset]) -> Asset | None:
    """Explicit ref match against every identifying field of an asset.

    (markdown units carry the alt text in asset_ref, docx-derived packs
    may carry the full artifact ref, JSON packs an asset id.)
    """
    for a in assets:
        if ref in (a.id, a.artifact_id, a.source_url, a.alt):
            return a
    return None


def resolve_slide_images(
    asset_refs: Iterable[str | None],
    assets: list[Asset],
    content_path: Path | None,
    used_asset_ids: set[str],
    generated: dict[str, bytes] | None = None,
) -> tuple[list[ResolvedImage], int]:
    """Map the slide's asset_refs to assets and re-read their bytes.

    Ref resolution: explicit match on id/artifact_id/source_url/alt;
    ``None`` refs take the next not-yet-used asset in document order
    (parsers emit assets in image order, matching image_ref blocks).
    A ref that identifies no asset, or an asset with unreachable bytes,
    counts as dropped. *used_asset_ids* spans the whole deck so the
    sequential pool never re-hands the same asset.

    *generated* (ADR-017) — ``asset_ref -> already-in-memory bytes`` for
    model-generated images: checked only when the ref matches no real
    ContentPack asset (never shadows a user-supplied image), so it never
    goes through :func:`resolve_asset_bytes` — there is no content_path
    member or URL to re-read, the bytes already exist. A synthetic
    ``Asset`` is built on the fly so :class:`ResolvedImage`'s contract
    (asset + data) stays uniform for both sources.
    """
    assets = list(assets)
    images: list[ResolvedImage] = []
    dropped = 0
    pool = iter(assets)
    generated = generated or {}

    def next_unused() -> Asset | None:
        for candidate in pool:
            if candidate.id not in used_asset_ids:
                return candidate
        return None

    for ref in asset_refs:
        asset = _match_asset(ref, assets) if ref else next_unused()
        if asset is None:
            if ref is not None and ref in generated:
                images.append(
                    ResolvedImage(
                        asset=Asset(id=ref, kind=Kind1.image, artifact_id=ref),
                        data=generated[ref],
                    )
                )
                continue
            dropped += 1
            continue
        used_asset_ids.add(asset.id)
        data = resolve_asset_bytes(asset, content_path)
        if data is None:
            dropped += 1
            continue
        images.append(ResolvedImage(asset=asset, data=data))
    return images, dropped


def _blip_part_order(
    slide_xml: bytes,
    slide_part: str,
    slide_rels,
) -> list[str]:
    """Media part names behind a:blip/@r:embed, in document order.

    Only parts actually referenced by a blip count — image rels that
    exist but aren't embedded (e.g. leftovers) are left alone, and a
    part embedded twice on the slide is filled once.
    """
    root = etree.fromstring(slide_xml)
    rid_to_part = {
        rel.id: resolve_target(slide_part, rel.target)
        for rel in slide_rels
        if rel.type == _IMAGE_REL and not rel.is_external
    }
    parts: list[str] = []
    for blip in root.iter(f"{{{A}}}blip"):
        part = rid_to_part.get(blip.get(f"{{{R_NS}}}embed"))
        if part is not None and part not in parts:
            parts.append(part)
    return parts


def _register_media_content_type(
    pkg_parts: dict[str, bytes], part: str, detected: str
) -> None:
    """Make *part*'s detected type valid in [Content_Types].xml:
    a `Default` for the extension when absent, an `Override` when the
    existing Default maps the extension to a different type."""
    ct_xml = pkg_parts[CONTENT_TYPES]
    defaults, _overrides = _content_type_maps(ct_xml)
    ext = part.rsplit(".", 1)[-1].lower()
    declared = defaults.get(ext)
    if declared == detected:
        return
    root = etree.fromstring(ct_xml)
    if declared is None:
        el = etree.SubElement(root, f"{{{CONTENT_TYPES_NS}}}Default")
        el.set("Extension", ext)
        el.set("ContentType", detected)
    else:
        el = etree.SubElement(root, f"{{{CONTENT_TYPES_NS}}}Override")
        el.set("PartName", f"/{part}")
        el.set("ContentType", detected)
    pkg_parts[CONTENT_TYPES] = etree.tostring(
        root, xml_declaration=True, encoding="UTF-8"
    )


def _next_media_part(pkg_parts: dict[str, bytes], ext: str) -> str:
    idx = max(
        (
            int(m.group(1))
            for name in pkg_parts
            for m in [_MEDIA_RE.match(name)]
            if m
        ),
        default=0,
    )
    return f"ppt/media/image{idx + 1}.{ext}"


def _next_rid(slide_rels) -> str:
    idx = max(
        (
            int(rel.id[3:])
            for rel in slide_rels
            if rel.id.startswith("rId") and rel.id[3:].isdigit()
        ),
        default=0,
    )
    return f"rId{idx + 1}"


def _build_pic(
    shape_id: int, rid: str, x: int, y: int, cx: int, cy: int
) -> etree._Element:
    """A standalone `p:pic` — same XML python-pptx `add_picture` emits."""
    xml = (
        f'<p:pic xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R_NS}">'
        f'<p:nvPicPr><p:cNvPr id="{shape_id}" name="Picture {shape_id}"/>'
        f'<p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr>'
        f"<p:nvPr/></p:nvPicPr>"
        f'<p:blipFill><a:blip r:embed="{rid}"/>'
        f"<a:stretch><a:fillRect/></a:stretch></p:blipFill>"
        f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/>'
        f'<a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
        f"</p:pic>"
    )
    return etree.fromstring(xml)


def _add_slide_picture(
    pkg_parts: dict[str, bytes],
    slide_part: str,
    image: ResolvedImage,
    slide_rels,
    used_hosts: set[tuple[int, int, int, int]],
) -> bool:
    """Create a new `p:pic` for *image* from scratch — the add_table
    analogue: largest emptied text body as host (same `used_hosts` key
    convention), new media part + rels entry + pic XML. False when no
    host, no rels part, or the raster format is untypable."""
    detected = detect_image_content_type(image.data)
    ext = _EXT_BY_TYPE.get(detected or "")
    if ext is None:
        return False  # нечем честно типизировать новый media-парт
    rels_part = rels_name_for(slide_part)
    if rels_part not in pkg_parts:
        return False
    root = etree.fromstring(pkg_parts[slide_part])
    sp_tree = root.find(f"{{{P}}}cSld/{{{P}}}spTree")
    if sp_tree is None:
        return False
    host = next(
        (h for h in _empty_body_hosts(root) if h[1:5] not in used_hosts),
        None,
    )
    if host is None:
        return False
    _, x, y, cx, cy, _sp = host
    used_hosts.add(host[1:5])
    part = _next_media_part(pkg_parts, ext)
    rid = _next_rid(slide_rels)
    pkg_parts[part] = image.data
    slide_rels.append(
        Relationship(id=rid, type=_IMAGE_REL, target=f"../media/{part.split('/')[-1]}")
    )
    pkg_parts[rels_part] = serialize_rels(slide_rels)
    _register_media_content_type(pkg_parts, part, detected or "image/png")
    sp_tree.append(_build_pic(_next_shape_id(root), rid, x, y, cx, cy))
    pkg_parts[slide_part] = etree.tostring(
        root, xml_declaration=True, standalone=True
    )
    return True


def _content_type_maps(ct_xml: bytes) -> tuple[dict, dict]:
    root = etree.fromstring(ct_xml)
    defaults, overrides = {}, {}
    for el in root:
        local = etree.QName(el).localname
        if local == "Default":
            defaults[el.get("Extension", "").lower()] = el.get("ContentType", "")
        elif local == "Override":
            overrides[el.get("PartName", "").lstrip("/")] = el.get("ContentType", "")
    return defaults, overrides


def fill_slide_images(
    pkg_parts: dict[str, bytes],
    slide_part: str,
    images: list[ResolvedImage],
    slide_rels,
    used_parts: set[str],
    used_hosts: set[tuple[int, int, int, int]] | None = None,
) -> ImageFillReport:
    """Replace the media-part bytes behind *slide_part*'s blip embeds.

    Mutates *pkg_parts* (and ``[Content_Types].xml`` when the detected
    format differs from the declared one). *used_parts* spans the whole
    deck: a media part shared by several output slides can't carry two
    different images — the second unit drops.

    Units beyond the slide's blip embeds create a new `p:pic` in the
    largest emptied text body (`_add_slide_picture`, the add_table
    analogue) when *used_hosts* is provided — the same set table/diagram
    creation consumes, so all three never fight over one host slot.
    """
    report = ImageFillReport()
    if not images:
        return report
    slide_rels = list(slide_rels)
    embed_parts = _blip_part_order(pkg_parts[slide_part], slide_part, slide_rels)
    report.images_found = len(embed_parts)
    part_iter = iter(embed_parts)
    for image in images:
        part = next(
            (p for p in part_iter if p not in used_parts and p in pkg_parts),
            None,
        )
        if part is None:
            if used_hosts is not None and _add_slide_picture(
                pkg_parts, slide_part, image, slide_rels, used_hosts
            ):
                report.images_filled += 1
                report.images_created += 1
                report.asset_ids.append(image.asset.id)
            else:
                report.units_dropped += 1
            continue
        detected = detect_image_content_type(image.data)
        if detected is None:
            report.format_unrecognized += 1
        pkg_parts[part] = image.data
        used_parts.add(part)
        report.images_filled += 1
        report.asset_ids.append(image.asset.id)
        if detected is not None:
            ct_xml = pkg_parts[CONTENT_TYPES]
            defaults, overrides = _content_type_maps(ct_xml)
            declared = overrides.get(part) or defaults.get(
                part.rsplit(".", 1)[-1].lower()
            )
            if declared and declared != detected:
                root = etree.fromstring(ct_xml)
                el = etree.SubElement(root, f"{{{CONTENT_TYPES_NS}}}Override")
                el.set("PartName", f"/{part}")
                el.set("ContentType", detected)
                pkg_parts[CONTENT_TYPES] = etree.tostring(
                    root, xml_declaration=True, encoding="UTF-8"
                )
                report.content_type_overrides += 1
    return report


# --- shared media-part unshare (unshare_chart_parts analogue) ------


def _relativize(owner_part: str, target_part: str) -> str:
    """Target string for a rel owned by *owner_part* — `../media/x.png`
    style, matching what slide .rels already use."""
    return posixpath.relpath(target_part, posixpath.dirname(owner_part))


def _content_type_of(parts: dict[str, bytes], part_name: str) -> str | None:
    """Per-part Override content type, when the package declares one."""
    _defaults, overrides = _content_type_maps(parts[CONTENT_TYPES])
    return overrides.get(part_name)


def _add_override(
    parts: dict[str, bytes], part_name: str, content_type: str
) -> None:
    root = etree.fromstring(parts[CONTENT_TYPES])
    for el in root:
        if (
            etree.QName(el).localname == "Override"
            and el.get("PartName") == f"/{part_name}"
        ):
            return  # already declared
    el = etree.SubElement(root, f"{{{CONTENT_TYPES_NS}}}Override")
    el.set("PartName", f"/{part_name}")
    el.set("ContentType", content_type)
    parts[CONTENT_TYPES] = etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", standalone=True
    )


def _unique_part_name(
    parts: dict[str, bytes], part_name: str, tag: str
) -> str:
    """*part_name* with `_<tag>` before the extension, bumped until free."""
    stem, dot, ext = part_name.rpartition(".")
    candidate = f"{stem}_{tag}{dot}{ext}"
    n = 2
    while candidate in parts:
        candidate = f"{stem}_{tag}_{n}{dot}{ext}"
        n += 1
    return candidate


def _duplicate_media_part(
    out_parts: dict[str, bytes],
    src_parts: dict[str, bytes],
    media_part: str,
    tag: str,
) -> str | None:
    """Copy *media_part* under a fresh name for another output slide.

    Media is a leaf part — no .rels to relink, just the binary. Bytes
    come from *src_parts* (the unmutated template package) so the copy
    is pristine even after sibling slides already filled their own;
    an Override is copied from the SOURCE [Content_Types].xml — the
    output's Override on the shared part may already describe mutated
    bytes and must not mis-type the pristine duplicate. Returns the
    new part name, or None when the source bytes are missing.
    """
    raw = src_parts.get(media_part)
    if raw is None:
        return None
    new_part = _unique_part_name(out_parts, media_part, tag)
    out_parts[new_part] = raw
    ct = _content_type_of(src_parts, media_part)
    if ct:
        _add_override(out_parts, new_part, ct)
    return new_part


def unshare_image_parts(
    out_parts: dict[str, bytes],
    src_parts: dict[str, bytes],
    slide_part: str,
    used_parts: set[str],
    tag: str,
) -> None:
    """Repoint *slide_part*'s image rels at fresh duplicates of media
    parts already claimed by an earlier output slide.

    Two output slides cloned from the same image-bearing exemplar
    share one physical part — the dependency closure is a set. Call
    before :func:`fill_slide_images` for each output slide; parts not
    yet in *used_parts* are left alone (the slide keeps the first
    claim), missing source bytes keep the shared part (the unit drops
    downstream, as before).
    """
    rels_name = rels_name_for(slide_part)
    rels = parse_rels(out_parts.get(rels_name, _EMPTY_RELS))
    changed = False
    new_rels = []
    dup_i = 0
    for rel in rels:
        if rel.type != _IMAGE_REL or rel.is_external:
            new_rels.append(rel)
            continue
        target = resolve_target(slide_part, rel.target)
        if target not in used_parts:
            new_rels.append(rel)
            continue
        dup_i += 1
        dup = _duplicate_media_part(
            out_parts, src_parts, target, f"{tag}_{dup_i}"
        )
        if dup is None:
            new_rels.append(rel)
            continue
        new_rels.append(replace(rel, target=_relativize(slide_part, dup)))
        changed = True
    if changed:
        out_parts[rels_name] = serialize_rels(new_rels)
