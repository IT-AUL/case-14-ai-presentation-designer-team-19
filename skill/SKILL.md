# DeckDNA — portable agent skill

One entrypoint, run three times internally, no manual editing: an
arbitrary PPTX/POTX template plus a content file (or a directory holding
exactly one) become three template-compliant, editable presentation
variants — **faithful**, **balanced**, **visual** — each exported as
native PPTX, PDF and HTML, each with a Quality Passport.

This file documents `skill/run_skill.py`; see `skill/manifest.yaml` for
the machine-readable capability and MCP declaration.

## Run it

Works from any working directory. No `pip install` required (it
bootstraps `sys.path` the same way `tests/conftest.py` does) — though an
installed environment (`pip install -e .`) works identically:

```bash
python3 skill/run_skill.py TEMPLATE CONTENT OUT_DIR [options]
# or, from the repo root, as a module:
python3 -m skill.run_skill TEMPLATE CONTENT OUT_DIR [options]
```

### Positional arguments

| Argument   | Meaning |
|------------|---------|
| `TEMPLATE` | Path to a `.pptx`/`.potx` file. **Any** template — nothing in the pipeline is keyed to the three organizer benchmark templates (`vk_tech`, `vk_workspace`, `lct2026_submission`); those are development fixtures only. |
| `CONTENT`  | Path to a single content file (`.json`, `.md`, `.markdown`, `.txt`, `.docx`, `.pdf`, `.xlsx`), **or** a directory containing exactly one such file. A directory with zero or more than one candidate is a typed `invalid_input` error, not a guess. |
| `OUT_DIR`  | Output directory (created if missing). Holds `summary.json` at the top plus one subdirectory per strategy. **Must be empty** (or contain only a `summary.json` left by an earlier failed run) — a used directory is rejected up front so stale artifacts can never masquerade as new output; reruns get a fresh destination. |

### Options

| Flag | Default | Meaning |
|------|---------|---------|
| `--purpose TEXT` | `Представить решение` | `Brief.purpose` |
| `--audience TEXT` | `эксперты и жюри` | `Brief.audience` |
| `--language TEXT` | `ru` | `Brief.language` |
| `--slides N` | `12` | `Brief.target_slide_count` (3–40) |
| `--llm` | off | Enables the LLM/VLM path (LLM story planning + per-slide VLM contextual audit) via the configured `ModelGateway`. Requires `DECKDNA_MOCK_PROVIDER=false` plus `DECKDNA_PROVIDER_BASE_URL` / `DECKDNA_PROVIDER_API_KEY` / `DECKDNA_MODEL_TEXT` (and `DECKDNA_MODEL_VISION` for the contextual audit) pointing at a real OpenAI-compatible endpoint. With the default mock provider (`DECKDNA_MOCK_PROVIDER=true`, the default), `--llm` exercises the same code path fully offline against `MockProvider` — useful to prove the wiring without a live key. |
| `--strategies faithful,balanced` | all three | **Debug-only escape hatch.** The skill's contract is all three strategies per run; only use this to iterate on one strategy during development. |

### Exit codes

`0` only if all three strategies (or the requested subset) completed
with `status: "ok"` (PPTX + PDF + HTML + Quality Passport all produced).
Non-zero (`1`) for anything else: any failed strategy, a degraded one
(HTML export failed but PPTX/PDF/passport exist), a bad template/content
path, an ambiguous content directory, or missing LLM provider
configuration when `--llm` is set. **A non-zero exit never means the run
produced nothing usable** — check `summary.json`'s per-variant `status`
and `artifacts` before deciding what to keep; partial success is
reported honestly, never silently upgraded to "ok".

## Outputs

```
OUT_DIR/
  summary.json          # machine-readable index of the whole run
  faithful/
    deck.pptx
    deck.pdf
    quality-passport.json
    html/index.html      # + assets/: media/fonts, text/tables as DOM, charts as SVG
  balanced/  (same shape)
  visual/    (same shape)
```

`summary.json`:

