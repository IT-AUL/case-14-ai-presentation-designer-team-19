"""Бюджет текста из геометрии эталона и его применение до компоновки."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from deckdna.contracts.deck_plan import Brief, Kind
from deckdna.ingestion.content_parsers import parse_file
from deckdna.planning.story_director import plan_deck
from deckdna.pptx.composing.card_reflow import _Box, _box, _Card, _expand_bodies
from deckdna.pptx.composing.orphans import remove_orphans
from deckdna.pptx.composing.text_budget import (
    chars_that_fit,
    slide_text_budget,
    template_budgets,
)
from deckdna.pptx.fitting.budget_fit import PROMPT_NAME, can_rewrite, fit_plan_to_budgets
from deckdna.providers.mock import MockProvider
from lxml import etree

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
EMU_IN = 914400
TEMPLATE = Path("tests/fixtures/pptx/vk_tech_template.pptx")
needs_template = pytest.mark.skipif(not TEMPLATE.exists(), reason="organizer fixture missing")


def _sp(sid, x, y, w, h, text="", *, fill=False, title=False, sz=None):
    ph = '<p:ph type="title"/>' if title else ""
    fill_xml = '<a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill>' if fill else "<a:noFill/>"
    rpr = f'<a:rPr sz="{int(sz * 100)}"/>' if sz else ""
    body = (
        f'<p:txBody><a:bodyPr/><a:p><a:r>{rpr}<a:t>{text}</a:t></a:r></a:p></p:txBody>'
        if text is not None
        else ""
    )
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="S{sid}"/><p:cNvSpPr/><p:nvPr>{ph}</p:nvPr>'
        f'</p:nvSpPr><p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{w}" cy="{h}"/>'
        f'</a:xfrm><a:prstGeom prst="rect"/>{fill_xml}</p:spPr>{body}</p:sp>'
    )


def _slide(*shapes: str) -> bytes:
    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:sld xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree>'
        f'<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        f'<p:grpSpPr/>{"".join(shapes)}</p:spTree></p:cSld></p:sld>'
    ).encode()


# ---------------------------------------------------------------- budget


def test_chars_that_fit_grows_with_box_and_shrinks_with_font():
    small = chars_that_fit(2 * EMU_IN, 1 * EMU_IN, 12)
    big = chars_that_fit(4 * EMU_IN, 2 * EMU_IN, 12)
    larger_font = chars_that_fit(2 * EMU_IN, 1 * EMU_IN, 18)
    assert 0 < larger_font < small < big


def test_title_budget_is_two_lines_even_in_a_one_line_frame():
    one_line = chars_that_fit(7 * EMU_IN, 10**9, 24, 1)
    xml = _slide(_sp(2, 0, 0, 7 * EMU_IN, int(0.45 * EMU_IN), "Заголовок", title=True, sz=24))
    budget = slide_text_budget(xml)
    assert budget["title_max_chars"] > one_line * 1.6


def test_card_headings_flag_follows_slots_per_card():
    xml = _slide(_sp(3, 0, EMU_IN, 3 * EMU_IN, EMU_IN, "Текст карточки", sz=9))
    assert slide_text_budget(xml, slots_per_card=2)["card_headings"] is True
    assert slide_text_budget(xml, slots_per_card=1)["card_headings"] is False


@needs_template
def test_template_budgets_cover_every_slide():
    budgets = template_budgets(TEMPLATE)
    assert len(budgets) > 20
    titles = [b["title_max_chars"] for b in budgets.values() if b["title_max_chars"]]
    assert titles and all(24 <= t <= 90 for t in titles)


# ------------------------------------------------------------ budget fit


def _plan():
    pack = parse_file(Path("tests/fixtures/content/poc_article.md"))
    return plan_deck(pack, Brief(purpose="t", audience="q", language="ru", target_slide_count=10))


def test_mock_without_fixture_cannot_rewrite():
    assert not can_rewrite(MockProvider())
    assert can_rewrite(MockProvider(fixtures={PROMPT_NAME: {"title": "x", "bullets": []}}))


def test_budget_fit_shortens_only_over_budget_slides_and_keeps_numbers():
    plan = _plan()
    target = next(
        i for i, s in enumerate(plan.slides)
        if len([u for u in s.content_units if u.kind == Kind.bullet and u.text]) >= 2
    )
    parts = [f"p{i}" for i in range(len(plan.slides))]
    budgets = {p: {"title_max_chars": 500, "bullet_max_chars": 500} for p in parts}
    budgets[parts[target]] = {"title_max_chars": 10, "bullet_max_chars": 10}
    seen: list[dict] = []

    def fixture(payload: dict) -> dict:
        seen.append(payload)
        return {
            "title": "Короткий вывод",
            "bullets": [f"Пункт {k + 1} кратко" for k in range(len(payload["bullets"]))],
        }

    gw = MockProvider(fixtures={PROMPT_NAME: fixture})
    new_plan, report = asyncio.run(fit_plan_to_budgets(plan, parts, budgets, gw))
    assert report.slides_over_budget == 1 and len(seen) == 1
    assert new_plan.slides[target].title_intent == "Короткий вывод"
    others = [i for i in range(len(plan.slides)) if i != target]
    assert all(new_plan.slides[i] == plan.slides[i] for i in others)


def test_budget_fit_rejects_invented_numbers_and_fragments():
    plan = _plan()
    parts = [f"p{i}" for i in range(len(plan.slides))]
    budgets = {p: {"title_max_chars": 5, "bullet_max_chars": 5} for p in parts}

    def fixture(payload: dict) -> dict:
        return {"title": "рост на 99%", "bullets": ["…"] * len(payload["bullets"])}

    gw = MockProvider(fixtures={PROMPT_NAME: fixture})
    new_plan, report = asyncio.run(fit_plan_to_budgets(plan, parts, budgets, gw))
    assert report.items_rewritten == 0
    assert [s.title_intent for s in new_plan.slides] == [s.title_intent for s in plan.slides]


# -------------------------------------------------------- card body growth


def test_card_body_grows_up_into_invisible_empty_heading_slot():
    card = etree.fromstring(_slide(_sp(10, 0, 0, 2 * EMU_IN, 2 * EMU_IN, None, fill=True)))
    root = etree.fromstring(
        _slide(
            _sp(11, 100000, 100000, 80000, 80000, None, fill=True),  # маркер-точка
            _sp(12, 100000, 100000, 1700000, 300000, ""),  # пустой заголовок
            _sp(13, 100000, 900000, 1700000, 800000, "Текст карточки"),
        )
    )
    tree = root.find(f"{{{P}}}cSld/{{{P}}}spTree")
    members = [(el, _box(el)) for el in tree if el.tag == f"{{{P}}}sp"]
    container = card.find(f".//{{{P}}}sp")
    grown = _expand_bodies([_Card(container, _Box(0, 0, 2 * EMU_IN, 2 * EMU_IN), members)])
    body = _box(members[-1][0])
    assert grown == 1
    assert 180000 < body.y < 900000  # под точкой, выше прежнего верха


# ------------------------------------------------------------------ orphans


def test_orphan_avatar_next_to_cleared_name_is_removed():
    before = _slide(
        _sp(2, 0, 0, 6 * EMU_IN, EMU_IN, "Спасибо", title=True),
        _sp(3, int(1.6 * EMU_IN), 4 * EMU_IN, 3 * EMU_IN, 300000, "Имя Фамилия"),
        _sp(4, int(0.7 * EMU_IN), 4 * EMU_IN, int(0.7 * EMU_IN), int(0.7 * EMU_IN), None,
            fill=True),
        _sp(5, 8 * EMU_IN, 0, int(0.5 * EMU_IN), int(0.5 * EMU_IN), None, fill=True),
    )
    after = before.replace("Имя Фамилия".encode(), b"")
    out, removed = remove_orphans(before, after, 10 * EMU_IN, int(5.6 * EMU_IN))
    ids = {el.get("id") for el in etree.fromstring(out).iter(f"{{{P}}}cNvPr")}
    assert removed == 1
    assert "4" not in ids and "5" in ids  # аватар ушёл, далёкий декор остался


def test_orphans_untouched_when_nothing_cleared():
    xml = _slide(_sp(3, 0, 0, EMU_IN, EMU_IN, "Текст"))
    assert remove_orphans(xml, xml, 10 * EMU_IN, 5 * EMU_IN) == (xml, 0)


# ------------------------------------------------------------ title frame


def test_title_frame_grows_into_free_space_before_font_shrinks():
    from deckdna.pptx.composing.text_replace import replace_text_runs

    xml = _slide(
        _sp(2, 0, 0, 7 * EMU_IN, int(0.45 * EMU_IN), "Заголовок", title=True, sz=24),
        _sp(3, 0, int(1.6 * EMU_IN), 7 * EMU_IN, EMU_IN, "Текст"),
    )
    title = "Ролевая игра развивает навыки переговоров и решений"
    out, report = replace_text_runs(xml, ["Текст"], title_text=title)
    root = etree.fromstring(out)
    sp = next(el for el in root.iter(f"{{{P}}}sp") if el.find(f".//{{{P}}}ph") is not None)
    cy = int(sp.find(f".//{{{A}}}ext").get("cy"))
    assert cy > int(0.45 * EMU_IN)  # рамка выросла
    assert cy <= int(1.6 * EMU_IN)  # но не залезла на текст под ней
    assert report.bodies_shrunk == 0  # кегль 24 сохранён


def test_gray_photo_stub_is_removed_colored_decor_kept():
    def circle(sid, x, color):
        return (
            f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="c{sid}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
            f'<p:spPr><a:xfrm><a:off x="{x}" y="0"/><a:ext cx="{EMU_IN}" cy="{EMU_IN}"/></a:xfrm>'
            f'<a:prstGeom prst="ellipse"/><a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'
            f"</p:spPr></p:sp>"
        )

    xml = _slide(circle(4, 0, "7C8A9A"), circle(5, 3 * EMU_IN, "0077FF"),
                 _sp(6, 5 * EMU_IN, 0, 2 * EMU_IN, EMU_IN, "Текст"))
    out, removed = remove_orphans(xml, xml, 10 * EMU_IN, int(5.6 * EMU_IN))
    ids = {el.get("id") for el in etree.fromstring(out).iter(f"{{{P}}}cNvPr")}
    assert removed == 1 and "4" not in ids and "5" in ids


def test_usable_height_stops_at_caption_frame_inside():
    from deckdna.pptx.composing.text_replace import _usable_box_emu

    root = etree.fromstring(_slide(
        _sp(2, 0, 0, 4 * EMU_IN, 4 * EMU_IN, "Основной текст карточки"),
        _sp(3, int(0.2 * EMU_IN), 2 * EMU_IN, int(3.5 * EMU_IN), int(0.8 * EMU_IN), "Подпись"),
    ))
    body = next(root.iter(f"{{{P}}}txBody"))
    _w, h = _usable_box_emu(body)
    assert h < 2 * EMU_IN  # не до низа карточки, а до подписи
