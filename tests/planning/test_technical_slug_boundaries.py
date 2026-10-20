"""Technical metadata is filtered without deleting meaningful user copy."""

import pytest
from deckdna.contracts.deck_plan import Brief
from deckdna.ingestion.content_parsers import parse_markdown
from deckdna.planning.content_fill import estimate_content_capacity
from deckdna.planning.story_director import clean_purpose_text, plan_deck
from deckdna.planning.talk_track import _sentence


@pytest.mark.parametrize(
    "slug", ["presentation", "project", "deck", "balanced", "faithful", "visual"]
)
def test_punctuated_metadata_is_not_used_as_presentation_copy(slug):
    raw = f"  {slug}.  "
    source = parse_markdown("# Результаты пилота\n\nКоманда завершила пилот и проверила качество.")
    source = source.model_copy(update={"title_hint": raw})
    brief = Brief(
        purpose=raw, key_message=raw, audience="команда", language="ru", target_slide_count=3
    )
    plan = plan_deck(source, brief)
    cover = plan.slides[0]
    assert clean_purpose_text(raw) is None
    assert cover.title_intent == "Результаты пилота"
    assert cover.key_message == "Результаты пилота"
    assert [u.text for u in cover.content_units] == ["Результаты пилота"]
    section = source.sections[0].model_copy(update={"heading": raw})
    assert not estimate_content_capacity(
        source.model_copy(update={"sections": [section]}), brief
    ).covered_topics


@pytest.mark.parametrize(
    "text",
    [
        "Контекст: visual analytics помогает исследовать данные.",
        "Context: visualizations reveal the distribution.",
        "Цель: project management снижает сроки согласования.",
        "Style: balanced growth remains the strategic objective.",
        "Purpose: deck design helps communicate findings.",
    ],
)
def test_speaker_notes_preserve_meaningful_phrases_starting_with_slug(text):
    assert _sentence(text) == text
    assert clean_purpose_text(text) == text


def test_speaker_notes_still_remove_standalone_technical_context():
    assert _sentence("Контекст: visual. Пилот завершён.") == "Пилот завершён."


@pytest.mark.parametrize("punctuation", ["...", "?!", "…", "!!!"])
def test_speaker_notes_remove_technical_context_with_repeated_punctuation(punctuation):
    text = f"Контекст: visual{punctuation} Пилот завершён."
    assert _sentence(text) == "Пилот завершён."
