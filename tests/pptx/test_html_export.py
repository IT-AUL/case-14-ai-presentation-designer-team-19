"""HTML must represent slide content as markup, independently of slide previews."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from deckdna.pptx.exporting.html import html_files
from deckdna.pptx.exporting.previews import html_bundle_zip
from deckdna.pptx.exporting.render import render_html
from lxml import etree, html
from PIL import Image
from pptx import Presentation
from pptx.chart.data import CategoryChartData, XyChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Inches, Pt

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pptx"


def synthetic_deck(path):
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(12), Inches(7)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = RGBColor.from_string("F1E2D3")
    box = slide.shapes.add_textbox(Inches(1), Inches(0.5), Inches(10), Inches(1.5))
    p = box.text_frame.paragraphs[0]
    p.text = "Текст <DOM> & данные"
    p.runs[0].font.bold = True
    p.runs[0].font.size = Pt(28)
    p.runs[0].font.color.rgb = RGBColor.from_string("224466")
    p.runs[0].font.name = "+mj-lt"
    p = box.text_frame.add_paragraph()
    p.text = "Пункт списка"
    bullet = OxmlElement("a:buChar")
    bullet.set("char", "•")
    p._p.get_or_add_pPr().append(bullet)
    table = slide.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(5), Inches(1.5)).table
    table.cell(0, 0).text = "Показатель"
    table.cell(0, 1).text = "Значение"
    table.cell(1, 0).text = "Рост 42%"
    table.cell(1, 0).merge(table.cell(1, 1))
    pic = io.BytesIO()
    Image.new("RGB", (30, 20), "purple").save(pic, "PNG")
    slide.shapes.add_picture(
        io.BytesIO(pic.getvalue()), Inches(10), Inches(5), Inches(1), Inches(1)
    )
    # Same image in the master checks inherited non-placeholder content and dedup.
    picture_xml = etree.fromstring(etree.tostring(slide.shapes[-1].element))
    master = slide.slide_layout.slide_master
    _, rid = master.part.get_or_add_image_part(io.BytesIO(pic.getvalue()))
    picture_xml.find(".//{http://schemas.openxmlformats.org/drawingml/2006/main}blip").set(
        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed", rid
    )
    master.shapes._spTree.insert_element_before(picture_xml, "p:extLst")
    data = CategoryChartData()
    data.categories = ["A", "B", "C"]
    data.add_series("Выручка", [2, -1, 4])
    slide.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(6), Inches(2), Inches(5), Inches(3), data
    )
    prs.save(path)
    return path


@pytest.fixture
def deck(tmp_path):
    return synthetic_deck(tmp_path / "synthetic.pptx")


def document(path):
    files = html_files(path)
    return html.fromstring(files["index.html"]), files


def test_html_text_tables_charts_images_are_objects(deck, monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("Supported content must not render slide screenshots/PDF")

    monkeypatch.setattr("deckdna.pptx.exporting.render.render_slides_png", forbidden)
    monkeypatch.setattr("deckdna.pptx.exporting.render.render_pdf", forbidden)
    index = render_html(deck, tmp_path / "html")
    doc = html.parse(str(index))
    assert "Текст <DOM> & данные" in doc.xpath('//div[@class="canvas"]')[0].text_content()
    assert doc.xpath('//ul/li/span[text()="Пункт списка"]')
    assert doc.xpath('//table//td[@colspan="2"]//span[text()="Рост 42%"]')
    assert doc.xpath(
        '//*[local-name()="svg"]/*[local-name()="desc" and contains(text(),"Выручка")]'
    )
    assert len(doc.xpath("//img")) == 2  # package picture + master logo, no slide image
    assert all(x.startswith("assets/") for x in doc.xpath("//img/@src"))
    assert not list(index.parent.glob("slide-*.png"))
    assert doc.xpath('//span[contains(@style,"font-weight:700") and contains(@style,"#224466")]')
    assert "font-size:5.55556cqh" in index.read_text()  # 28pt / 504pt height
    assert "background:#F1E2D3" in index.read_text()
    assert "aspect-ratio:10972800/6400800" in index.read_text()
    assert "@media print" in index.read_text() and "ArrowRight" in index.read_text()
    assert "@page{size:864pt 504pt;margin:0}" in index.read_text()


def test_api_zip_and_cli_share_html_objects_and_assets(deck, tmp_path):
    # Legacy preview argument accepted but cannot replace any content in the DOM.
    zipped = html_bundle_zip(deck, [b"not a PNG"], title="HTML & Deck", slide_titles=["Данные"])
    index = render_html(deck, tmp_path / "html")
    with zipfile.ZipFile(io.BytesIO(zipped)) as archive:
        doc = html.fromstring(archive.read("index.html"))
        assert doc.xpath("//h1/text()") == ["HTML & Deck"]
        assert "Слайд 1 — Данные" in doc.text_content()
        assert len(doc.xpath("//table")) == 1
        assert len(doc.xpath('//div[@class="object"]')) == len(
            html.parse(str(index)).xpath('//div[@class="object"]')
        )
        assert all(n == "index.html" or n.startswith("assets/") for n in archive.namelist())
        for src in doc.xpath("//img/@src"):
            assert archive.read(src).startswith(b"\x89PNG")


@pytest.mark.parametrize(
    "chart_type",
    [
        XL_CHART_TYPE.COLUMN_CLUSTERED,
        XL_CHART_TYPE.BAR_CLUSTERED,
        XL_CHART_TYPE.LINE,
        XL_CHART_TYPE.PIE,
        XL_CHART_TYPE.DOUGHNUT,
        XL_CHART_TYPE.XY_SCATTER,
    ],
)
def test_native_chart_types_from_data(tmp_path, chart_type, monkeypatch):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    if chart_type == XL_CHART_TYPE.XY_SCATTER:
        data = XyChartData()
        ser = data.add_series("Измерения")
        for x, y in [(-100, 2), (10, -3), (200, 6)]:
            ser.add_data_point(x, y)
    else:
        data = CategoryChartData()
        data.categories = ["Точка один", "Точка два", "Точка три"]
        data.add_series("Измерения", [2, 3, 6])
    slide.shapes.add_chart(chart_type, Inches(1), Inches(1), Inches(8), Inches(4), data)
    path = tmp_path / "chart.pptx"
    prs.save(path)
    monkeypatch.setattr(
        "deckdna.pptx.exporting.render.render_pdf",
        lambda *a, **k: pytest.fail("native SVG expected"),
    )
    doc, _ = document(path)
    svg = doc.xpath('//*[local-name()="svg" and @role="img"]')
    assert len(svg) == 1
    assert "Измерения" in svg[0].text_content()
    assert not doc.xpath("//img")
    assert svg[0].xpath('.//*[local-name()="rect" or local-name()="path" or local-name()="circle"]')
    assert "nan" not in etree.tostring(svg[0]).decode().lower()


@pytest.mark.parametrize(
    "name", ["vk_tech.pptx", "vk_education.pptx", "vk_workspace.pptx", "synthetic_unseen.pptx"]
)
def test_organizer_and_unseen_templates_keep_all_slide_text_in_dom(name):
    path = FIXTURES / name
    doc, files = document(path)
    prs = Presentation(path)
    slides = doc.xpath('//section[@class="slide"]')
    assert len(slides) == len(prs.slides)

    def texts(shapes):
        for shape in shapes:
            if shape.has_text_frame:
                yield from (r.text for p in shape.text_frame.paragraphs for r in p.runs)
            if shape.shape_type == 6:  # group
                yield from texts(shape.shapes)

    for pptx_slide, section in zip(prs.slides, slides, strict=True):
        visible = section.text_content()
        for text in texts(pptx_slide.shapes):
            assert text in visible
    assert not any(n.startswith("slide-") for n in files)
    for src in doc.xpath("//img/@src"):
        assert src in files


def test_group_geometry_and_theme_inheritance(tmp_path):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = "Тема заголовка"
    placeholder = slide.slide_layout.placeholders[0]
    placeholder.text_frame.paragraphs[0].font.size = Pt(36)
    group = slide.shapes.add_group_shape()
    box = group.shapes.add_textbox(Inches(1), Inches(2), Inches(2), Inches(1))
    box.text = "Текст группы"
    group.left, group.top = Inches(3), Inches(3)
    group.width, group.height = Inches(4), Inches(2)
    path = tmp_path / "group.pptx"
    prs.save(path)
    doc, _ = document(path)
    span = doc.xpath('//span[text()="Тема заголовка"]')[0]
    assert "font-size:6.66667cqh" in span.get("style")  # 36 / 540 pt
    group_box = doc.xpath('//div[@class="object"][.//span[text()="Текст группы"]]')[0]
    assert "left:30.00000%" in group_box.get("style")
    assert "width:40.00000%" in group_box.get("style")
    assert "font-size:6.66667cqh" in group_box.xpath(".//span")[0].get("style")


def test_master_picture_background_and_hidden_master_shapes(tmp_path):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    pic = io.BytesIO()
    Image.new("RGB", (4, 4), "green").save(pic, "PNG")
    master = slide.slide_layout.slide_master
    _, rid = master.part.get_or_add_image_part(io.BytesIO(pic.getvalue()))
    bg = master.background._cSld.get_or_add_bg()
    for el in list(bg):
        bg.remove(el)
    props = OxmlElement("p:bgPr")
    fill = OxmlElement("a:blipFill")
    blip = OxmlElement("a:blip")
    blip.set("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed", rid)
    fill.append(blip)
    props.append(fill)
    bg.append(props)
    path = tmp_path / "background.pptx"
    prs.save(path)
    doc, files = document(path)
    images = doc.xpath('//div[@class="canvas"]/img[@class="background"]')
    assert len(images) == 1 and images[0].get("src") in files


def test_unsupported_chart_fallback_crops_only_object(tmp_path, monkeypatch):
    import pymupdf

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_textbox(
        Inches(0.2), Inches(0.2), Inches(4), Inches(0.5)
    ).text = "Остаётся текстом"
    data = CategoryChartData()
    data.categories = ["A", "B"]
    data.add_series("Series", [3, 8])
    slide.shapes.add_chart(XL_CHART_TYPE.AREA, Inches(2), Inches(2), Inches(4), Inches(2), data)
    path = tmp_path / "fallback.pptx"
    prs.save(path)
    calls = []

    def pdf_render(source, target):
        calls.append(source)
        pdf = pymupdf.open()
        pdf.new_page(width=720, height=540)
        pdf.save(target)
        pdf.close()
        return target

    monkeypatch.setattr("deckdna.pptx.exporting.render.render_pdf", pdf_render)
    doc, files = document(path)
    assert len(calls) == 1
    assert doc.xpath('//span[text()="Остаётся текстом"]')
    images = doc.xpath('//img[@data-fallback="object"]')
    assert len(images) == 1 and images[0].get("alt").startswith("График ")
    with Image.open(io.BytesIO(files[images[0].get("src")])) as image:
        assert image.size == (576, 288)  # 4x2 inches at 144 dpi, not 1440x1080 page


def test_embedded_table_style_uses_runtime_colors_and_fonts(tmp_path):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    table = slide.shapes.add_table(2, 1, Inches(1), Inches(1), Inches(4), Inches(2)).table
    table.cell(0, 0).text = "Заголовок таблицы"
    table.cell(1, 0).text = "Обычная ячейка"
    table._tbl.tblPr.find(
        "{http://schemas.openxmlformats.org/drawingml/2006/main}tableStyleId"
    ).text = "runtime-style"
    table.cell(1, 0).fill.solid()
    table.cell(1, 0).fill.fore_color.rgb = RGBColor.from_string("112233")
    for rel in prs.part.rels.values():
        if rel.reltype.endswith("/tableStyles"):
            rel.target_part._blob = b"""<a:tblStyleLst xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
              <a:tblStyle styleId="runtime-style"><a:wholeTbl>
                <a:tcStyle><a:fill><a:solidFill>
                  <a:srgbClr val="ABCDEF"/>
                </a:solidFill></a:fill></a:tcStyle>
              </a:wholeTbl><a:firstRow><a:tcTxStyle b="on">
                <a:font><a:latin typeface="Test Face"/></a:font>
                <a:srgbClr val="446688"/>
              </a:tcTxStyle></a:firstRow></a:tblStyle>
            </a:tblStyleLst>"""
    path = tmp_path / "table.pptx"
    prs.save(path)
    doc, _ = document(path)
    cells = doc.xpath("//td")
    assert "background:#ABCDEF" in cells[0].get("style")
    assert "background:#112233" in cells[1].get("style")
    run = cells[0].xpath(".//span")[0]
    assert "Test Face" in run.get("style")
    assert "font-weight:700" in run.get("style") and "#446688" in run.get("style")


def test_numbered_paragraphs_continue_until_list_ends(tmp_path):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    frame = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(3)).text_frame
    for i in range(3):
        p = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
        p.text = f"Шаг {i + 1}"
        bullet = OxmlElement("a:buAutoNum")
        bullet.set("type", "arabicPeriod")
        if i == 0:
            bullet.set("startAt", "4")
        p._p.get_or_add_pPr().append(bullet)
    path = tmp_path / "list.pptx"
    prs.save(path)
    doc, _ = document(path)
    assert doc.xpath("//ol/@start") == ["4", "5", "6"]


def test_potx_export_uses_same_object_renderer(deck, tmp_path):
    path = tmp_path / "template.potx"
    with zipfile.ZipFile(deck) as src, zipfile.ZipFile(path, "w") as dst:
        for name in src.namelist():
            payload = src.read(name)
            if name == "[Content_Types].xml":
                payload = payload.replace(
                    b"presentationml.presentation.main+xml", b"presentationml.template.main+xml"
                )
            dst.writestr(name, payload)
    doc, _ = document(path)
    assert doc.xpath('//span[text()="Текст <DOM> & данные"]')
    assert doc.xpath("//table")


def test_runtime_theme_fonts_colors_and_master_background(tmp_path):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = "Шрифт из темы"
    master = slide.slide_layout.slide_master
    for rel in master.part.rels.values():
        if rel.reltype.endswith("/theme"):
            root = etree.fromstring(rel.target_part.blob)
            ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
            root.find(".//a:majorFont/a:latin", ns).set("typeface", "Runtime Display Face")
            root.find(".//a:clrScheme/a:dk1/a:sysClr", ns).set("lastClr", "182838")
            rel.target_part._blob = etree.tostring(root)
    master.background.fill.solid()
    fill = master.background.fill._xPr.find(
        "{http://schemas.openxmlformats.org/drawingml/2006/main}solidFill"
    )
    fill.clear()
    scheme = OxmlElement("a:schemeClr")
    scheme.set("val", "dk1")
    fill.append(scheme)
    path = tmp_path / "theme.pptx"
    prs.save(path)
    doc, _ = document(path)
    assert "Runtime Display Face" in doc.xpath('//span[text()="Шрифт из темы"]')[0].get("style")
    assert "background:#182838" in doc.xpath('//div[@class="canvas"]')[0].get("style")


def test_table_grid_dimensions_override_stale_graphic_frame_extent(tmp_path):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    shape = slide.shapes.add_table(2, 2, Inches(1), Inches(1), Inches(4), Inches(2))
    shape.table.cell(0, 0).text = "Полная ширина таблицы"
    # Real PPTX tables can retain stale frame extents while grid widths and
    # row heights describe their final size (PowerPoint/LibreOffice use those).
    shape.width, shape.height = Inches(1), Inches(1)
    path = tmp_path / "table-grid.pptx"
    prs.save(path)
    doc, _ = document(path)
    style = doc.xpath("//table/..")[0].get("style")
    assert "width:40.00000%" in style
    assert "height:26.66667%" in style


def test_table_rows_can_grow_to_fit_paragraphs(tmp_path):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    table = slide.shapes.add_table(1, 1, Inches(1), Inches(1), Inches(4), Inches(0.1)).table
    table.cell(0, 0).text = "Первая строка\nВторая строка\nТретья строка"
    path = tmp_path / "minimum-row-height.pptx"
    prs.save(path)
    doc, files = document(path)
    assert len(doc.xpath("//td//p")) == 3
    # OOXML row height is a minimum. Table text must participate in layout,
    # otherwise an absolute frame clips it to .1 inches instead of growing.
    css = "".join(doc.xpath("//style/text()"))
    assert ".table .text-frame{position:relative;inset:auto;overflow:visible;" in css


def test_paragraph_line_box_matches_explicit_small_run_font(tmp_path):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    table = slide.shapes.add_table(1, 1, Inches(1), Inches(1), Inches(4), Inches(0.5)).table
    p = table.cell(0, 0).text_frame.paragraphs[0]
    p.text = "Мелкий текст не требует строки 18pt"
    p.runs[0].font.size = Pt(9)
    path = tmp_path / "font-strut.pptx"
    prs.save(path)
    doc, _ = document(path)
    paragraph = doc.xpath("//td//p")[0]
    styles = dict(prop.split(":", 1) for prop in paragraph.get("style").split(";"))
    assert styles["font-size"] == "1.66667cqh"


@pytest.mark.parametrize('label_color', ['FFFFFF', 'DAE7F3'])
def test_chart_explicit_text_style_survives_dark_background(tmp_path, label_color):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = RGBColor.from_string('07182A')
    data = CategoryChartData()
    data.categories = ['Первая категория', 'Вторая категория']
    data.add_series('Выручка', [3, 7])
    chart = slide.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(1), Inches(8), Inches(4), data,
    ).chart
    chart.font.color.rgb = RGBColor.from_string(label_color)
    chart.font.name = 'Runtime Chart Face'
    chart.font.size = Pt(14)
    path = tmp_path / 'dark-chart.pptx'
    prs.save(path)
    doc, _ = document(path)
    group = doc.xpath('//svg/g')[0]
    assert group.get('fill') == '#' + label_color
    assert 'Runtime Chart Face' in group.get('font-family')
    assert float(group.get('font-size')) == pytest.approx(800 * 1400 * 127 / Inches(8), abs=0.00005)
    assert 'Первая категория' in ''.join(group.itertext())


def test_dense_chart_axis_keeps_endpoints_without_label_collisions(tmp_path):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    data = CategoryChartData()
    data.categories = [f'Месяц {i:02d}' for i in range(1, 25)]
    data.add_series('Выручка', list(range(1, 25)))
    slide.shapes.add_chart(
        XL_CHART_TYPE.LINE, Inches(1), Inches(1), Inches(5), Inches(3), data,
    )
    path = tmp_path / 'dense-axis.pptx'
    prs.save(path)
    doc, _ = document(path)
    labels = doc.xpath('//svg/g/text[starts-with(text(), "Месяц")]')
    assert 2 <= len(labels) < 24
    assert labels[0].text == 'Месяц 01' and labels[-1].text == 'Месяц 24'
    font_size = float(labels[-1].getparent().get('font-size'))
    assert float(labels[-1].get('x')) + len(labels[-1].text) * font_size * 0.3 <= 800
    # Every data point and full category still exists in the accessible chart.
    assert len(doc.xpath('//svg/g/circle')) == 24
    assert all(f'Месяц {i:02d}=' in ''.join(doc.xpath('//svg/desc/text()')) for i in range(1, 25))


@pytest.mark.parametrize('orientation', ['minMax', 'maxMin'])
def test_horizontal_chart_labels_fit_and_respect_axis_direction(tmp_path, orientation):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    data = CategoryChartData()
    data.categories = ['Электроника', 'Книги']
    data.add_series('Выручка', [7, 3])
    chart = slide.shapes.add_chart(
        XL_CHART_TYPE.BAR_CLUSTERED, Inches(1), Inches(1), Inches(5), Inches(3), data,
    ).chart
    chart._chartSpace.xpath('.//c:catAx/c:scaling/c:orientation')[0].set('val', orientation)
    path = tmp_path / 'bar-axis.pptx'
    prs.save(path)
    doc, _ = document(path)
    first = doc.xpath('//svg/g/text[text()="Электроника"]')[0]
    second = doc.xpath('//svg/g/text[text()="Книги"]')[0]
    font_size = float(first.getparent().get('font-size'))
    assert float(first.get('x')) >= len(first.text) * font_size * 0.6
    if orientation == 'minMax':
        assert float(first.get('y')) > float(second.get('y'))
    else:
        assert float(first.get('y')) < float(second.get('y'))
    bars = doc.xpath('//svg/g/rect')
    assert len(bars) == 2
    assert (float(bars[0].get('y')) > float(bars[1].get('y'))) == (orientation == 'minMax')
