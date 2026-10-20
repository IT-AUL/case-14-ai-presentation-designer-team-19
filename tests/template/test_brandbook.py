"""ADR-033 §9: брендбук — разбор входов и слияние с шаблоном."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import httpx
from deckdna.ingestion.intake import detect
from deckdna.template.brandbook import (
    apply_brandbook,
    build_brandbook,
    fetch_figma_variables,
    figma_file_key,
    parse_pdf_text,
    template_look,
    theme_of,
)

ROOT = Path(__file__).resolve().parents[2]
BRAND = ROOT / "examples" / "stage2" / "brandbook"
FIXTURES = ROOT / "tests" / "fixtures" / "pptx"
WANT_COLORS = {"primary": "#E4572E", "secondary": "#29335C", "accent": "#F3A712"}
WANT_FONTS = {"heading": "DejaVu Serif", "body": "Liberation Sans"}


def _book(*names: str):
    return build_brandbook([(n, (BRAND / n).read_bytes()) for n in names], "bb")


def _roles(book) -> tuple[dict[str, str], dict[str, str]]:
    return ({c.role: c.hex for c in book.colors if c.role in WANT_COLORS},
            {f.role: f.family for f in book.fonts})


def test_every_input_gives_the_same_brand():
    for name in ("tokens.json", "figma_variables.json", "guideline.pdf"):
        colors, fonts = _roles(_book(name))
        assert colors == WANT_COLORS, name
        assert fonts == WANT_FONTS, name


def test_token_dialects():
    style_dictionary = {"color": {"brand": {"primary": {"value": "#123456"},
                                            "secondary": {"value": "#ABCDEF"}}},
                        "font": {"heading": {"value": "Inter"}, "body": {"value": "Roboto"}}}
    studio = {"global": {"primary": {"value": "#123456", "type": "color"},
                         "accent": {"value": "rgba(200, 30, 30, 1)", "type": "color"},
                         "h1": {"value": {"fontFamily": "Inter", "fontSize": 40},
                                "type": "typography"}}}
    simple = {"colors": {"primary": "#123456", "accent": "#C81E1E"},
              "fonts": {"heading": "Inter", "body": "Roboto"}}
    for raw in (style_dictionary, studio, simple):
        book = build_brandbook([("t.json", json.dumps(raw).encode())], "x")
        assert book.color("primary").hex == "#123456"
        assert book.font("heading").family == "Inter"


def test_pdf_text_rgb_and_cmyk():
    found = parse_pdf_text(
        "Основной цвет: RGB 228, 87, 46\nАкцентный — CMYK 0 31 93 5\n"
        "Шрифт заголовков: PT Serif\nЛоготип: не меньше 20 мм", "g.pdf")
    assert [c.hex for c in found.colors] == ["#E4572E", "#F2A711"]
    assert found.colors[0].role == "primary" and found.colors[1].role == "accent"
    assert found.fonts[0].family == "PT Serif" and found.fonts[0].role == "heading"
    assert found.rules


def test_figma_rest_with_mock_transport_and_without_token():
    payload = json.loads((BRAND / "figma_variables.json").read_text())
    variables = {f"v{i}": v for i, v in enumerate(payload["collections"][0]["variables"])}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Figma-Token"] == "tok"
        assert request.url.path == "/v1/files/AbC123/variables/local"
        return httpx.Response(200, json={"meta": {"variables": variables}})

    key = figma_file_key("https://www.figma.com/design/AbC123/Brand?node-id=1-2")
    data = fetch_figma_variables(key, "tok", transport=httpx.MockTransport(handler))
    book = build_brandbook([("figma.json", json.dumps(data).encode())], "f")
    assert _roles(book) == (WANT_COLORS, WANT_FONTS)
    assert fetch_figma_variables(key, None) is None


def test_intake_detection():
    assert detect("tokens.json", (BRAND / "tokens.json").read_bytes()).kind == "brandbook"
    assert detect("guideline.pdf", (BRAND / "guideline.pdf").read_bytes()).kind == "brandbook"
    assert detect("logo.png", (BRAND / "logo.png").read_bytes()).kind == "brandbook"
    stage2 = ROOT / "examples" / "stage2"
    assert detect("kpi.json", (stage2 / "kpi.json").read_bytes()).kind == "data"
    assert detect("sales.csv", b"a;b\n1;2\n").kind == "data"
    assert detect("t.pptx", b"").kind == "template"
    assert detect("old.pptx", b"", template_taken=True).kind == "content"
    assert detect("notes.md", b"# x").kind == "content"
    assert detect("photo.png", b"").kind == "unknown"


def _parts(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {n: z.read(n) for n in z.namelist()}


def test_apply_brandbook_recolors_theme_and_slides_keeps_unspecified_neutrals():
    book = _book("tokens.json", "logo.png")
    src = (FIXTURES / "vk_tech_template.pptx").read_bytes()
    branded, report = apply_brandbook(src, book)
    colors, fonts = theme_of(_parts(branded))
    assert colors["accent1"] == "#E4572E"
    assert fonts == {"major": "DejaVu Serif", "minor": "Liberation Sans"}
    old_colors, _ = theme_of(_parts(src))
    assert colors["dk1"] == book.color("text").hex
    assert colors["lt1"] == book.color("background").hex
    for slot in ("accent2", "accent5"):  # unspecified neutrals stay unchanged
        assert colors[slot] == old_colors[slot]
    look = template_look(branded)
    assert "#0077FF" not in look.palette and look.palette[0] == "#E4572E"
    assert {c.field for c in report.conflicts} >= {"color.primary", "font.heading"}
    assert all(c.resolved == "brandbook" for c in report.conflicts)
    # тот же набор частей пакета, меняются только тема и рисунок
    assert set(_parts(branded)) == set(_parts(src))


def test_template_priority_changes_nothing_but_reports_conflicts():
    book = _book("tokens.json")
    src = (FIXTURES / "vk_workspace.pptx").read_bytes()
    same, report = apply_brandbook(src, book, "template")
    assert same == src and not report.applied
    assert report.conflicts and all(c.resolved == "template" for c in report.conflicts)
