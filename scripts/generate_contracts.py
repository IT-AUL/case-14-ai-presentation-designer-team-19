#!/usr/bin/env python3
"""Generate Pydantic models from /schemas JSON Schema files into
backend/deckdna/contracts/. CI asserts this output is in sync (freeze-1).

Run: uv run python scripts/generate_contracts.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "schemas"
OUT = ROOT / "backend" / "deckdna" / "contracts"


def main() -> int:
    if not SCHEMAS.exists():
        print("schemas/ not found", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    for schema in sorted(SCHEMAS.glob("*.schema.json")):
        target = OUT / (schema.stem.replace(".schema", "") .replace("-", "_") + ".py")
        subprocess.run(
            [
                sys.executable, "-m", "datamodel_code_generator",
                "--input", str(schema),
                "--input-file-type", "jsonschema",
                "--output", str(target),
                "--output-model-type", "pydantic_v2.BaseModel",
                "--use-annotated",
                "--disable-timestamp",
            ],
            check=True,
        )
        print(f"generated {target.name} <- {schema.name}")
    print("contracts in sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
