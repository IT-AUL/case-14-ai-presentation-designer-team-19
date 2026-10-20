"""Live regressions: ample-space clipping, equation loss, and arbitrary theme."""
import pytest
from deckdna.audit.basic import _wrap_metrics
from deckdna.errors import DeckDNAError
from deckdna.pptx.composing.sources_layout import (
    _source_entry,
    compose_dense_prose,
    compose_visual_cards,
    paginate_sources,
)
from lxml import etree
from pptx import Presentation

A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
P = '{http://schemas.openxmlformats.org/presentationml/2006/main}'


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


A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


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


def test_sources_that_cannot_fit_keep_full_text_at_floor_instead_of_failing():
    """Раньше одна неподходящая запись роняла всю генерацию
    (composition_failed). Теперь раскладка деградирует: две колонки →
    одна → общий блок на 8 pt; текст не теряется, переполнение остаётся
    измеримым для аудита, а колода собирается."""
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    entry = "Очень длинная библиографическая запись " * 80
    xml, _shortened = compose_dense_prose(
        etree.tostring(slide._element),
        "Источники",
        [entry] * 4,
        int(prs.slide_width),
        int(prs.slide_height),
    )
    root = etree.fromstring(xml)
    text = "".join(t.text or "" for t in root.iter(f"{{{A_NS}}}t"))
    assert text.count("Очень длинная библиографическая запись") == 80 * 4
    sizes = {int(r.get("sz")) for r in root.iter(f"{{{A_NS}}}rPr") if r.get("sz")}
    assert 800 in sizes


def test_sources_paginate_without_losing_entries_or_numbering():
    prs = Presentation()
    prs.slide_width, prs.slide_height = 9_144_000, 5_143_500
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    entries = [
        f"Автор {i}. Обзор результатов и ограничений исследования, 2024. DOI: 10.1000/{i}"
        for i in range(1, 31)
    ]
    pages = paginate_sources(
        etree.tostring(slide._element), "Источники", entries,
        int(prs.slide_width), int(prs.slide_height),
        continuation_title="Источники — продолжение",
    )
    assert len(pages) > 1
    assert sum(count for _, count in pages) == len(entries)
    rendered = []
    for number, (xml, count) in enumerate(pages, 1):
        root = etree.fromstring(xml)
        shapes_by_name = {}
        for shape in root.iter(P + 'sp'):
            name = shape.find(P + 'nvSpPr/' + P + 'cNvPr')
            if name is not None:
                shapes_by_name[name.get('name')] = shape
        title_shape = shapes_by_name['DeckDNA bibliography title']
        title = ''.join(t.text or '' for t in title_shape.iter(A + 't'))
        assert title == ("Источники" if number == 1 else "Источники — продолжение")
        source_shapes = [
            shape for name, shape in shapes_by_name.items()
            if (name or '').startswith('DeckDNA source ')
        ]
        assert len(source_shapes) == count
        for shape in source_shapes:
            rendered.append("".join(t.text or "" for t in shape.iter(A + "t")))
            transform = shape.find(P + 'spPr/' + A + 'xfrm')
            off, ext = transform.find(A + "off"), transform.find(A + "ext")
            assert int(off.get("y")) + int(ext.get("cy")) <= int(prs.slide_height * .905)
    assert rendered == [f"{i}. {entry}" for i, entry in enumerate(entries, 1)]


def test_sources_paginate_rejects_single_unreadable_entry():
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    with pytest.raises(DeckDNAError, match="source entry does not fit"):
        paginate_sources(
            etree.tostring(slide._element), "Источники",
            ["Очень длинная библиографическая запись " * 200],
            int(prs.slide_width), int(prs.slide_height),
        )


def test_data_split_leaves_room_for_native_chart_or_table():
    """Образец без рамки данных и без места: своя раскладка «текст слева»
    оставляет справа свободный прямоугольник не меньше минимума графика —
    график/таблица встают туда, а не отбрасываются."""
    from deckdna.pptx.composing.minimal import _needs_data_room
    from deckdna.pptx.composing.sources_layout import compose_data_split
    from deckdna.pptx.composing.table_fill import _free_space_host
    from pptx.util import Emu

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    w, h = int(prs.slide_width), int(prs.slide_height)
    for i in range(4):  # плотная сетка заполненных карточек донора
        box = slide.shapes.add_textbox(Emu(int(w*.05 + i*w*.23)), Emu(int(h*.3)),
                                       Emu(int(w*.2)), Emu(int(h*.6)))
        box.text_frame.text = f"Пункт {i+1} с текстом карточки"
    xml = etree.tostring(slide._element)
    assert _needs_data_room(xml, xml, False, True, w, h)
    out = compose_data_split(
        xml, "Рост пользователей", ["Пилот: 420 человек", "Адаптация: 61 день"], w, h
    )
    root = etree.fromstring(out)
    texts = "".join(t.text or "" for t in root.iter(A + "t"))
    assert "Пилот: 420 человек" in texts and "Рост пользователей" in texts
    free = _free_space_host(root, w, h)
    assert free is not None and free[3] >= 2_000_000 and free[4] >= 1_500_000
    assert free[1] >= int(w * .4)  # справа от текстовой колонки
    assert not _needs_data_room(xml, out, False, True, w, h)
