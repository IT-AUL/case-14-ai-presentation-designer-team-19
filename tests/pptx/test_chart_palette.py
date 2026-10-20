"""A narrow runtime palette must not reuse pie sectors or HTML legend keys."""
from io import BytesIO
from itertools import combinations
from zipfile import ZipFile

import pytest
from deckdna.contracts.content_pack import Chart, ChartSeries, SourceRef
from deckdna.pptx.composing.chart_colors import contrast
from deckdna.pptx.composing.chart_fill import fill_slide_charts
from deckdna.pptx.opc.package import OpcPackage, parse_rels, rels_name_for
from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Inches

A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
C = 'http://schemas.openxmlformats.org/drawingml/2006/chart'
S = 'ppt/slides/slide1.xml'


@pytest.mark.parametrize('background', ['FFFFFF', '000000'])
@pytest.mark.parametrize('palette', [('0077FF', 'FF308D', '202020'), ('777777',)])
def test_six_categories_have_distinct_contrasting_colors_and_matching_html(background, palette):
    from deckdna.pptx.exporting.html_charts import chart_svg
    from deckdna.pptx.exporting.html_style import Theme

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = RGBColor.from_string(background)
    stream = BytesIO()
    prs.save(stream)
    with ZipFile(stream) as z:
        pkg = OpcPackage({n: z.read(n) for n in z.namelist()})
    theme_name = next(n for n in pkg.parts if n.startswith('ppt/theme/') and n.endswith('.xml'))
    theme = etree.fromstring(pkg.parts[theme_name])
    for i in range(1, 7):
        accent = theme.find(f'.//{{{A}}}accent{i}')
        for child in list(accent):
            accent.remove(child)
        etree.SubElement(accent, f'{{{A}}}srgbClr', val=palette[(i-1) % len(palette)])
    pkg.parts[theme_name] = etree.tostring(theme)
    chart = Chart(id='shares', chart_type='pie', categories=[f'Category {i}' for i in range(6)],
                  series=[ChartSeries(name='Value', values=[20, 12, 10, 9, 7, 42])],
                  source_ref=SourceRef(artifact_id='values.csv'))
    report = fill_slide_charts(pkg.parts, S, [chart], parse_rels(pkg.parts[rels_name_for(S)]),
                               set(), used_hosts=set())
    assert report.charts_filled == 1 and report.units_dropped == 0
    raw = next(v for n, v in pkg.parts.items() if n.startswith('ppt/charts/chart'))
    colors = etree.fromstring(raw).xpath('//c:dPt/c:spPr/a:solidFill/a:srgbClr/@val',
                                         namespaces={'c': C, 'a': A})
    assert len(colors) == len(set(colors)) == 6
    assert all(contrast(c, background) >= 1.6 for c in colors)
    def rgb(c):
        return tuple(int(c[i:i+2], 16) for i in (0, 2, 4))
    assert min(sum((a-b)**2 for a,b in zip(rgb(x), rgb(y), strict=True)) ** .5
               for x,y in combinations(colors, 2)) >= 30
    # Same input gives byte-identical chart styles, independent of random run ids.
    again = fill_slide_charts(pkg.parts, S, [chart], parse_rels(pkg.parts[rels_name_for(S)]),
                              set(), used_hosts=set())
    assert again.charts_filled == 1
    assert pkg.parts[next(n for n in pkg.parts if n.startswith('ppt/charts/chart'))] == raw
    output = BytesIO()
    with ZipFile(output, 'w') as z:
        for n, data in pkg.parts.items():
            z.writestr(n, data)
    rendered = Presentation(output).slides[0]
    native = next(s for s in rendered.shapes if s.has_chart)
    svg = etree.fromstring(chart_svg(native.chart, Theme(rendered), Inches(8), Inches(4)).encode())
    ns = {'s': 'http://www.w3.org/2000/svg'}
    assert svg.xpath('//s:path/@fill', namespaces=ns) == ['#' + c for c in colors]
    assert svg.xpath('//s:rect/@fill', namespaces=ns) == ['#' + c for c in colors]
    assert len(native.chart.series[0].values) == 6
