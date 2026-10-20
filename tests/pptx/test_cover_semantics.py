"""Cover copy follows semantic geometry, including manually drawn templates."""

from deckdna.contracts.deck_plan import Brief, ContentUnit, Kind, Origin
from deckdna.ingestion.content_parsers import parse_markdown
from deckdna.planning.story_director import plan_deck
from deckdna.pptx.composing.minimal import _slide_texts, generate_deck
from pptx import Presentation
from pptx.util import Inches, Pt

TOPIC = "Итоги продаж за год у Mvideo"


def test_manual_cover_replaces_stock_kicker_and_preserves_title_hierarchy(tmp_path):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    # Put kicker first in XML to reproduce the original positional-pour bug.
    for y, height, size, text in [
        (0.4, 0.4, 14, "Идея"),
        (1.5, 1.5, 40, "Название презентации"),
        (3.4, 0.7, 20, "Стоковый подзаголовок"),
    ]:
        tb = slide.shapes.add_textbox(Inches(0.6), Inches(y), Inches(8.5), Inches(height))
        tb.text = text
        tb.text_frame.paragraphs[0].runs[0].font.size = Pt(size)
    template = tmp_path / "manual-cover.pptx"
    prs.save(template)
    plan = plan_deck(
        parse_markdown(f"# Идея\n\n{TOPIC}."),
        Brief(
            purpose=TOPIC,
            audience="руководство",
            language="ru",
            key_message="Отчётный период: 2025 год",
            target_slide_count=3,
        ),
    )
    output = tmp_path / "result.pptx"
    generate_deck(template, plan, output)
    shapes = [sh for sh in Presentation(output).slides[0].shapes if sh.has_text_frame]
    texts = [sh.text.strip() for sh in shapes]
    assert "Идея" not in texts
    assert "Название презентации" not in texts
    assert texts.count(TOPIC) == 1
    title = next(sh for sh in shapes if sh.text.strip() == TOPIC)
    subtitle = next(sh for sh in shapes if sh.text.strip() == "Отчётный период: 2025 год")
    kicker = next(sh for sh in shapes if sh.text.strip() == "ГОДОВОЙ ОТЧЕТ")
    assert kicker.top < title.top < subtitle.top
    assert title.text_frame.paragraphs[0].runs[0].font.size.pt >= 28


def test_model_origin_does_not_add_assumption_prefix_back_to_title():
    plan = plan_deck(
        parse_markdown("# Рынок\n\nРазбор спроса и категорий."),
        Brief(purpose=TOPIC, audience="руководство", language="ru", target_slide_count=3),
    )
    slide = plan.slides[1].model_copy(
        update={
            "title_intent": "Рынок стабилизируется",
            "content_units": [
                ContentUnit(
                    role="title", kind=Kind.title, text="Рынок стабилизируется", origin=Origin.model
                ),
                ContentUnit(
                    role="body", kind=Kind.bullet, text="Проверить спрос", origin=Origin.model
                ),
            ],
        }
    )
    title, texts, *_ = _slide_texts(slide, language="ru")
    assert title == "Рынок стабилизируется"
    assert not any("Предположение:" in text for text in texts)


def test_cover_uses_topic_and_period_instead_of_service_heading():
    pack = parse_markdown(f"# Идея\n\n{TOPIC}.")
    req = Brief(
        purpose=TOPIC,
        audience="руководство",
        language="ru",
        target_slide_count=10,
        content_fill="expand",
    )
    plan = plan_deck(pack, req)
    cover = plan.slides[0]
    assert cover.title_intent == TOPIC
    assert next(u.text for u in cover.content_units if u.kind == Kind.title) == TOPIC
    assert any(u.role == "eyebrow" and u.text == "ГОДОВОЙ ОТЧЕТ" for u in cover.content_units)
    assert any(
        u.kind == Kind.subtitle and "год" in u.text.casefold()
        for u in cover.content_units
        if u.role != "eyebrow"
    )


def test_conclusion_title_cannot_reintroduce_assumption_prefix():
    pack = parse_markdown(
        "# Обзор\n\n## Рынок\n\n"
        "Предположение: Рынок электроники стабилизируется после спада. "
        "Команда проверит динамику спроса.\n\n"
        "- Потребуется сопоставить категории продаж и остатки."
    )
    req = Brief(
        purpose=TOPIC,
        audience="руководство",
        language="ru",
        target_slide_count=6,
        content_fill="expand",
    )
    plan = plan_deck(pack, req)
    assert any("Рынок электроники" in s.title_intent for s in plan.slides)
    assert all(not s.title_intent.casefold().startswith("предположение:") for s in plan.slides)


