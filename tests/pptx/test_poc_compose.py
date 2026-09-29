"""End-to-end POC test: DeckPlan -> multi-slide out.pptx -> PNG renders.

Golden path of the minimal Constraint Compiler on the organizer fixture:
- one suitable exemplar per SlidePlan, selected from the arbitrary template;
- all N slides emitted as ppt/slides/slide1..N.xml in plan order;
- output opens via python-pptx, has full relationship integrity;
- each slide carries the texts of its own SlidePlan;
- LibreOffice headless renders every slide into tests/fixtures/output/.
"""

import json
import shutil
import zipfile
from pathlib import Path

import pytest
from deckdna.pptx.composing.minimal import generate_deck
from deckdna.pptx.opc.package import CONTENT_TYPES, PRESENTATION, OpcPackage
from lxml import etree

FIXTURE = Path("tests/fixtures/pptx/vk_tech_template.pptx")
DECK_PLAN = Path("tests/fixtures/content/poc_deck_plan.json")
OUT_DIR = Path("tests/fixtures/output")
OUT_PPTX = OUT_DIR / "out.pptx"

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"

N_SLIDES = 5


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    if not FIXTURE.exists():
        pytest.skip("organizer fixture not present")
    plan = json.loads(DECK_PLAN.read_text(encoding="utf-8"))
    return generate_deck(FIXTURE, plan, tmp_path_factory.mktemp("compose") / "out.pptx")


def _slide_texts(report, idx: int) -> list[str]:
    with zipfile.ZipFile(report["output"]) as zf:
        root = etree.fromstring(zf.read(f"ppt/slides/slide{idx}.xml"))
    return [t.text for t in root.findall(f".//{{{A}}}t") if t.text]


def test_exemplar_selection(report):
    """Every chosen slide/layout comes from the supplied template."""
    from deckdna.pptx.cloning.exemplar import slide_layout_map
    pkg = OpcPackage.open(FIXTURE)
    layouts = slide_layout_map(pkg)
    assert len(report["slides"]) == N_SLIDES
    assert report["slides_out"] == N_SLIDES
    for slide in report["slides"]:
        exemplar = slide["exemplar"]
        assert exemplar["slide_part"] in pkg.parts
        assert exemplar["layout_part"] == layouts[exemplar["slide_part"]]
        assert exemplar["content_slots"] > 0


def test_exemplars_rotate(report):
    """Different plan slides back onto different exemplars where possible."""
    sources = {s["exemplar"]["slide_part"] for s in report["slides"]}
    # the layout pool has more than one content-like slide: no single
    # exemplar serves all N plan slides
    assert len(sources) >= 2
    # decorative densest slide is still gated out of every pick
    assert "ppt/slides/slide36.xml" not in sources


def test_decorative_slides_are_gated_out():
    """Fragmented-run slides (per-letter decor, KPI labels) lose the gate."""
    from deckdna.pptx.cloning.exemplar import run_profile

    if not FIXTURE.exists():
        pytest.skip("organizer fixture not present")
    pkg = OpcPackage.open(FIXTURE)
    # slide36: densest scene but almost no substitutable long text
    assert not run_profile(pkg, "ppt/slides/slide36.xml").is_content_like
    assert run_profile(pkg, "ppt/slides/slide16.xml").is_content_like


def test_icon_badge_grid_is_gated_out_on_the_real_fixture():
    """Live incident: an exemplar with 17 shapes all reading literally
    ">50*" was picked as a content slide -- real content landed as single
    fragmented words in badge-sized (0.4x0.2in) boxes, the 17 badges kept
    the template's own stock text untouched (correctly judged decorative
    by the per-shape gate in text_replace.py, so skipped rather than
    cleared). slide21 on the organizer fixture has that exact shape:
    a short label repeated 20x against a single long run."""
    from deckdna.pptx.cloning.exemplar import run_profile

    if not FIXTURE.exists():
        pytest.skip("organizer fixture not present")
    pkg = OpcPackage.open(FIXTURE)
    profile = run_profile(pkg, "ppt/slides/slide21.xml")
    assert profile.is_badge_grid
    assert not profile.is_content_like


