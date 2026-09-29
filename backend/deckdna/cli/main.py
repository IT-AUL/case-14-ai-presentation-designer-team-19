"""deckdna CLI — thin client over the DeckDNA pipeline."""


import json
import sys
import zipfile
from collections import Counter
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from deckdna.errors import DeckDNAError
from deckdna.logging_setup import configure_logging

# CLI output contract is a single JSON blob on stdout (parsed by callers,
# e.g. tests do `json.loads(result.output)`) -- logs go to stderr so they
# never corrupt it. See logging_setup.py / ADR-013 for why this exists.
configure_logging(stream=sys.stderr)

app = typer.Typer(help="DeckDNA — presentation compiler CLI", no_args_is_help=True)


def _fail(exc: DeckDNAError) -> typer.Exit:
    typer.echo(json.dumps(exc.to_envelope(), ensure_ascii=False))
    return typer.Exit(code=1)


def _raise_package_blocker(issues: list, *, stage: str) -> None:
    """integrity.package blocker -> ошибка CLI (правило аудита сообщает issue,
    но CLI-контракт для нечитаемого файла — ненулевой выход с package_corrupt)."""
    from deckdna.audit.basic import RULE_PACKAGE

    for issue in issues:
        if issue.rule_code == RULE_PACKAGE:
            raise _fail(
                DeckDNAError(
                    code="package_corrupt",
                    message=issue.message,
                    stage=stage,
                )
            )


@app.command()
def version() -> None:
    """Print version."""
    typer.echo("deckdna 0.1.0")


@app.command()
def serve() -> None:
    """Run the API server locally."""
    import uvicorn

    uvicorn.run("deckdna.api.app:app", host="0.0.0.0", port=8000)  # noqa: S104


@app.command(name="poc-compose")
def poc_compose(
    template: Annotated[
        Path, typer.Argument(exists=True, readable=True, help="template .pptx")
    ],
    deck_plan: Annotated[
        Path, typer.Argument(exists=True, readable=True, help="DeckPlan JSON")
    ],
    out: Annotated[Path, typer.Argument(help="output .pptx path")] = Path("out.pptx"),
) -> None:
    """POC slice of the compiler: exemplar-per-slide + DeckPlan -> out.pptx.

    No model calls — proves the relationship-safe multi-slide clone path
    end to end. Input is a DeckPlan JSON (schemas/deck-plan.schema.json).
    """
    from deckdna.errors import DeckDNAError
    from deckdna.pptx.composing.minimal import generate_deck

    try:
        report = generate_deck(
            template, json.loads(deck_plan.read_text(encoding="utf-8")), out
        )
    except DeckDNAError as exc:
        typer.echo(json.dumps(exc.to_envelope(), ensure_ascii=False))
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(report, ensure_ascii=False, indent=2))


@app.command(name="inspect-template")
def inspect_template(
    template: Annotated[
        Path, typer.Argument(exists=True, readable=True, help="template .pptx/.potx")
    ],
    out: Annotated[
        Path | None, typer.Option("--out", "-o", help="also write the JSON report here")
    ] = None,
) -> None:
    """Forensic template autopsy: package census, layout usage, fonts."""
    from deckdna.template.autopsy import analyze_template

    try:
        report = analyze_template(template)
    except (OSError, zipfile.BadZipFile) as exc:
        raise _fail(
            DeckDNAError(
                code="package_corrupt",
                message=f"cannot read template {template.name}: {exc}",
                stage="template.autopsy",
            )
        ) from exc
    payload = json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
    typer.echo(payload)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload + "\n", encoding="utf-8")


