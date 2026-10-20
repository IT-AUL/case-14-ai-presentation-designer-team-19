"""Тёмная тема: раскладка «текст + данные» оставляет фон слайда видимым,
а сгенерированный текст получает контрастные чернила.

Живой дефект (vk_workspace, тёмная тема): data-split рисовал белую
bg1-панель, чернила текста считались под неё (чёрные), а затем
remove_orphans снимал панель — её id совпадал с id уже удалённого
донорского текста. Итог: чёрный текст на чёрном слайде и такая же
невидимая сноска «Источник: …» (schemeClr tx1 на тёмном фоне).
"""

from deckdna.pptx.composing.background import align_composed_text_ink
from deckdna.pptx.composing.chart_fill import add_source_note
from deckdna.pptx.composing.orphans import remove_orphans
from deckdna.pptx.composing.sources_layout import compose_data_split
from deckdna.pptx.opc.package import Relationship, rels_name_for, serialize_rels
from lxml import etree

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

_W, _H = 12192000, 6858000


def _slide(*shapes: str) -> bytes:
    return (
        f'<p:sld xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}"><p:cSld><p:spTree>'
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        + "".join(shapes)
        + "</p:spTree></p:cSld></p:sld>"
    ).encode()


def _donor_shape(sid: int, text: str) -> str:
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="S{sid}"/>'
        "<p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr>"
        '<a:xfrm><a:off x="914400" y="914400"/>'
        '<a:ext cx="2743200" cy="914400"/></a:xfrm></p:spPr>'
        f"<p:txBody><a:bodyPr/><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp>"
    )


def _dark_package(slide_xml: bytes) -> tuple[dict[str, bytes], str]:
    """slide → layout (bg dk1=чёрный) → master → theme (dk1 000000/lt1 FFFFFF)."""
    slide = "ppt/slides/slide1.xml"
    layout = "ppt/slideLayouts/slideLayout1.xml"
    master = "ppt/slideMasters/slideMaster1.xml"
    theme = "ppt/theme/theme1.xml"
    parts = {
        slide: slide_xml,
        rels_name_for(slide): serialize_rels(
            [
                Relationship(
                    id="rId1",
                    type="http://schemas.openxmlformats.org/officeDocument/2006/"
                    "relationships/slideLayout",
                    target="../slideLayouts/slideLayout1.xml",
                )
            ]
        ),
        layout: (
            f'<p:sldLayout xmlns:p="{P}" xmlns:a="{A}">'
            '<p:cSld><p:bg><p:bgPr><a:solidFill><a:schemeClr val="dk1"/>'
            "</a:solidFill></p:bgPr></p:bg><p:spTree>"
            '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
            '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
            "</p:spTree></p:cSld></p:sldLayout>"
        ).encode(),
        rels_name_for(layout): serialize_rels(
            [
                Relationship(
                    id="rId1",
                    type="http://schemas.openxmlformats.org/officeDocument/2006/"
                    "relationships/slideMaster",
                    target="../slideMasters/slideMaster1.xml",
                )
            ]
        ),
        master: (
            f'<p:sldMaster xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}">'
            "<p:cSld><p:spTree>"
            '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
            '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
            "</p:spTree></p:cSld>"
            '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" '
            'accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" '
            'accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
            "</p:sldMaster>"
        ).encode(),
        rels_name_for(master): serialize_rels(
            [
                Relationship(
                    id="rId1",
                    type="http://schemas.openxmlformats.org/officeDocument/2006/"
                    "relationships/theme",
                    target="../theme/theme1.xml",
                )
            ]
        ),
        theme: (
            f'<a:theme xmlns:a="{A}"><a:themeElements><a:clrScheme name="dark">'
            '<a:dk1><a:srgbClr val="000000"/></a:dk1>'
            '<a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>'
            '<a:dk2><a:srgbClr val="1F2937"/></a:dk2>'
            '<a:lt2><a:srgbClr val="E5E7EB"/></a:lt2>'
            '<a:accent1><a:srgbClr val="0077FF"/></a:accent1>'
            '<a:accent2><a:srgbClr val="00E9FF"/></a:accent2>'
            '<a:accent3><a:srgbClr val="AAFBFF"/></a:accent3>'
            '<a:accent4><a:srgbClr val="EDF3FC"/></a:accent4>'
            '<a:accent5><a:srgbClr val="7C8A9A"/></a:accent5>'
            '<a:accent6><a:srgbClr val="202020"/></a:accent6>'
            '<a:hlink><a:srgbClr val="0077FF"/></a:hlink>'
            '<a:folHlink><a:srgbClr val="7C8A9A"/></a:folHlink>'
            "</a:clrScheme></a:themeElements></a:theme>"
        ).encode(),
    }
    return parts, slide