def test_data_story_cleans_assumption_prefix_from_cover():
    from deckdna.planning.data_story import cover_title_text

    assert cover_title_text("Предположение: Итоги продаж за год") == "Итоги продаж за год"


async def test_topic_only_brief_compiles_without_fabricated_data_or_donor_closing_credits(tmp_path):
    from deckdna.planning.content_fill import annotate_unit_origins
    from deckdna.pptx.opc.package import OpcPackage
    from deckdna.research.config import ResearchConfig
    from deckdna.research.fill import fill_content
    from lxml import etree
    from pptx.enum.shapes import MSO_SHAPE

    prs = Presentation()
    s0 = prs.slides.add_slide(prs.slide_layouts[0])
    s0.shapes.title.text = "Заголовок шаблона"
    s0.placeholders[1].text = "Подзаголовок шаблона"

    s1 = prs.slides.add_slide(prs.slide_layouts[1])
    s1.shapes.title.text = "Контентный слайд"
    s1.placeholders[1].text = "Текст шаблона для замены"

    s2 = prs.slides.add_slide(prs.slide_layouts[1])
    s2.shapes.title.text = "Thanks!"
    s2.placeholders[
        1
    ].text = "Do you have any questions? youremail@freepik.com +34 654 321 432 yourwebsite.com"
    attr_box = s2.shapes.add_textbox(Inches(0.8), Inches(5.8), Inches(4.5), Inches(0.4))
    attr_box.text = "Please keep this slide for attribution"
    for idx in range(3):
        icon = s2.shapes.add_shape(
            MSO_SHAPE.OVAL, Inches(0.8 + idx * 0.6), Inches(4.2), Inches(0.38), Inches(0.38)
        )
        icon.name = f"Social Icon {idx + 1}"
    layout_sp_tree = s2.slide_layout._element.find(
        ".//{http://schemas.openxmlformats.org/presentationml/2006/main}spTree"
    )
    layout_sp_tree.append(
        etree.fromstring(
            '<p:sp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
            ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<p:nvSpPr><p:cNvPr id="735" name="Layout Credit"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
            '<p:spPr><a:xfrm><a:off x="731520" y="4389120"/>'
            '<a:ext cx="4572000" cy="548640"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
            "<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>"
            "CREDITS: This presentation template was created by Slidesgo, "
            "and includes icons by Flaticon, and infographics &amp; images by Freepik"
            "</a:t></a:r></a:p></p:txBody></p:sp>"
        )
    )
    template = tmp_path / "slidesgo-like.pptx"
    prs.save(template)

    brief_text = "DNS - аналитика компании"
    request = Brief(
        purpose=brief_text,
        audience="Совет директоров",
        language="ru",
        target_slide_count=11,
        content_fill="expand",
    )
    result = await fill_content(
        parse_markdown(f"# Идея\n\n{brief_text}."),
        request,
        None,
        research_config=ResearchConfig(),
    )
    assert not result.pack.charts
    assert not result.pack.tables
    outcome = result.to_dict()
    assert outcome["honest_status"] == "missing_data"
    assert outcome["built_slides"] < 11
    assert outcome["shortfall_slides"] > 0
    assert len(outcome["missing_data_questions"]) >= 3

    plan = annotate_unit_origins(plan_deck(result.pack, result.brief), result.pack)
    assert not any(s.purpose.value == "section_divider" for s in plan.slides)
    assert any("Каких данных не хватает" in s.title_intent for s in plan.slides)

    output = tmp_path / "honest.pptx"
    generate_deck(
        template,
        plan,
        output,
        exemplar_overrides={plan.slides[-1].id: "ppt/slides/slide3.xml"},
    )
    rendered = Presentation(output)
    all_slide_texts = [
        sh.text.strip()
        for slide in rendered.slides
        for sh in slide.shapes
        if sh.has_text_frame and sh.text.strip()
    ]
    joined = " ".join(all_slide_texts)
    assert "Направление A" not in joined
    assert "усл. ед" not in joined.casefold()
    assert "условных единиц" not in joined.casefold()
    assert "демонстрационный сценарий" not in joined.casefold()
    assert "Предположение:" not in joined
    assert "Thanks!" not in joined
    assert "Please keep this slide for attribution" not in joined
    assert "youremail@freepik.com" not in joined
    assert "Спасибо за внимание" in joined

    closing_slide = rendered.slides[-1]
    assert not any(sh.name.startswith("Social Icon") for sh in closing_slide.shapes)

    pkg = OpcPackage.open(output)
    closing_part = f"ppt/slides/slide{len(rendered.slides)}.xml"
    for rel, target in pkg.internal_dependencies(closing_part):
        if rel.type_name == "slideLayout" and target in pkg.parts:
            ltxt = " ".join(
                t.text or ""
                for t in etree.fromstring(pkg.parts[target]).iter(
                    "{http://schemas.openxmlformats.org/drawingml/2006/main}t"
                )
            )
            assert "CREDITS" not in ltxt
            assert "Slidesgo" not in ltxt
            assert "Freepik" not in ltxt
            assert "Flaticon" not in ltxt


