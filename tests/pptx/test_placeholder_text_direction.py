"""Inherited vertical placeholders are not horizontal prose slots."""
from deckdna.pptx.composing.text_replace import _body_is_decorative, count_content_slots
from lxml import etree

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _slide(*, orient="vert", body_mode=None):
    own = f' vert="{body_mode}"' if body_mode else ""
    return (f'<p:sld xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree><p:sp>'
            '<p:nvSpPr><p:cNvPr id="2" name="Body"/><p:cNvSpPr/><p:nvPr>'
            f'<p:ph type="body" orient="{orient}"/></p:nvPr></p:nvSpPr>'
            '<p:spPr><a:xfrm><a:off x="100000" y="100000"/>'
            '<a:ext cx="6000000" cy="4000000"/></a:xfrm></p:spPr>'
            f'<p:txBody><a:bodyPr{own}/><a:p><a:r><a:t>Long source prose for a full text slot.'
            '</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>').encode()


def test_vertical_placeholder_hint_is_not_lost_when_body_properties_are_empty():
    xml = _slide()
    body = etree.fromstring(xml).find(f'.//{{{P}}}txBody')
    assert _body_is_decorative(body)
    assert count_content_slots(xml) == 0


def test_explicit_horizontal_body_overrides_placeholder_hint():
    xml = _slide(body_mode="horz")
    body = etree.fromstring(xml).find(f'.//{{{P}}}txBody')
    assert not _body_is_decorative(body)
    assert count_content_slots(xml) == 1


def test_explicit_vertical_mode_is_not_a_horizontal_slot():
    xml = _slide(orient="horz", body_mode="vert270")
    assert count_content_slots(xml) == 0
