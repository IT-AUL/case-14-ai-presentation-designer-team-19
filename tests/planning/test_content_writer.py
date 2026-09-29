"""ADR-016 Stage 2 (content_writer.py::write_slide_content) — per-slide
content synthesis from an evidence pool selected by Stage 1
(structure.py). One call per slide (not batched); grounding discipline
mirrors text_fit.py/content_style.py (number-diff check); any failure or
rejection falls back to VERBATIM content built from the evidence pool,
never left empty."""

from __future__ import annotations

import asyncio

from deckdna.contracts.deck_plan import Purpose
from deckdna.contracts.evidence_graph import Node, SourceRef, Type
from deckdna.planning import content_writer
from deckdna.planning.structure import SlideStructure
from deckdna.providers.mock import MockProvider

POOL = {
    "ev_1": Node(
        id="ev_1",
        type=Type.claim,
        text="Пользователи теряют просмотры на внешних ссылках.",
        source_ref=SourceRef(artifact_id="a.md"),
    ),
    "ev_2": Node(
        id="ev_2",
        type=Type.claim,
        text="Пилот на 5% аудитории показал рост открытий на 22%.",
        source_ref=SourceRef(artifact_id="a.md"),
    ),
}


class FakeGateway:
    provider_name = "fake"

    def __init__(self, answer):
        self.answer = answer
        self.calls: list[dict] = []

    async def text_json(self, prompt_name, payload, schema):
        assert prompt_name == content_writer.CONTENT_WRITER_PROMPT
        self.calls.append(payload)
        value = self.answer(payload)
        if isinstance(value, Exception):
            raise value
        return schema.model_validate(value)


def _slide(evidence_ids=("ev_1", "ev_2"), purpose=Purpose.problem) -> SlideStructure:
    return SlideStructure(
        id="s1",
        index=1,
        purpose=purpose,
        slide_brief="показать проблему с потерей просмотров",
        evidence_ids=list(evidence_ids),
    )


def _write(slide, gw, capacities=None, language="ru"):
    sem = asyncio.Semaphore(4)
    return asyncio.run(
        content_writer.write_slide_content(gw, slide, POOL, capacities, language, sem)
    )


def test_model_answer_is_used_when_grounded():
    gw = FakeGateway(
        lambda p: {
            "title_intent": "Пользователи теряют просмотры из-за внешних ссылок",
            "key_message": "Нужно решение внутри мессенджера.",
            "bullets": [
                "Внешние ссылки уводят пользователей из чата.",
                "Пилот на 5% аудитории показал рост открытий на 22%.",
            ],
        }
    )
    title, key_message, units, used = _write(_slide(), gw)
    assert used is True
    assert title == "Пользователи теряют просмотры из-за внешних ссылок"
    assert key_message == "Нужно решение внутри мессенджера."
    assert [u.text for u in units] == [
        "Внешние ссылки уводят пользователей из чата.",
        "Пилот на 5% аудитории показал рост открытий на 22%.",
    ]
    assert all(u.evidence_ids == ["ev_1", "ev_2"] for u in units)
    assert gw.calls[0]["purpose"] == "problem"
    assert gw.calls[0]["evidence_pool"] == [n.text for n in POOL.values()]