def _text_fill(root: etree._Element, name: str) -> str | None:
    for shape in root.iter(f"{{{P}}}sp"):
        props = shape.find(f"{{{P}}}nvSpPr/{{{P}}}cNvPr")
        if props is None or props.get("name") != name:
            continue
        for run_props in shape.iter(f"{{{A}}}rPr"):
            srgb = run_props.find(f"{{{A}}}solidFill/{{{A}}}srgbClr")
            if srgb is not None:
                return srgb.get("val")
    return None


def test_data_split_keeps_the_slide_background_instead_of_a_white_panel():
    donor = _slide(_donor_shape(2, "Заголовок"), _donor_shape(3, "Текст"))
    out = compose_data_split(donor, "Выручка выросла", ["Факт один"], _W, _H)
    names = {p.get("name") for p in etree.fromstring(out).iter(f"{{{P}}}cNvPr")}
    assert "DeckDNA visual panel" not in names
    assert "DeckDNA visual title" in names
    assert "DeckDNA data text" in names


def test_generated_panel_survives_orphan_cleanup_on_id_collision():
    donor = _slide(_donor_shape(2, "Заголовок"), _donor_shape(3, "Текст"))
    # Бесстекстовая сгенерированная фигура переиспользует id донорского
    # слота (так получается при max(ids)+1 после удаления донорских
    # текстов) — видимая заливка, иначе правило «очищенного слота»
    # её и без коллизии не тронуло бы.
    generated = (
        '<p:sp><p:nvSpPr><p:cNvPr id="2" name="DeckDNA visual accent 1"/>'
        "<p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr>"
        '<a:xfrm><a:off x="914400" y="914400"/>'
        '<a:ext cx="200000" cy="914400"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        '<a:solidFill><a:srgbClr val="0077FF"/></a:solidFill></p:spPr></p:sp>'
    )
    after = _slide(generated)
    cleaned, _ = remove_orphans(donor, after, _W, _H)
    names = {p.get("name") for p in etree.fromstring(cleaned).iter(f"{{{P}}}cNvPr")}
    assert "DeckDNA visual accent 1" in names


def test_generated_text_ink_is_light_on_a_dark_layout():
    parts, slide_part = _dark_package(
        compose_data_split(
            _slide(_donor_shape(2, "Заголовок")),
            "Выручка выросла",
            ["Факт"],
            _W,
            _H,
        )
    )
    out = align_composed_text_ink(parts, slide_part, parts[slide_part])
    root = etree.fromstring(out)
    assert _text_fill(root, "DeckDNA visual title") == "FFFFFF"
    assert _text_fill(root, "DeckDNA data text") == "FFFFFF"


def test_source_note_uses_resolved_ink_on_dark_background():
    root = etree.fromstring(_slide())
    frame = etree.fromstring(
        f'<p:graphicFrame xmlns:p="{P}" xmlns:a="{A}">'
        '<p:xfrm><a:off x="5080000" y="1440180"/><a:ext cx="5588000" cy="4000000"/></p:xfrm>'
        "</p:graphicFrame>"
    )
    root.find(f"{{{P}}}cSld/{{{P}}}spTree").append(frame)
    add_source_note(root, frame, "Источник: sales.csv", ink="FFFFFF")
    srgb = next(
        el
        for el in root.iter(f"{{{A}}}srgbClr")
        if el.get("val") == "FFFFFF" and etree.QName(el.getparent()).localname == "solidFill"
    )
    assert srgb is not None
    # lumMod/lumOff приглушают подпись, но белый остаётся белым (lum 1.0).
    assert srgb.find(f"{{{A}}}lumMod").get("val") == "65000"
