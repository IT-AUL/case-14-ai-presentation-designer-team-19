"""Преамбула «Нужна презентация на N слайдов: …» — не текст слайдов.

QA: бриф вида «Нужна презентация на 12 слайдов: Умный помощник…» попадал
в заголовок обложки и лид обзора целиком, включая служебную преамбулу.
"""

from deckdna.planning.story_director import clean_purpose_text


def test_meta_request_preamble_is_stripped():
    assert (
        clean_purpose_text("Нужна презентация на 12 слайдов: Умный помощник для HR")
        == "Умный помощник для HR"
    )
    assert clean_purpose_text("Сделай презентацию про кошек: Итоги Q3") == "Итоги Q3"
    assert clean_purpose_text("Make a 6-slide deck about: Revenue growth") == "Revenue growth"
    assert clean_purpose_text("Нужна презентация: Отчёт за год") == "Отчёт за год"


def test_legit_titles_are_not_stripped():
    # без двоеточия «Презентация продукта» — легитимная тема, а не запрос
    assert clean_purpose_text("Презентация продукта") == "Презентация продукта"
    assert clean_purpose_text("Отчёт: результаты Q3") == "Отчёт: результаты Q3"
    assert clean_purpose_text("Итоги продаж за год") == "Итоги продаж за год"


def test_generic_purpose_still_dropped():
    assert clean_purpose_text("presentation") is None
    assert clean_purpose_text("презентация.") is None
    assert clean_purpose_text(None) is None