```jsonc
{
  "skill": {"id": "deckdna", "version": "0.1.0"},
  "started_at": "2026-09-24T12:00:00+00:00",
  "finished_at": "2026-09-24T12:03:41+00:00",
  "duration_seconds": 221.4,
  "input": {"template": "...", "content": "...", "content_resolved": "..."},
  "brief": {"purpose": "...", "audience": "...", "language": "ru", "target_slide_count": 12},
  "llm_enabled": false,
  "strategies_requested": ["faithful", "balanced", "visual"],
  "out_dir": "...",
  "status": "ok",               // "ok" | "partial" | "failed"
  "variants": [
    {
      "strategy": "faithful",
      "status": "ok",           // "ok" | "degraded" | "failed"
      "duration_seconds": 71.2,
      "slides_out": 12,
      "planner": "deterministic",
      "issues_by_severity": {"warning": 3},
      "editability": { /* QualityPassport.metrics.editability, incl. pei_level */ },
      "dropped_units": {},         // compose_report.dropped_units — planned content the
                                   // composer could not place (e.g. text_unplaced); any
                                   // nonzero count degrades the variant even if every
                                   // artifact was produced — a deck that silently omits
                                   // supplied content is not a clean "ok"
      "compose_warnings": [],      // compose_report.warnings, verbatim
      "artifacts": {
        "pptx": {"path": "faithful/deck.pptx", "bytes": 4831221, "sha256": "..."},
        "pdf":  {"path": "faithful/deck.pdf",  "bytes": 902113,  "sha256": "..."},
        "quality_passport": {"path": "faithful/quality-passport.json", "bytes": 5122, "sha256": "..."},
        "html": {"path": "faithful/html/index.html", "dir": "faithful/html", "bytes": 8811, "sha256": "..."}
      }
    }
    // balanced, visual — same shape
  ]
}
```

Artifact paths are relative to `OUT_DIR` (portable — the run can be
moved or read from another machine). No artifact's raw bytes are ever
embedded in `summary.json`, only path/size/sha256; deck files stay on
disk where they were written.

On an **early failure** (bad template path, unresolved/ambiguous
content, invalid brief, missing LLM provider config), `summary.json` is
still written, with `"status": "failed"`, `"variants": []` and a top-level
`"error"` envelope (`code`/`message`/`stage`/`details` — the same shape
`DeckDNAError.to_envelope()` uses everywhere else in this codebase).
**Secrets never appear in output**: neither `summary.json` nor stdout
ever includes an API key, bearer token or provider URL — provider
failures surface only as `{"code": "provider_unavailable", "message":
"provider request failed: <ExceptionClassName>", ...}` (see
`deckdna.providers.openai_compat`), and `--llm`'s presence is reported
only as a boolean (`llm_enabled`).

## Execution Guarantees

- **Clean output destination.** `run_skill.py` requires an empty `OUT_DIR`
  (or one holding only a `summary.json` from an earlier preflight check) so
  artifacts from previous runs never mix with new output.
- **Full three-strategy output by default.** Standard invocation builds all
  three variants (`faithful`, `balanced`, `visual`) in a single run;
  `--strategies` is available when iterating on a specific strategy.
- **Interactive MCP and REST modes.** `run_skill.py` is a synchronous
  single-process CLI entrypoint. Step-by-step tool calls, SSE progress
  streaming, interactive plan editing, and targeted repair are exposed via
  `deckdna mcp` (`stdio` / `http`) and the FastAPI server (`deckdna.api.app`).
- **Explicit user-selected repair.** `run_skill.py` outputs audited variants
  and their Quality Passports; subsequent geometric or stylistic repair
  actions can be applied via `deckdna repair` or the API/MCP `repair_issues`
  tool.

## Verifying this yourself

```bash
python3 skill/run_skill.py \
  tests/fixtures/pptx/synthetic_unseen.pptx \
  tests/fixtures/content/poc_article.md \
  /tmp/deckdna-skill-demo --slides 10
echo "exit=$?"
cat /tmp/deckdna-skill-demo/summary.json
```

`tests/fixtures/pptx/synthetic_unseen*.pptx` are synthetic fixtures with
no organizer branding — they exist specifically to prove the pipeline
was never keyed to the three benchmark templates. See
`tests/skill/test_run_skill.py` for the same check as an automated,
subprocess-level test (including a from-another-cwd invocation).
