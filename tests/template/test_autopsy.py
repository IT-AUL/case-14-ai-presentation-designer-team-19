"""Forensic regression: the pitch claims are reproduced by code on the
real organizer fixture, not by hand-counted numbers.

- 36 of 54 VK Tech slides sit on a single layout (layout11);
- theme declares Arial while observed runs are dominated by Play/Calibri.
"""

import pytest
from deckdna.template.autopsy import analyze_template

VK_TEMPLATE = "dop-data/Датасет/VK Tech шаблон.pptx"


@pytest.fixture(scope="module")
def forensics():
    import os

    if not os.path.exists(VK_TEMPLATE):
        pytest.skip("organizer fixture not present")
    return analyze_template(VK_TEMPLATE)


def test_package_census(forensics):
    assert forensics.slides == 54
    assert forensics.masters == 2
    assert forensics.layouts == 39
    assert forensics.themes == 3
    assert forensics.media == 222
    assert forensics.charts == 0


def test_dominant_layout_claim(forensics):
    lid, count = forensics.dominant_layout
    assert lid == "11"
    assert count == 36
    assert forensics.dominant_layout_share == pytest.approx(36 / 54)


def test_declared_vs_observed_fonts(forensics):
    assert forensics.declared_fonts == ["Arial"]
    top_observed = next(iter(forensics.observed_fonts))
    assert top_observed == "Play"
    assert forensics.observed_fonts["Play"] > forensics.observed_fonts.get("Arial", 0)


def test_declared_theme_palettes(forensics):
    """Declared-палитра из theme XML: vk_tech несёт 3 темы — 2 одинаковые
    кастомные VK Tech и стандартную Тема Office."""
    palettes = forensics.theme_palettes
    assert len(palettes) == forensics.themes == 3
    vk = palettes["VK Tech"]
    # все 12 слоты clrScheme извлечены
    assert len(vk) == 12
    assert vk["accent1"] == "0077FF"
    assert vk["dk1"] == "000000" and vk["lt1"] == "FAFCFF"
    # дубликаты имён тем суффиксируются, палитра идентична
    assert palettes["VK Tech-2"] == vk
    office = palettes["Тема Office"]
    assert office["accent1"] == "4472C4"  # стандартная офисная палитра
    # to_dict пробрасывает палитры
    assert forensics.to_dict()["theme_palettes"] == palettes


def test_slot_hex_sysclr_fallback():
    """a:sysClr без srgbClr -> берём lastClr (фолбэк PowerPoint)."""
    from deckdna.template.autopsy import _slot_hex
    from lxml import etree

    A = "http://schemas.openxmlformats.org/drawingml/2006/main"
    slot = etree.fromstring(
        f'<a:dk1 xmlns:a="{A}">'
        f'<a:sysClr val="windowText" lastClr="1a2b3c"/></a:dk1>'
    )
    assert _slot_hex(slot) == "1A2B3C"
    srgb = etree.fromstring(
        f'<a:lt1 xmlns:a="{A}"><a:srgbClr val="ffee00"/></a:lt1>'
    )
    assert _slot_hex(srgb) == "FFEE00"
    empty = etree.fromstring(f'<a:dk2 xmlns:a="{A}"><a:scrgbClr r="0" g="0" b="0"/></a:dk2>')
    assert _slot_hex(empty) is None


def test_observed_colors(forensics):
    """Observed-палитра: реально используемые solid fill на слайдах.

    Самый частый fill — declared accent1 темы (VK-синий), за ним
    нейтральные (белый фон карточек, серые плейсхолдеры) — видно
    на рендере слайдов шаблона.
    """
    colors = forensics.observed_colors
    assert colors, "на слайдах шаблона есть solid fill"
    assert len(colors) <= 10
    # ключи — hex RRGGBB верхним регистром, значения — частоты
    for hexv, count in colors.items():
        assert len(hexv) == 6 and hexv == hexv.upper()
        assert int(hexv, 16) >= 0 and count >= 1
    # top-1 = declared accent1 VK-темы — палитра реально используется
    top_hex, top_count = next(iter(colors.items()))
    assert top_hex == forensics.theme_palettes["VK Tech"]["accent1"]
    # частоты отсортированы по убыванию (Counter.most_common)
    counts = list(colors.values())
    assert counts == sorted(counts, reverse=True)
    # to_dict пробрасывает observed-палитру
    assert forensics.to_dict()["observed_colors"] == colors


def test_fill_hex_scheme_resolution():
    """schemeClr резолвится через declared-палитру темы; alias tx*/bg*."""
    from deckdna.template.autopsy import _fill_hex
    from lxml import etree

    A = "http://schemas.openxmlformats.org/drawingml/2006/main"
    palette = {"accent1": "0077FF", "dk1": "000000", "lt1": "FAFCFF"}

    def fill(inner: str) -> etree._Element:
        return etree.fromstring(f'<a:solidFill xmlns:a="{A}">{inner}</a:solidFill>')

    assert _fill_hex(fill('<a:schemeClr val="accent1"/>'), palette) == "0077FF"
    # tx1 — алиас dk1, bg1 — алиас lt1 (ECMA-376)
    assert _fill_hex(fill('<a:schemeClr val="tx1"/>'), palette) == "000000"
    assert _fill_hex(fill('<a:schemeClr val="bg1"/>'), palette) == "FAFCFF"
    assert _fill_hex(fill('<a:srgbClr val="a1b2c3"/>'), palette) == "A1B2C3"
    # без палитры schemeClr не резолвится — честный None, не выдуманный hex
    assert _fill_hex(fill('<a:schemeClr val="accent2"/>'), None) is None
    assert _fill_hex(fill('<a:schemeClr val="accent3"/>'), palette) is None


def test_analyzer_is_template_agnostic():
    """Same code path must work on any pptx — no fixture names baked in."""
    import inspect

    import deckdna.template.autopsy as mod

    src = inspect.getsource(mod)
    assert "VK Tech" not in src and "шаблон" not in src
