"""Детерминированный срез таблицы для слайда; исходные данные не меняются."""

from __future__ import annotations

from deckdna.analysis.values import parse_number

MAX_SLIDE_COLUMNS = 4


def column_indices(
    headers: list[str], rows: list[list], limit: int = MAX_SLIDE_COLUMNS
) -> list[int]:
    """Ключевая первая колонка + самые вариативные метрики/статусы.

    Кардинальность оценивает вариативность независимо от единиц метрик.
    При равенстве сохраняется исходный порядок. Свободный длинный текст
    уступает числам и коротким повторяющимся категориям.
    """
    if len(headers) <= limit:
        return list(range(len(headers)))

    def score(index: int) -> tuple[int, int]:
        values = [str(r[index]).strip() for r in rows if index < len(r) and r[index] is not None]
        values = [v for v in values if v]
        distinct = len(set(values))
        numeric = bool(values) and sum(parse_number(v) is not None for v in values) >= 0.8 * len(
            values
        )
        status = (
            bool(values)
            and distinct <= 12
            and max(map(len, values)) <= 24
            and (distinct < len(values) or max(map(len, values)) <= 12)
        )
        return (int(numeric or status), distinct)

    ranked = sorted(range(1, len(headers)), key=score, reverse=True)
    return [0, *sorted(ranked[: max(0, limit - 1)])]


def column_note(shown: int, total: int) -> str:
    return f"Отображены первые {shown} из {total} колонок. Полная таблица доступна в выгрузке"
