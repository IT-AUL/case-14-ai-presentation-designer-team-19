"""Навык text_fit: модель переписывает переполненный текст короче, а
детерминированный слой проверяет ответ и решает, помогло ли."""

from __future__ import annotations

import asyncio

import pytest
from deckdna.audit.basic import audit_deck
from deckdna.errors import DeckDNAError
from deckdna.generation import pipeline
from deckdna.pptx.fitting import text_fit
from pptx import Presentation
from pptx.enum.text import MSO_AUTO_SIZE
from pptx.util import Inches, Pt

LONG = (
    "Ручная адаптация контента под фирменный шаблон занимает у команды до 6 часов "
    "на каждую презентацию и требует участия дизайнера на каждом шаге работы. "
    "Типовые генераторы выдают растровые слайды, которые нельзя редактировать."
)


class FakeGateway:
    """Отвечает заданной функцией от payload; считает вызовы."""

    def __init__(self, answer):
        self.answer = answer
        self.calls: list[dict] = []

    async def text_json(self, prompt_name, payload, schema):
        assert prompt_name == text_fit.PROMPT_NAME
        self.calls.append(payload)
        value = self.answer(payload)
        if isinstance(value, Exception):
            raise value
        return schema.model_validate(value)


def _deck(tmp_path, text=LONG, width=None, height=None):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(
        Inches(1), Inches(1), width or Inches(3), height or Inches(0.8)
    )
    box.text_frame.word_wrap = True
    box.text_frame.auto_size = MSO_AUTO_SIZE.NONE  # иначе рамка растёт под текст
    run = box.text_frame.paragraphs[0].add_run()
    run.text = text
    run.font.size = Pt(18)
    run.font.bold = True
    path = tmp_path / "deck.pptx"
    prs.save(path)
    return path


def _overflow(path):
    return [i for i in audit_deck(path) if i.rule_code == "text.overflow"]


def _fit(path, issues, gateway, out):
    return asyncio.run(text_fit.fit_texts(path, issues, gateway, out))


def test_model_rewrite_fixes_overflow_and_keeps_formatting(tmp_path):
    deck = _deck(tmp_path)
    issues = _overflow(deck)
    assert issues
    gw = FakeGateway(lambda p: {"paragraphs": ["Адаптация шаблона — до 6 часов"]})
    out = tmp_path / "out.pptx"
    report = _fit(deck, issues, gw, out)
    assert list(report.rewritten) == [issues[0].id]
    assert gw.calls[0]["max_chars"] < len(LONG)
    assert gw.calls[0]["paragraphs"] == [LONG]
    assert not _overflow(out), "rewritten text fits its frame"
    run = Presentation(str(out)).slides[0].shapes[0].text_frame.paragraphs[0].runs[0]
    assert run.text == "Адаптация шаблона — до 6 часов"
    assert run.font.bold and run.font.size == Pt(18)


def test_badge_sized_shape_skips_rewrite_instead_of_mangling_text(tmp_path):
    """Полное предложение в бейдж-рамке (0.19in) не должно доезжать до модели
    как задание «сожми до 12 символов» — так рождаются однословные огрызки
    вроде «Лента»/«Видео» на реальных живых прогонах. Геометрия здесь
    настолько мала, что ratio упирается в пол text_fit._RATIO_FLOOR; вместе
    с длинным исходником это должно быть честно отклонено без вызова модели."""
    deck = _deck(tmp_path, text=LONG, width=Inches(1), height=Inches(0.19))
    issues = _overflow(deck)
    assert issues
    gw = FakeGateway(lambda p: {"paragraphs": ["x"]})
    out = tmp_path / "out.pptx"
    report = _fit(deck, issues, gw, out)
    assert not report.rewritten
    assert gw.calls == [], "model must not be asked to compress a sentence into ~12 chars"
    assert "смысл" in next(iter(report.rejected.values()))


