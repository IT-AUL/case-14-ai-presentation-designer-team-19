"""Content Ingestion: входные файлы → ContentPack (docs/ARCHITECTURE.md)."""

from deckdna.ingestion.content_parsers import (
    PARSER_VERSION,
    detect_language,
    parse_file,
    parse_json_content,
    parse_markdown,
    parse_txt,
)

__all__ = [
    "PARSER_VERSION",
    "detect_language",
    "parse_file",
    "parse_json_content",
    "parse_markdown",
    "parse_txt",
]
