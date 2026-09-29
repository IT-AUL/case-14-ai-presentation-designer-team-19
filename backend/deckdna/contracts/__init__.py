"""Контракты DeckDNA (сгенерены datamodel-codegen из schemas/) + каноническая
сериализация для схемы — см. serialize.py."""

from deckdna.contracts.serialize import to_schema_dict, to_schema_json

__all__ = ["to_schema_dict", "to_schema_json"]
