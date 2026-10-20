"""Vision reads explicit labels, preserves token authority and page limits."""
import io

import fitz
import pytest
from deckdna.contracts.brandbook import Brandbook, BrandColor
from deckdna.template.brand_vision import (
    BrandReadings,
    analyze_brandbook,
    merge_readings,
    raster_pages,
)
from PIL import Image


def test_merge_requires_written_hex_and_font_label_and_keeps_tokens():
    book = Brandbook(id='b', colors=[BrandColor(name='token', hex='#123456', role='primary')])
    readings = BrandReadings.model_validate({
        'colors': [
            {'image': 1, 'quote': 'Primary #AABBCC', 'role': 'primary', 'hex': '#AABBCC'},
            {'image': 1, 'quote': 'accent is orange', 'role': 'accent', 'hex': '#EE9922'},
            {'image': 2, 'quote': 'Text #112233', 'role': 'text', 'hex': '#112233'},
            {'image': 1, 'quote': 'Secondary #ddeeff', 'role': 'secondary', 'hex': '#DDEEFF'},
        ],
        'fonts': [
            {'image': 1, 'quote': 'looks like Inter', 'role': 'heading', 'family': 'Inter'},
            {'image': 1, 'quote': 'Body font: Arial', 'role': 'body', 'family': 'Arial'},
        ],
    })
    result = merge_readings(book, readings, ['guide.png:page=1'])
    assert [(c.role, c.hex) for c in result.colors] == [
        ('primary', '#123456'), ('secondary', '#DDEEFF')]
    assert [(f.role, f.family) for f in result.fonts] == [('body', 'Arial')]
    assert 'page=1:vision:' in result.fonts[0].source
    assert len(book.colors) == 1 and not book.fonts


def test_pages_are_bounded_and_ordered():
    doc = fitz.open()
    for _ in range(5):
        doc.new_page(width=4000, height=2000)
    images, labels = raster_pages([('guide.pdf', doc.tobytes())])
    assert len(images) == 3 and labels[-1] == 'guide.pdf:page=3'
    import base64

    image = Image.open(io.BytesIO(base64.b64decode(images[0].split(',', 1)[1])))
    assert max(image.size) <= 1600


@pytest.mark.asyncio
async def test_analysis_sends_actual_raster_and_uses_vision_schema():
    data = io.BytesIO()
    Image.new('RGB', (100, 100), 'white').save(data, format='PNG')

    class Gateway:
        async def vision_json(self, name, images, payload, schema):
            assert name == 'brandbook_extract' and images[0].startswith('data:image/png;base64,')
            assert payload['pages'][0]['image'] == 1 and schema == BrandReadings
            return schema(colors=[], fonts=[])

    result, labels = await analyze_brandbook(Brandbook(id='b'), [('guide.png', data.getvalue())],
                                             Gateway())
    assert not result.colors and not result.fonts and len(labels) == 1
