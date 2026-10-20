"""Добор контента до запрошенного объёма колоды (ADR-030).

``fill_content`` — точка входа: оценка достаточности, веб-ресёрч с
проверкой цитат, добор из знаний модели, честное сокращение колоды.
"""

from deckdna.research.fill import FillOutcome, fill_content, merge_into_pack

__all__ = ["FillOutcome", "fill_content", "merge_into_pack"]