def test_model_answer_preserves_non_text_evidence_in_the_same_pool():
    """Regression: write_slide_content's success path used to build units
    ONLY from answer.bullets, silently dropping any image/table/chart/
    number node that was also part of the slide's evidence pool (found
    while fixing the analogous bug in layout_fit.py's Stage 3 rewrite —
    the model only ever WRITES text, it never carries a picture/table/
    number reference through on its own)."""
    from deckdna.contracts.deck_plan import Kind
    from deckdna.contracts.evidence_graph import Value

    pool = dict(POOL)
    pool["ev_img"] = Node(
        id="ev_img",
        type=Type.visual,
        text="Скриншот интерфейса",
        source_ref=SourceRef(artifact_id="a.md"),
    )
    pool["ev_num"] = Node(
        id="ev_num",
        type=Type.number,
        text="22%",
        value=Value(raw="22%"),
        source_ref=SourceRef(artifact_id="a.md"),
    )
    slide = _slide(evidence_ids=["ev_1", "ev_2", "ev_img", "ev_num"])
    gw = FakeGateway(
        lambda p: {
            "title_intent": "Пользователи теряют просмотры из-за внешних ссылок",
            "key_message": "Нужно решение внутри мессенджера.",
            "bullets": [
                "Внешние ссылки уводят пользователей из чата.",
                "Пилот на 5% аудитории показал рост открытий на 22%.",
            ],
        }
    )
    sem = asyncio.Semaphore(4)
    _title, _key, units, used = asyncio.run(
        content_writer.write_slide_content(gw, slide, pool, None, "ru", sem)
    )
    assert used is True
    bullet_texts = [u.text for u in units if u.kind == Kind.bullet]
    assert len(bullet_texts) == 2  # the model's own written bullets, untouched

    image_units = [u for u in units if u.kind == Kind.image]
    assert len(image_units) == 1
    assert image_units[0].evidence_ids == ["ev_img"]

    number_units = [u for u in units if u.kind == Kind.number]
    assert len(number_units) == 1
    assert number_units[0].evidence_ids == ["ev_num"]


def test_invented_number_falls_back_to_verbatim_pool_text():
    gw = FakeGateway(
        lambda p: {"title_intent": "x", "key_message": "y", "bullets": ["Потеряно 40% просмотров."]}
    )
    _title, _key, units, used = _write(_slide(evidence_ids=["ev_1"]), gw)
    assert used is False
    assert units and units[0].text == POOL["ev_1"].text


def test_provider_failure_falls_back_not_raises():
    gw = FakeGateway(lambda p: RuntimeError("provider down"))
    _title, _key, units, used = _write(_slide(evidence_ids=["ev_1"]), gw)
    assert used is False
    assert units and units[0].text == POOL["ev_1"].text


def test_empty_bullet_is_rejected():
    gw = FakeGateway(lambda p: {"title_intent": "x", "key_message": "y", "bullets": ["  "]})
    _title, _key, units, used = _write(_slide(evidence_ids=["ev_1"]), gw)
    assert used is False
    assert units and units[0].text == POOL["ev_1"].text


def test_runaway_growth_is_rejected():
    bloated = ["слово " * 400]  # ~2400 симв, выше 800*_MAX_GROWTH_RATIO=1600
    gw = FakeGateway(lambda p: {"title_intent": "x", "key_message": "y", "bullets": bloated})
    _title, _key, units, used = _write(_slide(evidence_ids=["ev_1"]), gw)
    assert used is False


def test_max_bullets_caps_the_model_answer():
    gw = FakeGateway(
        lambda p: {
            "title_intent": "Заголовок вывода",
            "key_message": "y",
            "bullets": ["Пункт раз.", "Пункт два.", "Пункт три.", "Пункт четыре."],
        }
    )
    from deckdna.contracts.design_dna import Capacities

    _title, _key, units, used = _write(_slide(), gw, capacities=Capacities(max_bullets=2))
    assert used is True
    assert len(units) == 2


def test_empty_evidence_pool_skips_the_model_entirely():
    slide = _slide(evidence_ids=["ev_missing"])  # не в POOL -> пул пуст
    gw = FakeGateway(lambda p: {"title_intent": "x", "key_message": "y", "bullets": ["x"]})
    _title, _key, units, used = _write(slide, gw)
    assert used is False
    assert gw.calls == []
    assert units == []  # _units_from_evidence на пустом evidence_ids тоже пуст


def test_offline_mock_never_writes_synthetic_text():
    assert not content_writer.can_rewrite(MockProvider())
    assert content_writer.can_rewrite(
        MockProvider(
            fixtures={
                content_writer.CONTENT_WRITER_PROMPT: {
                    "title_intent": "x",
                    "key_message": "y",
                    "bullets": ["z"],
                }
            }
        )
    )
    assert content_writer.can_rewrite(FakeGateway(lambda p: {}))


