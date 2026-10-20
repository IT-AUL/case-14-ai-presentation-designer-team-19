"""Chart plus source note must clear runtime layout/master branding."""
from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile

import pytest
from deckdna.contracts.content_pack import Chart, ChartSeries, SourceRef
from deckdna.pptx.composing.chart_fill import fill_slide_charts
from deckdna.pptx.opc.package import OpcPackage, parse_rels, rels_name_for
from lxml import etree
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches

S = 'ppt/slides/slide1.xml'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
P = 'http://schemas.openxmlformats.org/presentationml/2006/main'


@pytest.mark.parametrize('owner', ['layout', 'master'])
def test_chart_and_source_clear_inherited_branding_without_mutating_it(owner):
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(10), Inches(6)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    # A cleared donor body looks free locally, but extends through the footer.
    slide.shapes.add_textbox(Inches(.5), Inches(1), Inches(9), Inches(4.7))
    logo = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(.4), Inches(5.3),
                                  Inches(3), Inches(.5))
    logo.text = 'Runtime brand'
    container = slide.slide_layout if owner == 'layout' else slide.slide_layout.slide_master
    container.shapes._spTree.append(deepcopy(logo._element))
    slide.shapes._spTree.remove(logo._element)
    stream = BytesIO()
    prs.save(stream)
    with ZipFile(stream) as z:
        pkg = OpcPackage({n: z.read(n) for n in z.namelist()})
    branding = {n: v for n, v in pkg.parts.items()
                if n.startswith(('ppt/slideLayouts/', 'ppt/slideMasters/'))}
    chart = Chart(id='sample', chart_type='bar', categories=['A', 'B'],
                  series=[ChartSeries(name='Value', values=[3, 5])],
                  source_ref=SourceRef(artifact_id='data.csv'), source_note='Источник: data.csv')
    report = fill_slide_charts(pkg.parts, S, [chart], parse_rels(pkg.parts[rels_name_for(S)]),
                               set(), used_hosts=set())
    assert report.charts_filled == 1 and report.units_dropped == 0
    assert all(pkg.parts[n] == data for n,data in branding.items())
    root = etree.fromstring(pkg.parts[S])
    logo_box = (.4, 5.3, 3, .5)
    frames = root.findall(f'.//{{{P}}}graphicFrame')
    notes = [sp for sp in root.findall(f'.//{{{P}}}sp')
             if 'Источник:' in ''.join(sp.itertext())]
    assert len(frames) == len(notes) == 1
    for shape in frames + notes:
        xfrm = shape.find(f'{{{P}}}xfrm')
        if xfrm is None:
            xfrm = shape.find(f'{{{P}}}spPr/{{{A}}}xfrm')
        off, ext = xfrm.find(f'{{{A}}}off'), xfrm.find(f'{{{A}}}ext')
        x,y,w,h = [int(v)/914400 for v in
                   (off.get('x'), off.get('y'), ext.get('cx'), ext.get('cy'))]
        lx,ly,lw,lh = logo_box
        assert min(x+w,lx+lw) <= max(x,lx) or min(y+h,ly+lh) <= max(y,ly)
