"""Portable executable DeckDNA agent skill runtime.

`skill/manifest.yaml` and `GET /skill/manifest` describe the skill, but
nothing in the repo actually *runs* it end to end as one agent-invocable
entrypoint: `deckdna generate` builds one strategy into one directory,
`deckdna export` renders one format at a time. This module is the real,
single entrypoint the official requirement asks for — an arbitrary
PPTX/POTX template plus a supplied content package in, three editable,
template-compliant variants (faithful/balanced/visual) out, each with
native PPTX + PDF + HTML + a Quality Passport, in one run, without manual
editing.

It is a thin orchestration wrapper: every stage is the same public
pipeline code the CLI already uses (`deckdna.generation.pipeline.generate`,
`deckdna.pptx.exporting.render.render_html`). No template names, indices,
layouts or colors are hardcoded — the pipeline underneath already proves
arbitrary-template adaptation; this module only
drives it three times and reports the outcome honestly.

Runs from any working directory, with or without `pip install -e .`:

    python3 skill/run_skill.py TEMPLATE CONTENT OUT_DIR [options]
    python3 -m skill.run_skill TEMPLATE CONTENT OUT_DIR [options]

See skill/SKILL.md for the full contract (inputs, outputs, exit codes).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _ensure_deckdna_importable() -> None:
    """Make `import deckdna` resolve THIS checkout's ``backend/`` — never
    an installed package from a different checkout.

    The bundled backend goes first on ``sys.path`` unconditionally (not
    only on ImportError): a site-packages editable install of another
    checkout would otherwise silently shadow the repo the skill ships
    with. If ``deckdna`` is already imported (e.g. under pytest) it must
    be this same checkout's package — refusing to mix is honest, mixing
    versions silently is a bug.
    """
    backend = (_REPO_ROOT / "backend").resolve()
    existing = sys.modules.get("deckdna")
    if existing is not None:
        imported_from = Path(getattr(existing, "__file__", "") or "").resolve()
        if backend not in imported_from.parents:
            raise ImportError(
                "deckdna already imported from a different checkout "
                f"({imported_from}) — refusing to mix versions; "
                "run the skill in a clean interpreter"
            )
        return
    backend_str = str(backend)
    while backend_str in sys.path:
        sys.path.remove(backend_str)
    sys.path.insert(0, backend_str)


_ensure_deckdna_importable()

import yaml  # noqa: E402
from deckdna.contracts.deck_plan import Brief  # noqa: E402
from deckdna.contracts.variant_spec import Strategy  # noqa: E402
from deckdna.errors import DeckDNAError  # noqa: E402
from deckdna.generation.pipeline import generate  # noqa: E402
from deckdna.ingestion.content_parsers import (  # noqa: E402, SLF001 -- честный
    _PARSERS as _CONTENT_PARSERS,  # источник поддерживаемых расширений, не дублируем
)
from deckdna.logging_setup import configure_logging  # noqa: E402
from deckdna.pptx.exporting.render import render_html  # noqa: E402
from deckdna.providers.factory import build_gateway  # noqa: E402

# Skill output contract is a single JSON blob on stdout (run_skill.py's own
# CLI prints it, callers parse it) -- logs go to stderr so they never
# corrupt it. See logging_setup.py / ADR-013 for why this exists (the
# same silent-log-drop incident that motivated the API-server fix applies
# here too: this module never imported api/app.py, so it never inherited
# that fix).
configure_logging(stream=sys.stderr)

ALL_STRATEGIES: tuple[Strategy, ...] = (Strategy.faithful, Strategy.balanced, Strategy.visual)
SKILL_MANIFEST_PATH = _REPO_ROOT / "skill" / "manifest.yaml"


class SkillInputError(Exception):
    """Input-validation failure before the pipeline starts.

    Typed the same shape as ``DeckDNAError.to_envelope()`` so callers get
    one consistent error contract regardless of which stage rejected the
    run — but raised without importing providers/settings, so a bad path
    or ambiguous content directory never triggers provider setup.
    """

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_envelope(self) -> dict[str, Any]:
        return {
            "error": {
                "code": "invalid_input",
                "message": self.message,
                "stage": "skill.input",
                "retryable": False,
                "details": self.details,
            }
        }


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _unexpected_error_envelope(stage: str, exc: Exception) -> dict[str, Any]:
    """Typed ``internal_error`` for failures outside DeckDNAError —
    deliberately WITHOUT ``str(exc)``: exception text can carry URLs,
    headers or credentials from a failed call; the class name cannot."""
    return {
        "code": "internal_error",
        "message": f"unexpected {exc.__class__.__name__}",
        "stage": stage,
        "retryable": False,
        "details": {"exception": exc.__class__.__name__},
    }


def _close_gateway(gateway: Any) -> None:
    """Best-effort deterministic close of the gateway's HTTP client —
    pool lifetime must not depend on interpreter GC."""
    if gateway is None:
        return
    aclose = getattr(gateway, "aclose", None)
    if aclose is None:
        return
    try:
        asyncio.run(aclose())
    except Exception:  # noqa: BLE001, S110 -- cleanup must never fail the run
        pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_ref(path: Path, out_dir: Path) -> dict[str, Any]:
    """Path (relative to out_dir, portable), size and checksum — never the
    file's own bytes. Summary JSON stays small regardless of deck size."""
    return {
        "path": str(path.relative_to(out_dir)),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def resolve_content_file(content: Path) -> Path:
    """A single supported content file, or a directory with exactly one
    unambiguous candidate (non-recursive scan by extension).

    The real format dispatch stays in ``ingestion.content_parsers.parse_file``
    (imported via ``_CONTENT_PARSERS`` so the supported-extension set can
    never drift from what actually parses) — this only decides *which*
    file to hand it, honestly, before any pipeline stage runs.
    """
    if content.is_file():
        if content.suffix.lower() not in _CONTENT_PARSERS:
            raise SkillInputError(
                f"unsupported content format: {content.suffix!r}",
                details={"path": str(content), "supported": sorted(_CONTENT_PARSERS)},
            )
        return content
    if content.is_dir():
        candidates = sorted(
            p
            for p in content.iterdir()
            if p.is_file() and p.suffix.lower() in _CONTENT_PARSERS
        )
        if not candidates:
            raise SkillInputError(
                f"no supported content file found in directory: {content}",
                details={"path": str(content), "supported": sorted(_CONTENT_PARSERS)},
            )
        if len(candidates) > 1:
            raise SkillInputError(
                f"ambiguous content directory ({len(candidates)} candidates found) — "
                "pass a single file explicitly",
                details={"path": str(content), "candidates": [str(c) for c in candidates]},
            )
        return candidates[0]
    raise SkillInputError(f"content path not found: {content}", details={"path": str(content)})


def _skill_identity() -> dict[str, str]:
    try:
        data = yaml.safe_load(SKILL_MANIFEST_PATH.read_text(encoding="utf-8"))
    except OSError:
        return {"id": "", "version": ""}
    return {"id": str(data.get("id", "")), "version": str(data.get("version", ""))}


def _run_variant(
    strategy: Strategy,
    template: Path,
    content_file: Path,
    brief: Brief,
    out_dir: Path,
    gateway: Any,
) -> dict[str, Any]:
    """Run one strategy through the real pipeline + HTML export.

    Never raises: pipeline and render failures are typed DeckDNAErrors and
    become an honest per-variant status instead of aborting the whole run —
    one bad variant should not hide the other two succeeding.
    """
    variant_dir = out_dir / strategy.value
    record: dict[str, Any] = {"strategy": strategy.value, "status": "failed"}
    started = time.monotonic()

    try:
        report = generate(
            template, content_file, brief, variant_dir, gateway=gateway, strategy=strategy
        )
    except DeckDNAError as exc:
        record["error"] = exc.to_envelope()["error"]
        record["duration_seconds"] = round(time.monotonic() - started, 2)
        return record
    except Exception as exc:  # noqa: BLE001 -- typed, never a bare traceback
        record["error"] = _unexpected_error_envelope("skill.variant", exc)
        record["duration_seconds"] = round(time.monotonic() - started, 2)
        return record

    try:
        by_severity: dict[str, int] = {}
        for issue in report["audit_issues"]:
            by_severity[issue["severity"]] = by_severity.get(issue["severity"], 0) + 1

        pptx_path = Path(report["artifacts"]["pptx"])
        # Planned content the composer had to drop (e.g. text slots ran
        # out). A valid deck that silently omits supplied content is not
        # a clean "ok" — disclose the drop and degrade the variant.
        dropped_units = {
            kind: count
            for kind, count in (
                report["compose_report"].get("dropped_units") or {}
            ).items()
            if count
        }
        record.update(
            {
                "status": "ok",
                "duration_seconds": report["duration_seconds"],
                "slides_out": report["slides_out"],
                "planner": report["planner"],
                "contextual_audit": report["contextual_audit"],
                "issues_by_severity": by_severity,
                "editability": report["quality_passport"]["metrics"]["editability"],
                "dropped_units": dropped_units,
                "compose_warnings": report["compose_report"].get("warnings") or [],
                "artifacts": {
                    "pptx": _artifact_ref(pptx_path, out_dir),
                    "pdf": _artifact_ref(Path(report["artifacts"]["pdf"]), out_dir),
                    "quality_passport": _artifact_ref(
                        Path(report["artifacts"]["quality_passport"]), out_dir
                    ),
                    "html": None,
                },
            }
        )

        if dropped_units:
            record["status"] = "degraded"

        try:
            index = render_html(pptx_path, variant_dir / "html")
        except DeckDNAError as exc:
            # PPTX/PDF/passport are real and usable; HTML is one of three
            # required export formats, so this variant is not a clean "ok" —
            # but it must not be reported as a failed run either.
            record["status"] = "degraded"
            record["error"] = exc.to_envelope()["error"]
        except Exception as exc:  # noqa: BLE001 -- same degraded outcome, typed
            record["status"] = "degraded"
            record["error"] = _unexpected_error_envelope("skill.render_html", exc)
        else:
            html_ref = _artifact_ref(index, out_dir)
            html_ref["dir"] = str((variant_dir / "html").relative_to(out_dir))
            record["artifacts"]["html"] = html_ref
    except Exception as exc:  # noqa: BLE001 -- typed, never a bare traceback
        # Postprocessing (report access, stat/hash, html ref) is covered too:
        # a variant whose summary record cannot be completed honestly is a
        # failed variant, and the run continues with the others.
        record["status"] = "failed"
        record["error"] = _unexpected_error_envelope("skill.variant", exc)
        record["duration_seconds"] = round(time.monotonic() - started, 2)

    return record


def _early_failure(
    out_dir: Path, summary: dict[str, Any], error_envelope: dict[str, Any], t0: float
) -> dict[str, Any]:
    summary["status"] = "failed"
    summary["error"] = error_envelope["error"]
    summary["variants"] = []
    summary["finished_at"] = _now_iso()
    summary["duration_seconds"] = round(time.monotonic() - t0, 2)
    _write_summary(out_dir, summary)
    return summary


def _write_summary(out_dir: Path, summary: dict[str, Any]) -> Path:
    path = out_dir / "summary.json"
    path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def run_skill(
    *,
    template: Path,
    content: Path,
    out_dir: Path,
    purpose: str = "Представить решение",
    audience: str = "эксперты и жюри",
    language: str = "ru",
    target_slide_count: int = 12,
    use_llm: bool = False,
    strategies: list[Strategy] | None = None,
) -> dict[str, Any]:
    """Run the skill end to end; always returns a summary dict and always
    writes it to ``out_dir/summary.json`` — including on early failure, so
    a caller never has to guess whether the run started."""
    template = Path(template).resolve()
    content = Path(content).resolve()
    out_dir = Path(out_dir).resolve()
    run_strategies = list(strategies) if strategies else list(ALL_STRATEGIES)

    t0 = time.monotonic()
    summary: dict[str, Any] = {
        "skill": _skill_identity(),
        "started_at": _now_iso(),
        "input": {"template": str(template), "content": str(content)},
        "brief": {
            "purpose": purpose,
            "audience": audience,
            "language": language,
            "target_slide_count": target_slide_count,
        },
        "llm_enabled": use_llm,
        "strategies_requested": [s.value for s in run_strategies],
        "out_dir": str(out_dir),
    }

    out_dir.mkdir(parents=True, exist_ok=True)

    # Rerun safety: a destination already holding artifacts would let
    # stale decks survive a new failed run and masquerade as fresh
    # output — reject it (the tool is honestly NOT idempotent). An
    # out_dir containing ONLY summary.json is safe to reuse: it means an
    # earlier run died before producing anything.
    leftovers = [p.name for p in out_dir.iterdir() if p.name != "summary.json"]
    if leftovers:
        err = SkillInputError(
            f"out_dir is not empty: {out_dir} — pass a fresh destination "
            "(reruns into a used directory are rejected so stale artifacts "
            "can never masquerade as new output)",
            details={"path": str(out_dir), "leftover": leftovers[:10]},
        )
        return _early_failure(out_dir, summary, err.to_envelope(), t0)

    if not template.exists():
        err = SkillInputError(f"template not found: {template}", details={"path": str(template)})
        return _early_failure(out_dir, summary, err.to_envelope(), t0)

    try:
        content_file = resolve_content_file(content)
    except SkillInputError as exc:
        return _early_failure(out_dir, summary, exc.to_envelope(), t0)
    summary["input"]["content_resolved"] = str(content_file)

    try:
        brief = Brief(
            purpose=purpose,
            audience=audience,
            language=language,
            target_slide_count=target_slide_count,
        )
    except Exception as exc:  # noqa: BLE001 -- pydantic ValidationError, honest invalid_input
        err = SkillInputError(f"invalid brief: {exc}")
        return _early_failure(out_dir, summary, err.to_envelope(), t0)

    gateway = None
    if use_llm:
        try:
            gateway = build_gateway()
        except DeckDNAError as exc:
            return _early_failure(out_dir, summary, exc.to_envelope(), t0)
        except Exception as exc:  # noqa: BLE001
            return _early_failure(
                out_dir,
                summary,
                {"error": _unexpected_error_envelope("skill.gateway", exc)},
                t0,
            )

    try:
        variants = [
            _run_variant(strategy, template, content_file, brief, out_dir, gateway)
            for strategy in run_strategies
        ]
    finally:
        _close_gateway(gateway)

    ok_count = sum(1 for v in variants if v["status"] == "ok")
    usable_count = sum(1 for v in variants if v["status"] in ("ok", "degraded"))
    if variants and ok_count == len(variants):
        overall = "ok"
    elif usable_count > 0:
        # A degraded variant still produced honest artifacts (with the
        # reason disclosed) — the run is partial, not failed.
        overall = "partial"
    else:
        overall = "failed"

    summary["variants"] = variants
    summary["status"] = overall
    summary["finished_at"] = _now_iso()
    summary["duration_seconds"] = round(time.monotonic() - t0, 2)
    _write_summary(out_dir, summary)
    return summary


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_skill.py",
        description=(
            "DeckDNA portable agent skill: arbitrary PPTX/POTX template + content "
            "-> faithful/balanced/visual variants (PPTX+PDF+HTML+Quality Passport), "
            "one run, no manual editing."
        ),
    )
    parser.add_argument("template", type=Path, help="template .pptx/.potx")
    parser.add_argument(
        "content",
        type=Path,
        help=(
            "content: .json/.md/.markdown/.txt/.docx/.pdf/.xlsx file, or a directory "
            "containing exactly one file of a supported format"
        ),
    )
    parser.add_argument("out_dir", type=Path, help="output directory (created if missing)")
    parser.add_argument("--purpose", default="Представить решение", help="deck purpose")
    parser.add_argument("--audience", default="эксперты и жюри", help="target audience")
    parser.add_argument("--language", default="ru", help="deck language")
    parser.add_argument(
        "--slides",
        type=int,
        default=12,
        dest="target_slide_count",
        help="target slide count (3-40)",
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help=(
            "enable the LLM/VLM path via the configured gateway "
            "(DECKDNA_MOCK_PROVIDER=false + DECKDNA_PROVIDER_BASE_URL / "
            "DECKDNA_PROVIDER_API_KEY / DECKDNA_MODEL_TEXT[/_VISION]); "
            "off by default (deterministic path). With the default mock "
            "provider this flag exercises the same code path offline."
        ),
    )
    parser.add_argument(
        "--strategies",
        default=None,
        help=(
            "comma-separated override of the strategy set (default: all three — "
            "faithful,balanced,visual — as the skill contract requires; this flag "
            "is for debugging a single strategy only, not normal use)"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    strategies: list[Strategy] | None = None
    if args.strategies:
        try:
            strategies = [Strategy(s.strip()) for s in args.strategies.split(",") if s.strip()]
        except ValueError as exc:
            print(  # noqa: T201 -- CLI output, not debug logging
                json.dumps(
                    {
                        "error": {
                            "code": "invalid_input",
                            "message": f"--strategies: {exc}",
                            "stage": "skill.input",
                        }
                    },
                    ensure_ascii=False,
                )
            )
            return 1

    summary = run_skill(
        template=args.template,
        content=args.content,
        out_dir=args.out_dir,
        purpose=args.purpose,
        audience=args.audience,
        language=args.language,
        target_slide_count=args.target_slide_count,
        use_llm=args.llm,
        strategies=strategies,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))  # noqa: T201
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
