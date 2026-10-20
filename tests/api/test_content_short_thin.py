"""``content_short`` без явного «на N слайдов» и без target_slide_count.

QA: куцый вход (2 предложения) молча собирал 3 слайда — вопроса о
нехватке материала не было, потому что ``explicit_target`` был None.
Дефолт сервиса (8) — честный «want» для такого входа.
"""

from pathlib import Path

from deckdna.assistant.questions import build_questions
from deckdna.ingestion.content_parsers import parse_file

ONE_LINER = "# Итоги года\n\nИтоги продаж за год для совета директоров.\n"


def _thin_pack(tmp_path: Path):
    src = tmp_path / "idea.md"
    src.write_text(ONE_LINER, encoding="utf-8")
    return parse_file(src)


def test_thin_pack_without_explicit_target_asks_content_short(tmp_path):
    pack = _thin_pack(tmp_path)
    result = build_questions(pack, {})
    kinds = [q["id"] for q in result["questions"]]
    assert "content_short" in kinds
    # неявная худоба — вопрос, но не блокирующее вето на план
    assert result["suggested_brief"]["content_fill"] == "expand"


def test_rich_pack_without_target_stays_quiet(tmp_path):
    src = tmp_path / "rich.md"
    src.write_text(
        "# Продукт\n\n## Проблема\n\n"
        + "".join(f"- Пункт {i} с понятным пояснением для читателя.\n" for i in range(5))
        + "\n## Решение\n\n"
        + "".join(f"- Пункт {i} с понятным пояснением для читателя.\n" for i in range(5)),
        encoding="utf-8",
    )
    result = build_questions(
        parse_file(src), {"purpose": "Утвердить пилот", "audience": "руководство"}
    )
    assert all(q["id"] != "content_short" for q in result["questions"])


def test_count_inside_purpose_text_counts_as_explicit(tmp_path):
    pack = _thin_pack(tmp_path)
    result = build_questions(pack, {"purpose": "Нужна презентация на 12 слайдов про HR"})
    assert any(q["id"] == "content_short" for q in result["questions"])
    assert result["suggested_brief"]["target_slide_count"] == 12
    assert result["suggested_brief"]["content_fill"] == "ask"