def test_is_badge_grid_isolates_repetition_from_the_long_runs_gate():
    """Both examples below pass every OTHER is_content_like condition
    (long_runs/tiny_ratio/mean_len) identically -- only max_repeated_short_run
    differs, isolating is_badge_grid as the actual deciding signal.

    A real N-card grid repeats title+body ~1:1 (template author never
    diversified the demo text across cards, e.g. "Безопасность" + its
    paired sentence, both 8x on the organizer fixture's slide16 family)
    -- must NOT be flagged, unlike a short label repeated with no
    substantial paired body (a badge grid)."""
    from deckdna.pptx.cloning.exemplar import RunProfile

    card_grid = RunProfile(
        runs_total=17, runs_nonempty=17, tiny_runs=0, long_runs=8, mean_nonempty_len=30.0,
        max_repeated_short_run=8,  # repeated title, but only as often as its paired long body
    )
    assert not card_grid.is_badge_grid
    assert card_grid.is_content_like

    badge_grid = RunProfile(
        runs_total=25, runs_nonempty=25, tiny_runs=0, long_runs=8, mean_nonempty_len=30.0,
        max_repeated_short_run=25,  # same long_runs, but the short label repeats far more
    )
    assert badge_grid.is_badge_grid
    assert not badge_grid.is_content_like


def test_output_opens_with_python_pptx(report):
    from pptx import Presentation

    prs = Presentation(report["output"])
    assert len(prs.slides) == N_SLIDES
    # template chrome inherited: 16:9 slide size preserved
    assert prs.slide_width == 9144000
    assert prs.slide_height == 5143500


def test_relationship_integrity(report):
    """Every internal rel resolves; sldIdLst has N entries in order."""
    out = OpcPackage.open(report["output"])
    assert out.check_rel_integrity() == []
    missing_ct = out.content_type_coverage(set(out.parts) - {CONTENT_TYPES})
    assert missing_ct == []

    pres = etree.fromstring(out.parts[PRESENTATION])
    sld_ids = pres.findall(f".//{{{P}}}sldIdLst/{{{P}}}sldId")
    assert len(sld_ids) == N_SLIDES
    # sldIdLst order matches the rels pointing at slide1..N in order
    rels = {r.id: r for r in out.rels(PRESENTATION)}
    assert [rels[el.get(R_ID)].target for el in sld_ids] == [
        f"slides/slide{i}.xml" for i in range(1, N_SLIDES + 1)
    ]


def test_images_and_layout_cloned(report):
    """Slides' media + layout→master→theme chain came along once."""
    with zipfile.ZipFile(report["output"]) as zf:
        names = set(zf.namelist())
    for i in range(1, N_SLIDES + 1):
        assert f"ppt/slides/slide{i}.xml" in names
    for slide in report["slides"]:
        assert slide["exemplar"]["layout_part"] in names
    assert any(n.startswith("ppt/media/") for n in names)
    assert any(n.startswith("ppt/slideMasters/") for n in names)
    assert any(n.startswith("ppt/theme/") for n in names)
    # no source slide names leaked through
    assert not any(
        n.startswith("ppt/slides/slide")
        and int(n.removeprefix("ppt/slides/slide").removesuffix(".xml")) > N_SLIDES
        for n in names
    )
    # notes/comments dropped per spec
    assert not any(n.startswith("ppt/notesSlides/") for n in names)


def test_plan_texts_landed_per_slide(report):
    """Slide i carries the texts and title of plan slide i, not a shared pool."""
    texts1 = _slide_texts(report, 1)
    texts2 = _slide_texts(report, 2)
    # title goes to the title placeholder, not the positional pool
    assert "DeckDNA компилирует шаблон" in texts1
    assert "Шаблоны не масштабируются" in texts2
    # slide 2's texts must not leak into slide 1
    assert "Шаблоны не масштабируются" not in texts1


def test_titles_replaced_via_placeholder(report):
    """title_intent reaches the title placeholder on every slide."""
    for i, slide in enumerate(report["slides"], start=1):
        assert slide["text"]["title_placed"], f"slide {i} title not placed"
    texts5 = _slide_texts(report, 5)
    assert "Следующие шаги" in texts5
    assert "Заголовок" not in texts5


# Stock copy of the organizer template that must never survive into a
# generated deck — regression for the real-user defect (sparse plans
# left card text / Lorem-ipsum placeholders visible).
_STOCK_TEXTS = (
    "Безопасность",
    "Оцените высокий уровень защищенности инфраструктуры",
    "Lorem",
    "Имя Фамилия",
    "Должность",
    "Название команды",
    "Заголовок",
    "в две или одну строчку",
)


