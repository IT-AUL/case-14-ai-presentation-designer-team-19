"""A rendered native pie must keep its percent callouts readable."""
import re
from io import BytesIO
from itertools import combinations
from pathlib import Path

import openpyxl
import pymupdf
import pytest
from deckdna.contracts.deck_plan import Brief
from deckdna.generation.pipeline import generate
from pptx import Presentation
from pptx.enum.chart import XL_CHART_TYPE

ROOT = Path(__file__).parents[2]


def test_compact_pie_keeps_data_and_complete_nonoverlapping_percentages(tmp_path):
    """5 каналов с долями ≈100% — законный pie; 14 регионов с хвостом
    «прочие» ~57% кругу не положены (там выбирается bar)."""
    report = generate(
        ROOT / 'tests/fixtures/pptx/vk_tech_template.pptx',
        ROOT / 'examples/stage2/channels_share.json',
        Brief(purpose='Структура продаж по каналам', audience='Руководство',
              language='ru', target_slide_count=8, content_fill='shrink'),
        tmp_path / 'out',
    )
    prs = Presentation(report['artifacts']['pptx'])
    pdf = pymupdf.open(report['artifacts']['pdf'])
    found = 0
    for index, slide in enumerate(prs.slides):
        for shape in slide.shapes:
            if not shape.has_chart or shape.chart.chart_type != XL_CHART_TYPE.PIE:
                continue
            found += 1
            # The source strip must not consume the chart readability budget.
            assert shape.width >= prs.slide_width * .4
            assert shape.height >= prs.slide_height * .4
            chart = shape.chart
            assert len(chart.series[0].values) == 5
            workbook = next(r.target_part.blob for r in chart.part.rels.values()
                            if r.reltype.endswith('/package'))
            book = openpyxl.load_workbook(BytesIO(workbook), read_only=True, data_only=True)
            rows = list(book.active.values)
            assert [row[0] for row in rows[1:]] == [c.label for c in chart.plots[0].categories]
            assert [row[1] for row in rows[1:]] == pytest.approx(list(chart.series[0].values))
            book.close()
            area = pymupdf.Rect(shape.left / 12700, shape.top / 12700,
                                (shape.left + shape.width) / 12700,
                                (shape.top + shape.height) / 12700)
            words = pdf[index].get_text('words', clip=area)
            labels = [w for w in words if re.fullmatch(r'\d+%', w[4])]
            # проценты читаемые: без вертикального стекинга цифр и наложений
            assert labels
            assert all((pymupdf.Rect(a[:4]) & pymupdf.Rect(b[:4])).is_empty
                       for a, b in combinations(labels, 2))
            text = re.sub(r'\s+', '', pdf[index].get_text())
            assert all(re.sub(r'\s+', '', c.label) in text for c in chart.plots[0].categories)
    assert found == 1
