"""Live regressions: ample-space clipping, equation loss, and arbitrary theme."""
import pytest
from deckdna.audit.basic import _wrap_metrics
from deckdna.errors import DeckDNAError
from deckdna.pptx.composing.sources_layout import (
    _source_entry,
    compose_dense_prose,
    compose_visual_cards,
)
from lxml import etree
from pptx import Presentation

A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        ('См frontiersin.org', 'frontiersin.org'),
        ('Rhythms of relief: есть frontiersin.org', 'Rhythms of relief: frontiersin.org'),
        ('Digital 1:есть strathprints.strath.ac.uk', 'Digital 1: strathprints.strath.ac.uk'),
        ('Источники включают pubmed.ncbi.nlm.nih.gov', 'pubmed.ncbi.nlm.nih.gov'),
    ],
)
def test_source_entry_removes_weak_model_markers(raw, expected):
    assert _source_entry(raw) == expected


@pytest.mark.parametrize('cards', [True, False])
@pytest.mark.parametrize('count', [1, 2, 4, 6])
def test_native_fallback_keeps_complete_authored_text(cards, count):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    entry = ('В исследовании сравнили состояние участников до и после сеанса, '
             'сохранив одинаковые условия проведения и контрольную группу. '
             'Результат требует независимой проверки и не подтверждает клиническую эффективность.')
    entries = [f'{i+1}. {entry}' for i in range(count)]
    xml, omitted = compose_visual_cards(
        etree.tostring(slide._element), 'Полный вывод', entries,
        int(prs.slide_width), int(prs.slide_height), cards=cards,
    )
    root = etree.fromstring(xml)
    texts = [t.text for t in root.iter(A+'t')]
    assert omitted == 0
    assert texts == ['Полный вывод', *entries]
    assert not list(root.iter(A+'srgbClr'))  # colors stay in the donor's arbitrary theme


def test_dense_fallback_keeps_formula_and_qualifications():
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    entries = ['Focus Score = 0.4 × Accuracy + 0.6 × Stability',
               'Это нормированный показатель для сравнения с собственной базовой линией; '
               'он не является диагнозом и не используется для клинических решений.']
    xml, omitted = compose_dense_prose(etree.tostring(slide._element), 'Формула', entries,
                                      int(prs.slide_width), int(prs.slide_height))
    joined = '\n'.join(t.text for t in etree.fromstring(xml).iter(A+'t'))
    assert omitted == 0
    assert all(entry in joined for entry in entries)


def test_dense_prose_balances_columns_by_wrapped_height():
    prs = Presentation()
    prs.slide_width, prs.slide_height = 9_144_000, 5_143_500
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    width = int(prs.slide_width * .425)

    def entry_with_lines(lines: int) -> str:
        for count in range(1, 120):
            text = "Проверка факта и условия применения " * count
            if _wrap_metrics("• " + text, 8.0, width)[0] == lines:
                return text.strip()
        raise AssertionError(f"no synthetic entry with {lines} lines")

    line_counts = [2, 1, 2, 1, 2, 2, 2, 2, 2, 2, 4, 2, 2, 2, 2, 6, 2, 2]
    entries = [entry_with_lines(lines) for lines in line_counts]
    xml, omitted = compose_dense_prose(
        etree.tostring(slide._element), "Метрики и ограничения", entries,
        int(prs.slide_width), int(prs.slide_height),
    )
    assert omitted == 0
    root = etree.fromstring(xml)
    P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
    columns: dict[int, list[tuple[int, int]]] = {0: [], 1: []}
    rendered = []
    for shape in root.iter(P + "sp"):
        name = shape.find(P + "nvSpPr/" + P + "cNvPr")
        if name is None or not (name.get("name") or "").startswith("DeckDNA source"):
            continue
        transform = shape.find(P + "spPr/" + A + "xfrm")
        off, ext = transform.find(A + "off"), transform.find(A + "ext")
        x, y, h = int(off.get("x")), int(off.get("y")), int(ext.get("cy"))
        columns[int(x > prs.slide_width / 2)].append((y, y + h))
        rendered.append("".join(t.text or "" for t in shape.iter(A + "t")))
        assert y + h <= int(prs.slide_height * .905)
    assert rendered == ["• " + entry for entry in entries]
    assert len(columns[0]) > len(columns[1])  # longer entries move to the right
    for frames in columns.values():
        assert all(
            next_start >= end
            for (_, end), (next_start, _) in zip(frames, frames[1:], strict=False)
        )


def test_sources_reject_entry_that_cannot_fit_readability_floor():
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    entry = "Очень длинная библиографическая запись " * 80
    with pytest.raises(DeckDNAError, match="paginate the sources slide") as exc:
        compose_dense_prose(
            etree.tostring(slide._element),
            "Источники",
            [entry] * 4,
            int(prs.slide_width),
            int(prs.slide_height),
        )
    assert exc.value.code == "composition_failed"
    assert exc.value.stage == "composing.sources"