def test_no_original_template_text_survives(report):
    """Every unfilled content slot is cleared — no template copy leaks."""
    for i in range(1, N_SLIDES + 1):
        texts = _slide_texts(report, i)
        for stock in _STOCK_TEXTS:
            assert stock not in texts, (
                f"slide {i}: stock template text survived: {stock!r}"
            )


def test_per_slide_replace_report(report):
    """Per-slide stats + totals; decorative/empty runs were skipped."""
    for slide in report["slides"]:
        text = slide["text"]
        assert text["runs_replaced"] > 0
        # every eligible body is either filled from the plan or cleared
        assert (
            text["bodies_filled"] + text["bodies_cleared"]
            == text["bodies_eligible"]
        )
        assert text["bodies_filled"] == min(
            text["texts_supplied"], text["bodies_eligible"]
        )
        assert text["texts_dropped"] == 0
        assert text["bodies_truncated"] == 0
    totals = report["totals"]
    assert totals["runs_replaced"] == sum(
        s["text"]["runs_replaced"] for s in report["slides"]
    )
    assert totals["bodies_cleared"] >= 0


def test_shrink_to_fit_preserves_complete_text_in_a_finite_box():
    """Shrink a real editable textbox; no clipping or geometry substitution."""
    from deckdna.pptx.composing.text_replace import _body_overflows, replace_text_runs
    from pptx import Presentation
    from pptx.util import Inches, Pt

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    shape = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(8), Inches(.6))
    shape.text = "Source template text"
    shape.text_frame.paragraphs[0].runs[0].font.size = Pt(32)
    text = (
        "Полный научный вывод сохраняет условия эксперимента, "
        "ограничения и числовые показатели 25% и 30%."
    )
    body = shape._element.find(f"{{{P}}}txBody")
    assert _body_overflows(body, text)
    xml, stats = replace_text_runs(etree.tostring(slide._element), [text])
    root = etree.fromstring(xml)
    fitted = root.find(f".//{{{P}}}txBody")
    assert not _body_overflows(fitted, text)
    assert stats.bodies_shrunk == 1
    assert stats.bodies_truncated == stats.texts_dropped == 0
    assert text in [t.text for t in root.iter(f"{{{A}}}t")]


def test_long_text_shrunk_to_floor_bound(tmp_path):
    """A deliberately oversized text in a real slot gets a reduced sz
    that never goes below FONT_FLOOR_PT."""
    from deckdna.pptx.composing.text_replace import (
        FONT_FLOOR_PT,
        replace_text_runs,
    )

    if not FIXTURE.exists():
        pytest.skip("organizer fixture not present")
    pkg = OpcPackage.open(FIXTURE)
    slide_xml = pkg.parts["ppt/slides/slide16.xml"]
    long_text = (
        "Очень длинный заголовок карточки, который заведомо не помещается "
        "в отведённый слот при исходном кегле и должен быть ужат"
    )
    xml, rep = replace_text_runs(slide_xml, [long_text], title_text="Короткий титул")
    assert rep.bodies_shrunk >= 1
    root = etree.fromstring(xml)
    sizes = [
        int(el.get("sz"))
        for el in root.iter(f"{{{A}}}rPr", f"{{{A}}}endParaRPr")
        if el.get("sz")
    ]
    assert min(sizes) >= int(FONT_FLOOR_PT * 100)


def test_libreoffice_renders_all_slides(report):
    """The real gate: soffice renders every slide of the deck to PNG."""
    if shutil.which("soffice") is None or shutil.which("pdftoppm") is None:
        pytest.skip("soffice/pdftoppm not installed")
    from deckdna.pptx.exporting.render import render_slides_png
    from PIL import Image

    pngs = render_slides_png(
        report["output"], Path(report["output"]).parent, first=1, last=N_SLIDES
    )
    assert len(pngs) == N_SLIDES
    for png in pngs:
        assert png.exists() and png.stat().st_size > 10_000
        with Image.open(png) as img:
            # non-blank slide: render has real pixel variance
            extrema = img.convert("L").getextrema()
            assert extrema[1] - extrema[0] > 40