def test_validate_rules_directly():
    from deckdna.planning.content_writer import SlideContent, validate

    pool = "Пилот на 5% аудитории показал рост на 22%."
    ok = SlideContent(title_intent="Т", key_message="k", bullets=["Рост на 22% на пилоте 5%."])
    assert validate(pool, ok, max_chars=200) is None

    invented = SlideContent(title_intent="Т", key_message="k", bullets=["Рост на 40%."])
    assert validate(pool, invented, max_chars=200) is not None


def test_validate_rejects_lowercase_title_or_bullet():
    from deckdna.planning.content_writer import SlideContent, validate

    pool = "Пилот на 5% аудитории показал рост на 22%."
    lowercase_title = SlideContent(
        title_intent="рост на пилоте", key_message="k", bullets=["Рост на 22%."]
    )
    reason = validate(pool, lowercase_title, max_chars=200)
    assert reason is not None and "title_intent" in reason

    lowercase_bullet = SlideContent(
        title_intent="Рост на пилоте", key_message="k", bullets=["рост на 22%."]
    )
    reason = validate(pool, lowercase_bullet, max_chars=200)
    assert reason is not None and "bullet" in reason

    # a leading quote mark is a legitimate sentence start, not a fragment
    quoted = SlideContent(
        title_intent="«Рилсы» растут", key_message="k", bullets=["«Рилсы» на 22%."]
    )
    assert validate(pool, quoted, max_chars=200) is None

    empty_bullet = SlideContent(title_intent="t", key_message="k", bullets=["  "])
    assert validate(pool, empty_bullet, max_chars=200) is not None


def test_validate_rejects_dangling_numeric_range_from_live_deck():
    from deckdna.planning.content_writer import SlideContent, validate

    pool = "Пилот строится на 30–50 участниках. Период 2025–2026."
    fragment = SlideContent(
        title_intent="Пилотное исследование",
        key_message="Проверка переносимости",
        bullets=["Дизайн: пилот строится на 30–50"],
    )
    assert "диапазоном" in (validate(pool, fragment, max_chars=200) or "")
    fragment.bullets = ["Дизайн: пилот строится на 30–50 участниках."]
    assert validate(pool, fragment, max_chars=200) is None
    fragment.bullets = ["Период 2025–2026"]
    assert validate(pool, fragment, max_chars=200) is None


def test_writer_preserves_equation_even_when_model_only_explains_it():
    equation = "Index = 0.25 × active - 0.75 × idle"
    pool = dict(POOL, ev_math=Node(
        id="ev_math", type=Type.claim, text=equation,
        source_ref=SourceRef(artifact_id="math.md"),
    ))
    gw = FakeGateway(lambda p: {
        "title_intent": "Индекс сравнивает активность с базовой линией",
        "key_message": "Это продуктовая гипотеза.",
        "bullets": ["Коэффициенты требуют проверки на данных."],
    })
    result = asyncio.run(content_writer.write_slide_content(
        gw, _slide(["ev_1", "ev_math"]), pool, None, "ru", asyncio.Semaphore(1),
    ))
    assert result[3]
    equations = [u for u in result[2] if u.role == "equation"]
    assert len(equations) == 1 and equations[0].text == equation
    assert equations[0].evidence_ids == ["ev_math"]


def test_numeric_guard_checks_title_and_key_message_without_decimal_false_positive():
    from deckdna.planning.content_writer import SlideContent, validate

    answer = SlideContent(title_intent="Доля составляет 37,20%", key_message="Тест",
                          bullets=["Доля составляет 37.2%."])
    assert validate("Доля 37.20%", answer, 200) is None
    assert validate("Доля 22%", answer, 200) is not None
    answer.title_intent = "Подтверждённый результат"
    answer.key_message = "Выборка составила 999 человек"
    answer.bullets = ["Рост на 22%."]
    assert validate("Доля 22%", answer, 200) is not None