@app.command(name="export")
def export_deck(
    deck: Annotated[Path, typer.Argument(exists=True, readable=True, help="deck .pptx")],
    fmt: Annotated[
        str, typer.Option("--format", "-f", help="output format: png | pdf | html")
    ] = "png",
    out: Annotated[
        Path | None,
        typer.Option(
            "--out", "-o", help="output directory (png/html) or file path (pdf)"
        ),
    ] = None,
) -> None:
    """Headless render via soffice+pdftoppm (settings.soffice_path on PATH)."""
    fmt = fmt.lower()
    if fmt == "pdf":
        from deckdna.pptx.exporting.render import render_pdf

        target = out or deck.with_suffix(".pdf")
        try:
            path = render_pdf(deck, target)
        except DeckDNAError as exc:
            raise _fail(exc) from exc
        typer.echo(
            json.dumps({"format": "pdf", "out": str(path)}, ensure_ascii=False, indent=2)
        )
    elif fmt == "html":
        from deckdna.pptx.exporting.render import render_html

        out_dir = out or Path(f"{deck.stem}-html")
        try:
            index = render_html(deck, out_dir)
        except DeckDNAError as exc:
            raise _fail(exc) from exc
        typer.echo(
            json.dumps(
                {"format": "html", "out_dir": str(out_dir), "index": str(index)},
                ensure_ascii=False,
                indent=2,
            )
        )
    elif fmt == "png":
        from deckdna.pptx.exporting.render import render_slides_png

        out_dir = out or Path("renders")
        try:
            slides = render_slides_png(deck, out_dir)
        except DeckDNAError as exc:
            raise _fail(exc) from exc
        typer.echo(
            json.dumps(
                {
                    "format": "png",
                    "out_dir": str(out_dir),
                    "slides": [str(p) for p in slides],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        raise _fail(
            DeckDNAError(
                code="invalid_input",
                message=f"unsupported export format: {fmt!r} (expected png|pdf|html)",
                stage="cli.export",
            )
        )


@app.command(name="audit")
def audit_deck_cmd(
    deck: Annotated[Path, typer.Argument(exists=True, readable=True, help="deck .pptx")],
    out: Annotated[
        Path | None, typer.Option("--out", "-o", help="also write the JSON report here")
    ] = None,
) -> None:
    """Render Arena: deterministic audit rules over a finished .pptx."""
    from pptx.exc import PackageNotFoundError

    from deckdna.audit.basic import audit_deck

    try:
        issues = audit_deck(deck)
    except (OSError, zipfile.BadZipFile, PackageNotFoundError) as exc:
        raise _fail(
            DeckDNAError(
                code="package_corrupt",
                message=f"cannot read deck {deck.name}: {exc}",
                stage="audit.basic",
            )
        ) from exc
    _raise_package_blocker(issues, stage="audit.basic")
    by_rule = Counter(i.rule_code for i in issues)
    by_severity = Counter(i.severity for i in issues)
    payload = json.dumps(
        {
            "deck": str(deck),
            "issue_count": len(issues),
            "issues": [i.to_dict() for i in issues],
            "summary": {
                "by_rule_code": dict(by_rule.most_common()),
                "by_severity": dict(by_severity.most_common()),
            },
        },
        ensure_ascii=False,
        indent=2,
    )
    typer.echo(payload)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload + "\n", encoding="utf-8")


@app.command(name="generate")
def generate_cmd(
    template: Annotated[
        Path, typer.Argument(exists=True, readable=True, help="template .pptx/.potx")
    ],
    content: Annotated[
        Path, typer.Argument(exists=True, readable=True, help="content file (.json/.md/.txt)")
    ],
    out_dir: Annotated[Path, typer.Argument(help="output directory")] = Path("out"),
    purpose: Annotated[
        str, typer.Option("--purpose", help="deck objective (one sentence)")
    ] = "Представить решение",
    audience: Annotated[
        str, typer.Option("--audience", help="target audience")
    ] = "эксперты и жюри",
    language: Annotated[str, typer.Option("--language", help="deck language")] = "ru",
    slides: Annotated[
        int, typer.Option("--slides", help="target slide count (10–15 default profile)")
    ] = 12,
    llm: Annotated[
        bool,
        typer.Option(
            "--llm",
            help="enable the LLM/VLM path: LLM story planning (prompt 'storyline') "
            "plus a per-slide VLM contextual audit, via the configured provider "
            "gateway. Requires a real endpoint — DECKDNA_MOCK_PROVIDER=false plus "
            "DECKDNA_PROVIDER_BASE_URL / DECKDNA_PROVIDER_API_KEY / "
            "DECKDNA_MODEL_*. Off by default; with the default mock provider the "
            "flag exercises the same code path offline (MockProvider).",
        ),
    ] = False,
    strategy: Annotated[
        str,
        typer.Option(
            "--strategy",
            help="deck variant: 'faithful' keeps source structure (no synthesized "
            "agenda/recap slides), 'balanced' is the default profile, 'visual' "
            "prefers media-rich exemplars and caps body text density, 'custom' "
            "currently maps to balanced.",
        ),
    ] = "balanced",
) -> None:
    """Full pipeline: content + template -> deck.pptx, deck.pdf, QualityPassport."""
    from deckdna.contracts.deck_plan import Brief
    from deckdna.contracts.variant_spec import Strategy
    from deckdna.generation.pipeline import generate
    from deckdna.providers.factory import build_gateway

    try:
        variant = Strategy(strategy)
    except ValueError as exc:
        raise _fail(
            DeckDNAError(
                "invalid_input",
                f"--strategy: {exc} — expected faithful | balanced | visual | custom",
                stage="cli",
            )
        ) from exc

    try:
        brief = Brief(
            purpose=purpose,
            audience=audience,
            language=language,
            target_slide_count=slides,
        )
    except ValidationError as exc:
        raise _fail(
            DeckDNAError("invalid_input", f"invalid brief: {exc}", stage="planning")
        ) from exc
    gateway = build_gateway() if llm else None
    try:
        report = generate(
            template, content, brief, out_dir, gateway=gateway, strategy=variant
        )
    except DeckDNAError as exc:
        raise _fail(exc) from exc

    by_severity = Counter(i["severity"] for i in report["audit_issues"])
    typer.echo(
        json.dumps(
            {
                "artifacts": report["artifacts"],
                "slides": report["slides_out"],
                "strategy": report["strategy"],
                "planner": report["planner"],
                "contextual_audit": report["contextual_audit"],
                "duration_seconds": report["duration_seconds"],
                "issues": {
                    "total": len(report["audit_issues"]),
                    "by_severity": dict(by_severity.most_common()),
                },
                "editability": report["quality_passport"]["metrics"]["editability"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command(name="ingest")
def ingest_content(
    content: Annotated[
        Path,
        typer.Argument(exists=True, readable=True, help="content file (.json/.md/.txt)"),
    ],
    out: Annotated[
        Path | None, typer.Option("--out", "-o", help="also write the ContentPack JSON here")
    ] = None,
) -> None:
    """Normalize a content file into a ContentPack (schema-canonical JSON)."""
    from deckdna.contracts.serialize import to_schema_json
    from deckdna.ingestion.content_parsers import parse_file

    try:
        pack = parse_file(content)
    except DeckDNAError as exc:
        raise _fail(exc) from exc
    except OSError as exc:
        raise _fail(
            DeckDNAError(
                code="invalid_input",
                message=f"cannot read content {content.name}: {exc}",
                stage="cli.ingest",
            )
        ) from exc
    payload = to_schema_json(pack, indent=2)
    typer.echo(payload)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload + "\n", encoding="utf-8")


# официальное требование ТЗ: генерация ≤5 минут НА ОДНУ колоду
BENCHMARK_BUDGET_SECONDS = 300.0
ORGANIZER_TEMPLATES = ("vk_tech.pptx", "vk_workspace.pptx", "vk_education.pptx")
# generalization check: runs when present in --fixtures-dir, skipped silently when absent
UNSEEN_TEMPLATE = "synthetic_unseen.pptx"


def _format_issues(by_severity: dict[str, int]) -> str:
    if not by_severity:
        return "-"
    order = ("blocker", "error", "warning", "info")
    return " ".join(f"{sev[0]}={by_severity[sev]}" for sev in order if by_severity.get(sev))


@app.command(name="benchmark")
def benchmark_cmd(
    content: Annotated[
        Path,
        typer.Argument(
            exists=True,
            readable=True,
            help="content file (.json/.md/.txt) shared by all template runs",
        ),
    ] = Path("tests/fixtures/content/poc_article.md"),
    fixtures_dir: Annotated[
        Path,
        typer.Option("--fixtures-dir", exists=True, help="dir with organizer .pptx fixtures"),
    ] = Path("tests/fixtures/pptx"),
    work_dir: Annotated[
        Path,
        typer.Option("--work-dir", help="per-template generation output root"),
    ] = Path("out/benchmark"),
    out: Annotated[
        Path | None, typer.Option("--out", "-o", help="also write the JSON report here")
    ] = None,
    budget: Annotated[
        float,
        typer.Option("--budget", help="per-deck time budget in seconds (verdict threshold)"),
    ] = BENCHMARK_BUDGET_SECONDS,
    purpose: Annotated[
        str, typer.Option("--purpose", help="deck objective (one sentence)")
    ] = "Представить решение",
    audience: Annotated[
        str, typer.Option("--audience", help="target audience")
    ] = "эксперты и жюри",
    language: Annotated[str, typer.Option("--language", help="deck language")] = "ru",
    slides: Annotated[
        int, typer.Option("--slides", help="target slide count (10–15 default profile)")
    ] = 12,
    variants: Annotated[
        str,
        typer.Option(
            "--variants",
            help="comma-separated strategies to time per template "
            "(faithful,balanced,visual,custom; default 'balanced'). "
            "'faithful,balanced,visual' × 3 organizer templates yields "
            "the official 9 decks (OR-027).",
        ),
    ] = "balanced",
) -> None:
    """Time `generate` on every organizer template; verdict vs the 5-min budget.

    The synthetic unseen fixture joins the run whenever it exists in
    --fixtures-dir — organizer templates are the required baseline
    (missing = error row), the unseen one is an extra generalization
    check, so its absence is not an error.
    """
    from deckdna.contracts.deck_plan import Brief
    from deckdna.contracts.variant_spec import Strategy
    from deckdna.generation.pipeline import generate

    try:
        strategies = [Strategy(s.strip()) for s in variants.split(",") if s.strip()]
    except ValueError as exc:
        raise _fail(
            DeckDNAError(
                "invalid_input",
                f"--variants: {exc} — expected faithful | balanced | visual | custom",
                stage="cli",
            )
        ) from exc
    if not strategies:
        raise _fail(DeckDNAError("invalid_input", "--variants is empty", stage="cli"))

    brief = Brief(
        purpose=purpose,
        audience=audience,
        language=language,
        target_slide_count=slides,
    )
    names = list(ORGANIZER_TEMPLATES)
    if (fixtures_dir / UNSEEN_TEMPLATE).exists():
        names.append(UNSEEN_TEMPLATE)
    multi = len(strategies) > 1
    rows: list[dict[str, object]] = []
    for name in names:
        template = fixtures_dir / name
        if not template.exists():
            for strategy in strategies:
                rows.append(
                    {
                        "template": name,
                        "strategy": strategy.value,
                        "status": "error",
                        "error": {
                            "code": "invalid_input",
                            "message": f"template not found: {template}",
                        },
                    }
                )
            continue
        for strategy in strategies:
            row: dict[str, object] = {
                "template": f"{name}:{strategy.value}" if multi else name,
                "strategy": strategy.value,
                "resolved": str(template.resolve()),
            }
            out_key = f"{Path(name).stem}-{strategy.value}" if multi else Path(name).stem
            try:
                report = generate(
                    template,
                    content,
                    brief,
                    work_dir / out_key,
                    strategy=strategy,
                )
            except DeckDNAError as exc:
                row["status"] = "error"
                row["error"] = exc.to_envelope()["error"]
            except Exception as exc:  # noqa: BLE001 — one failing run must not stop the rest
                row["status"] = "error"
                row["error"] = {
                    "code": "internal_error",
                    "message": f"{type(exc).__name__}: {exc}",
                }
            else:
                duration = report["duration_seconds"]
                by_severity = Counter(i["severity"] for i in report["audit_issues"])
                row.update(
                    {
                        "status": "ok",
                        "duration_seconds": duration,
                        "slides_out": report["slides_out"],
                        "pei_level": report["quality_passport"]["metrics"]["editability"][
                            "pei_level"
                        ],
                        "issues_by_severity": dict(by_severity.most_common()),
                        "verdict": "ok" if duration <= budget else "over_budget",
                    }
                )
            rows.append(row)

    widths = (26, 9, 6, 5, 12, 11)
    lines = [
        "template".ljust(widths[0])
        + " seconds".rjust(widths[1])
        + " slides".rjust(widths[2])
        + " pei".rjust(widths[3])
        + " issues".rjust(widths[4])
        + " verdict".rjust(widths[5]),
    ]
    for row in rows:
        if row["status"] == "ok":
            cells = (
                str(row["duration_seconds"]),
                str(row["slides_out"]),
                str(row["pei_level"]),
                _format_issues(row["issues_by_severity"]),  # type: ignore[arg-type]
                str(row["verdict"]),
            )
        else:
            err = row["error"]  # type: ignore[index]
            cells = ("-", "-", "-", str(err["code"]), "error")
        lines.append(
            str(row["template"]).ljust(widths[0])
            + cells[0].rjust(widths[1])
            + cells[1].rjust(widths[2])
            + cells[2].rjust(widths[3])
            + cells[3].rjust(widths[4])
            + cells[4].rjust(widths[5])
        )
    verdicts = Counter(
        str(row.get("verdict", row["status"])) for row in rows
    )
    lines.append(
        f"budget={budget:.0f}s → "
        + ", ".join(f"{k}: {v}" for k, v in verdicts.most_common())
    )
    typer.echo("\n".join(lines))

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "budget_seconds": budget,
            "content": str(content),
            "brief": brief.model_dump(mode="json", exclude_none=True),
            "templates": rows,
            "summary": dict(verdicts.most_common()),
        }
        out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


@app.command(name="repair")
def repair_cmd(
    deck: Annotated[Path, typer.Argument(exists=True, readable=True, help="deck .pptx")],
    out: Annotated[
        Path | None,
        typer.Option("--out", "-o", help="output .pptx (default: <name>-repaired.pptx)"),
    ] = None,
    protected_slides: Annotated[
        str | None,
        typer.Option(
            "--protected-slides",
            help="0-based slide indices to keep verbatim, comma-separated "
            "(default: protected_slide_indices from generation config)",
        ),
    ] = None,
) -> None:
    """Bounded Repair: audit -> plan_repairs -> apply_repairs -> re-audit."""
    from pptx.exc import PackageNotFoundError

    from deckdna.audit.basic import audit_deck
    from deckdna.planning.config import load_generation_config
    from deckdna.repair.apply import apply_repairs
    from deckdna.repair.planner import plan_repairs

    if protected_slides is None:
        protected = load_generation_config().protected_slide_indices
    else:
        try:
            protected = [
                int(p) for p in protected_slides.split(",") if p.strip()
            ]
        except ValueError as exc:
            raise _fail(
                DeckDNAError(
                    code="invalid_input",
                    message=(
                        f"--protected-slides expects comma-separated ints, "
                        f"got {protected_slides!r}"
                    ),
                    stage="cli.repair",
                )
            ) from exc

    try:
        issues_before = audit_deck(deck)
    except (OSError, zipfile.BadZipFile, PackageNotFoundError) as exc:
        raise _fail(
            DeckDNAError(
                code="package_corrupt",
                message=f"cannot read deck {deck.name}: {exc}",
                stage="audit.basic",
            )
        ) from exc
    _raise_package_blocker(issues_before, stage="audit.basic")
    actions = plan_repairs(issues_before)
    target = out or deck.with_name(f"{deck.stem}-repaired{deck.suffix}")
    try:
        report = apply_repairs(deck, actions, target, protected=protected)
    except DeckDNAError as exc:
        raise _fail(exc) from exc
    try:
        issues_after = audit_deck(target)
    except (OSError, zipfile.BadZipFile, PackageNotFoundError) as exc:
        raise _fail(
            DeckDNAError(
                code="package_corrupt",
                message=f"cannot re-audit repaired deck {target.name}: {exc}",
                stage="audit.basic",
            )
        ) from exc

    typer.echo(
        json.dumps(
            {
                "deck": str(deck),
                "out": str(target),
                "issues": {
                    "before": len(issues_before),
                    "after": len(issues_after),
                    "by_rule_before": dict(
                        Counter(i.rule_code for i in issues_before).most_common()
                    ),
                    "by_rule_after": dict(
                        Counter(i.rule_code for i in issues_after).most_common()
                    ),
                },
                "protected_indices": sorted(set(protected)),
                "actions": {
                    "total": len(report.results),
                    "applied": report.applied,
                    "skipped": report.skipped,
                    "failed": report.failed,
                    "not_implemented": report.not_implemented,
                    "by_type": report.by_type,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command(name="check-models")
def check_models_cmd() -> None:
    """OR-016: validate configs/model_licenses.yaml against declared models."""
    from deckdna.evaluation.model_licenses import check, format_report

    report = check()
    typer.echo(format_report(report))
    if not report.ok:
        raise typer.Exit(code=1)


# Reserved command-surface stubs — kept so `--help` shows the frozen surface.
for _cmd in ["init"]:

    @app.command(name=_cmd)
    def _stub() -> None:  # noqa: B023
        typer.echo("not implemented yet")
        raise typer.Exit(code=2)
