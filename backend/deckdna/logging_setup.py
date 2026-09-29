"""Root-logger configuration shared by every entrypoint (API, CLI, skill).

ADR-013: a live 24-minute contextual-audit hang produced zero application
log lines -- nothing anywhere called ``logging.basicConfig()``, so the
existing ``logger.info``/``warning`` calls scattered across
``story_director.py``/``rerank.py``/``contextual.py``/``providers/*`` were
silently dropped by the root logger's absent handler. The API server fix
(``api/app.py``) covers only that one entrypoint -- CLI (``deckdna
generate``) and the portable skill runtime (``skill/run_skill.py``, the
actual hackathon-submission entrypoint) import ``generation/pipeline.py``
directly and never touch ``api/app.py``, so they were still silently
dropping logs.

Stream choice matters: the API server's stdout is pure log output (no
machine-readable contract on it), but the CLI and skill runtime print a
JSON result to stdout as their actual output contract (parsed by callers,
e.g. ``json.loads(result.output)`` in CLI tests) -- log lines interleaved
into that stream would corrupt it. CLI/skill entrypoints must log to
stderr; only the API server logs to stdout.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import TextIO


def configure_logging(*, stream: TextIO = sys.stderr) -> None:
    """Configure the root logger once; a no-op on repeat calls (tests import
    entrypoint modules multiple times) since ``basicConfig`` itself already
    no-ops when the root logger already has a handler.

    ``DECKDNA_LOG_LEVEL`` (default ``INFO``) controls the level everywhere.
    """
    level_name = os.environ.get("DECKDNA_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        stream=stream,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
