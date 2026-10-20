"""Библиотека иконок шаблона: извлечь, подписать моделью со зрением.

Шаблоны несут готовую графику в своём стиле — паки иконок у маркетплейсов
(Slidesgo: сотни векторных значков на служебных слайдах), значки и маркеры
в карточках у корпоративных шаблонов. Это материал для колоды, а не мусор:
иконка из самого шаблона выглядит «родной», в отличие от сгенерированной.

Правило отбора общее, без знания шаблона. Иконка — фигура верхнего уровня
``spTree``:

* векторная: группа контуров (``grpSp``) или одиночный контур
  (``sp`` с ``custGeom``) — переносится на любой слайд копированием XML;
* без текста и без ссылок на части пакета (``r:embed``/``r:link``/``r:id``)
  — иначе копия потянула бы за собой картинки и связи;
* почти квадратная (0.6–1.7) и небольшая: сторона 1.5–12% ширины слайда.

Подписи: иконки рендерятся (LibreOffice → PDF → PNG), вырезаются по рамкам,
складываются в пронумерованную сетку, и модель со зрением (промпт
``icon_labels``) одним вызовом на сетку пишет 1–3 слова «что изображено»
или отмечает «не иконка». Результат кешируется по хешу шаблона.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from lxml import etree
from pydantic import BaseModel, Field

from deckdna.pptx.opc.package import OpcPackage

logger = logging.getLogger(__name__)

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PROMPT_NAME = "icon_labels"
_MIN_SIDE, _MAX_SIDE = 0.015, 0.12  # доля ширины слайда
_MIN_ASPECT, _MAX_ASPECT = 0.6, 1.7
_MAX_ICONS = 480
_GRID = 6  # иконок в ряду сетки для модели
_CELL_PX = 128
_BATCH = _GRID * _GRID
_MAX_CONCURRENT = 4


@dataclass
class IconAsset:
    id: str
    part: str
    slide_index: int
    box: tuple[int, int, int, int]
    xml: str
    label: str | None = None
    # растровая иконка (p:pic): байты картинки и её расширение — при вставке
    # кладутся в пакет колоды отдельной частью с новой связью
    media_b64: str | None = None
    media_ext: str | None = None


class IconLabel(BaseModel):
    i: int
    label: str = ""
    is_icon: bool = True


class IconLabelBatch(BaseModel):
    icons: list[IconLabel] = Field(default_factory=list)


def _box(el: etree._Element) -> tuple[int, int, int, int] | None:
    tag = etree.QName(el).localname
    xfrm = (
        el.find(f"{{{P}}}grpSpPr/{{{A}}}xfrm") if tag == "grpSp"
        else el.find(f"{{{P}}}spPr/{{{A}}}xfrm")
    )  # p:pic тоже держит рамку в p:spPr
    if xfrm is None:
        return None
    off, ext = xfrm.find(f"{{{A}}}off"), xfrm.find(f"{{{A}}}ext")
    if off is None or ext is None:
        return None
    try:
        return int(off.get("x")), int(off.get("y")), int(ext.get("cx")), int(ext.get("cy"))
    except (TypeError, ValueError):
        return None


def _portable(el: etree._Element) -> bool:
    if any((t.text or "").strip() for t in el.iter(f"{{{A}}}t")):
        return False
    for node in el.iter():
        if any(k.startswith(f"{{{R}}}") for k in node.attrib):
            return False
    return True


def _pic_media(pkg: OpcPackage, part: str, el: etree._Element) -> tuple[str, str] | None:
    """(base64 байтов, расширение) картинки p:pic, если она внутри пакета."""
    blip = el.find(f"{{{P}}}blipFill/{{{A}}}blip")
    rid = blip.get(f"{{{R}}}embed") if blip is not None else None
    if not rid or any(True for _ in el.iter(f"{{{A}}}t")):
        return None
    for rel, target in pkg.internal_dependencies(part):
        if rel.id == rid and target in pkg.parts:
            ext = target.rsplit(".", 1)[-1].lower()
            if ext not in ("png", "jpg", "jpeg", "gif", "svg", "emf", "wmf"):
                return None
            return base64.b64encode(pkg.parts[target]).decode(), ext
    return None


def _is_vector_icon(el: etree._Element) -> bool:
    tag = etree.QName(el).localname
    if tag == "grpSp":
        paths = sum(1 for _ in el.iter(f"{{{A}}}custGeom"))
        return paths >= 1 and not any(True for _ in el.iter(f"{{{P}}}pic"))
    if tag == "sp":
        return el.find(f"{{{P}}}spPr/{{{A}}}custGeom") is not None
    return False


def _canon(el: etree._Element) -> str:
    """XML без id/имён и сдвига — для дедупликации одинаковых иконок."""
    xml = etree.tostring(el).decode()
    xml = re.sub(r'\s(id|name)="[^"]*"', "", xml)
    return re.sub(r'<a:off x="-?\d+" y="-?\d+"/>', "", xml, count=1)


def extract_icons(pkg: OpcPackage, parts: list[str], slide_w: int) -> list[IconAsset]:
    icons: list[IconAsset] = []
    seen: set[str] = set()
    for index, part in enumerate(parts):
        xml = pkg.parts.get(part)
        if xml is None:
            continue
        tree = etree.fromstring(xml).find(f"{{{P}}}cSld/{{{P}}}spTree")
        if tree is None:
            continue
        for el in tree:
            if etree.QName(el).localname not in ("grpSp", "sp", "pic"):
                continue
            box = _box(el)
            if box is None or not box[3]:
                continue
            w, h = box[2] / slide_w, box[3] / slide_w
            if not (_MIN_SIDE <= w <= _MAX_SIDE and _MIN_SIDE <= h <= _MAX_SIDE):
                continue
            if not _MIN_ASPECT <= box[2] / box[3] <= _MAX_ASPECT:
                continue
            media = None
            if etree.QName(el).localname == "pic":
                media = _pic_media(pkg, part, el)
                if media is None:
                    continue
                key = hashlib.sha256(media[0].encode()).hexdigest()
            else:
                if not _is_vector_icon(el) or not _portable(el):
                    continue
                key = hashlib.sha256(_canon(el).encode()).hexdigest()
            if key in seen:
                continue
            seen.add(key)
            nv = el.find(f".//{{{P}}}cNvPr")
            icons.append(
                IconAsset(
                    id=f"{part}#{nv.get('id') if nv is not None else len(icons)}",
                    part=part,
                    slide_index=index,
                    box=box,
                    xml=etree.tostring(el).decode(),
                    media_b64=media[0] if media else None,
                    media_ext=media[1] if media else None,
                )
            )
            if len(icons) >= _MAX_ICONS:
                return icons
    return icons


_MIN_RASTER_ICON_SHEET = 12


def has_template_icons(template_path: str | Path) -> bool:
    """Есть ли в шаблоне собственные векторные иконки/пиктограммы или лист иконок.

    Одиночные мелкие ``p:pic`` на обычных слайдах — это логотипы, плашки и
    аватары, а не библиотека пиктограмм; их прогон через LibreOffice + VLM
    тратит секунды впустую.
    """
    from collections import Counter

    template_path = Path(template_path)
    pkg = OpcPackage.open(template_path)
    root = etree.fromstring(pkg.parts["ppt/presentation.xml"])
    sld_sz = root.find(f".//{{{P}}}sldSz")
    slide_w = int(sld_sz.get("cx", "12192000")) if sld_sz is not None else 12192000
    parts = pkg.ordered_slide_parts()
    icons = extract_icons(pkg, parts, slide_w)
    if not icons:
        return False
    if any(i.media_b64 is None for i in icons):
        return True
    per_slide = Counter(i.slide_index for i in icons)
    return max(per_slide.values(), default=0) >= _MIN_RASTER_ICON_SHEET


def _render_pages(template_path: Path) -> list[bytes]:
    from deckdna.pptx.exporting import previews
    from deckdna.pptx.exporting.render import render_pdf

    with tempfile.TemporaryDirectory(prefix="deckdna-icons-") as tmp:
        pdf = render_pdf(template_path, Path(tmp) / "template.pdf")
        return previews.pdf_page_pngs(pdf, width_px=1600)


def _grids(icons: list[IconAsset], pages: list[bytes], slide_w: int) -> list[str]:
    """Сетки по ``_BATCH`` иконок: PNG data URL с номером в каждой ячейке."""
    from PIL import Image, ImageDraw

    page_images: dict[int, Image.Image] = {}
    urls: list[str] = []
    for start in range(0, len(icons), _BATCH):
        chunk = icons[start : start + _BATCH]
        rows = (len(chunk) + _GRID - 1) // _GRID
        sheet = Image.new("RGB", (_GRID * _CELL_PX, rows * _CELL_PX), "white")
        draw = ImageDraw.Draw(sheet)
        for k, icon in enumerate(chunk):
            if icon.slide_index >= len(pages):
                continue
            if icon.slide_index not in page_images:
                page_images[icon.slide_index] = Image.open(io.BytesIO(pages[icon.slide_index]))
            page = page_images[icon.slide_index].convert("RGB")
            scale = page.width / slide_w
            x, y, w, h = (int(v * scale) for v in icon.box)
            pad = max(2, int(0.08 * max(w, h)))
            crop = page.crop((max(0, x - pad), max(0, y - pad), x + w + pad, y + h + pad))
            crop.thumbnail((_CELL_PX - 20, _CELL_PX - 20))
            cx, cy = (k % _GRID) * _CELL_PX, (k // _GRID) * _CELL_PX
            sheet.paste(crop, (cx + 10, cy + 14))
            draw.text((cx + 3, cy + 1), str(k), fill="red")
        buf = io.BytesIO()
        sheet.save(buf, "PNG")
        urls.append("data:image/png;base64," + base64.b64encode(buf.getvalue()).decode())
    return urls


def _cache_path(digest: str) -> Path:
    from deckdna.providers.prompts import load_prompt
    from deckdna.settings import settings

    version = getattr(load_prompt(PROMPT_NAME), "version", "0")
    return Path(settings.artifact_root) / "icon_cache" / f"{digest}.{version}.json"


def can_label(gateway: object) -> bool:
    if gateway is None:
        return False
    if getattr(gateway, "provider_name", None) == "mock":
        return PROMPT_NAME in (getattr(gateway, "fixtures", None) or {})
    return getattr(gateway, "_model_vision", True) != ""


async def icon_library(
    template_path: str | Path, gateway: object | None
) -> list[IconAsset]:
    """Подписанные иконки шаблона (кеш по хешу файла). Пусто — если иконок
    нет или модели со зрением нет: без подписи иконку не поставить по смыслу."""
    template_path = Path(template_path)
    data = template_path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    cache = _cache_path(digest)
    if cache.exists():
        try:
            cached = json.loads(cache.read_text())
            return [IconAsset(**{**d, "box": tuple(d["box"])}) for d in cached]
        except (ValueError, TypeError, KeyError):
            pass
    if not can_label(gateway):
        return []
    from pptx import Presentation

    prs = Presentation(str(template_path))
    parts = [str(s.part.partname).lstrip("/") for s in prs.slides]
    pkg = OpcPackage.open(template_path)
    icons = extract_icons(pkg, parts, int(prs.slide_width))
    if not icons:
        return []
    try:
        pages = await asyncio.to_thread(_render_pages, template_path)
    except Exception as exc:  # noqa: BLE001 — без рендера нечего показать модели
        logger.warning("icon library: template render failed (%s)", exc)
        return []
    grids = _grids(icons, pages, int(prs.slide_width))
    sem = asyncio.Semaphore(_MAX_CONCURRENT)

    async def label(grid_no: int, url: str) -> None:
        chunk = icons[grid_no * _BATCH : (grid_no + 1) * _BATCH]
        async with sem:
            try:
                out = await gateway.vision_json(  # type: ignore[attr-defined]
                    PROMPT_NAME, [url], {"count": len(chunk)}, IconLabelBatch
                )
            except Exception as exc:  # noqa: BLE001 — сетка без подписей = без иконок
                logger.warning("icon labels batch %d failed (%s)", grid_no, exc)
                return
        for item in out.icons:
            if 0 <= item.i < len(chunk) and item.is_icon and item.label.strip():
                chunk[item.i].label = item.label.strip()[:40]

    await asyncio.gather(*(label(n, u) for n, u in enumerate(grids)))
    labelled = [i for i in icons if i.label]
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps([asdict(i) for i in labelled], ensure_ascii=False))
    except OSError:
        pass
    logger.info("icon library: %d/%d icons labelled", len(labelled), len(icons))
    return labelled