def test_few_sources_render_as_slide_footnotes_without_sources_slide(tmp_path):
    from deckdna.contracts.content_pack import ExternalSource
    from deckdna.contracts.content_pack import Origin as PackOrigin
    from deckdna.planning.content_fill import annotate_unit_origins

    prs = Presentation()
    for title in ("Обложка", "Контент", "Спасибо за внимание!"):
        sl = prs.slides.add_slide(prs.slide_layouts[1])
        sl.shapes.title.text = title
        sl.placeholders[1].text = "Тело слайда"
    template = tmp_path / "tpl.pptx"
    prs.save(template)

    pack = parse_markdown(
        "# Обзор компании\n\n"
        "## Инфраструктура сети\n\n"
        "- Розничная сеть управляет магазинами электроники по всей стране.\n"
        "- Логистическая инфраструктура включает региональные распределительные центры.\n\n"
        "## Источники\n\n"
        "- [1] Официальный сайт компании — https://example.com/about"
    )
    src_id = pack.sections[1].blocks[0].source_ref.artifact_id
    pack = pack.model_copy(
        update={
            "external_sources": [
                ExternalSource(
                    id=src_id,
                    title="Официальный сайт компании",
                    url="https://example.com/about",
                    origin=PackOrigin.web,
                )
            ]
        }
    )
    req = Brief(purpose="Обзор компании", audience="Совет", language="ru", target_slide_count=5)
    plan = annotate_unit_origins(plan_deck(pack, req), pack)
    assert not any("источник" in s.title_intent.casefold() for s in plan.slides)
    assert any(
        u.role == "footnote" and "example.com" in (u.text or "")
        for s in plan.slides
        for u in s.content_units
    )

    output = tmp_path / "out.pptx"
    report = generate_deck(template, plan, output)
    rendered = Presentation(output)
    all_texts = [
        sh.text.strip()
        for slide in rendered.slides
        for sh in slide.shapes
        if sh.has_text_frame and sh.text.strip()
    ]
    assert any("Источник:" in t and "example.com" in t for t in all_texts)
    assert not any(r.get("title") == "Источники" for r in report["slides"])


def test_few_key_facts_render_as_kpi_or_cards_instead_of_bullet_list(tmp_path):
    prs = Presentation()
    for title in ("Обложка", "Ключевые факты", "Спасибо за внимание!"):
        sl = prs.slides.add_slide(prs.slide_layouts[1])
        sl.shapes.title.text = title
        sl.placeholders[1].text = "Пункт списка"
    template = tmp_path / "tpl-facts.pptx"
    prs.save(template)

    pack = parse_markdown(
        "# Ключевые факты\n\n"
        "- Выручка сети: 726 млрд ₽ за отчётный год.\n"
        "- Сеть магазинов: более 2 700 точек в 1 200 городах."
    )
    req = Brief(purpose="Аналитика DNS", audience="Совет", language="ru", target_slide_count=3)
    plan = plan_deck(pack, req)
    output = tmp_path / "facts.pptx"
    report = generate_deck(template, plan, output)
    content_reports = [
        r for r in report["slides"] if r["purpose"] not in ("title", "thank_you", "agenda")
    ]
    assert content_reports
    assert content_reports[0]["native_composition"] in ("kpi", "cards")