def test_invented_numbers_are_rejected(tmp_path):
    deck = _deck(tmp_path)
    gw = FakeGateway(lambda p: {"paragraphs": ["Адаптация занимает 40 часов"]})
    report = _fit(deck, _overflow(deck), gw, tmp_path / "out.pptx")
    assert not report.rewritten
    assert "40" in next(iter(report.rejected.values()))


def test_wrong_paragraph_count_and_too_long_are_rejected():
    assert "абзацев" in text_fit.validate(["a b c"], text_fit.FitText(paragraphs=["x", "y"]), 50)
    long = text_fit.FitText(paragraphs=["слово " * 30])
    assert "длинный" in text_fit.validate(["слово " * 40], long, 20)


def test_provider_failure_is_reported_not_raised(tmp_path):
    deck = _deck(tmp_path)
    gw = FakeGateway(lambda p: DeckDNAError("provider_unavailable", "down"))
    out = tmp_path / "out.pptx"
    report = _fit(deck, _overflow(deck), gw, out)
    assert not report.rewritten and "provider_unavailable" in next(iter(report.rejected.values()))
    assert out.read_bytes() == deck.read_bytes()


def test_pipeline_model_fit_accepts_only_verified_improvement(tmp_path):
    deck = _deck(tmp_path)
    issues = audit_deck(deck, deck_revision=1)
    ok = FakeGateway(lambda p: {"paragraphs": ["Адаптация шаблона — до 6 часов"]})
    after, gained, reason = pipeline._model_fit(deck, issues, ok, None, "ru")
    assert gained == 1 and reason is None
    assert not [i for i in after if i.rule_code == "text.overflow"]

    (tmp_path / "second").mkdir()
    deck2 = _deck(tmp_path / "second")
    issues2 = audit_deck(deck2, deck_revision=1)
    before = deck2.read_bytes()
    # «короче», но всё ещё не влезает — отказ, колода не тронута
    still_long = FakeGateway(lambda p: {"paragraphs": [LONG[:-40]]})
    kept, gained2, reason2 = pipeline._model_fit(deck2, issues2, still_long, None, "ru")
    assert gained2 == 0 and reason2 and kept is issues2
    assert deck2.read_bytes() == before


def test_protected_slides_are_never_rewritten(tmp_path):
    deck = _deck(tmp_path)
    issues = audit_deck(deck, deck_revision=1)
    gw = FakeGateway(lambda p: {"paragraphs": ["коротко и ясно про шаблон"]})
    _, gained, _ = pipeline._model_fit(deck, issues, gw, [0], "ru")
    assert gained == 0 and gw.calls == []


@pytest.mark.parametrize(
    "use_llm,session,server,expected",
    [
        (None, False, False, "none"),
        (None, False, True, "server"),
        (False, False, True, "none"),
        (True, False, False, "server"),
        (None, True, False, "session"),
    ],
)
def test_gateway_selection_auto_mode(monkeypatch, use_llm, session, server, expected):
    from deckdna.api import app as api
    from deckdna.contracts.deck_plan import Brief

    monkeypatch.setattr(api, "_server_provider_ready", lambda: server)
    monkeypatch.setattr(api, "build_gateway", lambda: "server")
    monkeypatch.setattr(api, "_session_gateway", lambda s: "session")
    monkeypatch.setattr(api, "_require_session", lambda sid, pid=None: sid)
    body = api.PlanRequest(
        content_pack_id="p",
        brief=Brief(purpose="x", audience="y", language="ru", target_slide_count=10),
        use_llm=use_llm,
        provider_session_id="ps" if session else None,
    )
    assert (api._gateway_for(body) or "none") == expected


def test_offline_mock_never_writes_synthetic_text():
    from deckdna.providers.mock import MockProvider

    assert not text_fit.can_rewrite(MockProvider())
    assert text_fit.can_rewrite(MockProvider(fixtures={"text_fit": {"paragraphs": ["x"]}}))
    assert text_fit.can_rewrite(FakeGateway(lambda p: {}))
