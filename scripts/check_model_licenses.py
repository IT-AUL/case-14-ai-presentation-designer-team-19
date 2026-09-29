#!/usr/bin/env python3
"""OR-016: проверка манифеста моделей configs/model_licenses.yaml.

Usage: .venv/bin/python scripts/check_model_licenses.py
Exit 0 — все заявленные модели покрыты и соответствуют TZ; 1 — violations.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from deckdna.evaluation.model_licenses import check, format_report


def main() -> int:
    report = check()
    print(format_report(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
