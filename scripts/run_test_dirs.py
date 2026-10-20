"""Run each pytest directory in a fresh process, bounded by two hours overall.

PYTHONPATH=backend .venv/bin/python scripts/run_test_dirs.py --out artifacts/tests
No single pytest process accumulates STORE across every test directory. Full logs
and process exit codes are retained; a green summary is never inferred from grep.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = (
    "acceptance", "analysis", "api", "audit", "cli", "contract", "evaluation", "generation",
    "ingestion", "mcp", "planning", "pptx", "providers", "regression", "repair", "research",
    "skill", "template",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "artifacts/tests")
    parser.add_argument("--timeout-s", type=float, default=7200)
    parser.add_argument("directories", nargs="*", default=[f"tests/{d}" for d in DIRECTORIES])
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    rows = []
    for directory in args.directories:
        path = (ROOT / directory).resolve()
        if not path.is_relative_to(ROOT / "tests") or not path.is_dir():
            parser.error(f"not a test directory: {directory}")
        log_path = args.out / f"{path.name}.log"
        before = time.monotonic()
        remaining = args.timeout_s - (before - started)
        command = [sys.executable, "-m", "pytest", str(path), "-q", "-p", "no:cacheprovider",
                   "--tb=short", "-rfs"]
        env = {**os.environ, "PYTHONPATH": str(ROOT / "backend"), "DECKDNA_VK_LIVE": "0",
               "DECKDNA_MOCK_PROVIDER": "true"}
        timed_out = remaining <= 0
        rc = 124
        if not timed_out:
            with log_path.open("w") as log:
                # Fixed current interpreter, no shell. The process group includes renderers.
                proc = subprocess.Popen(  # noqa: S603
                    command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                try:
                    rc = proc.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        proc.wait()
        row = {"directory": directory, "exit_code": rc, "timed_out": timed_out,
               "seconds": round(time.monotonic() - before, 3), "log": str(log_path)}
        rows.append(row)
        (args.out / "summary.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        print(json.dumps(row, ensure_ascii=False), flush=True)
        if timed_out:
            break
    return 0 if len(rows) == len(args.directories) and all(r["exit_code"] == 0 for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
