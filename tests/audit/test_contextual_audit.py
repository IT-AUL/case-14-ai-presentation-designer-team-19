"""run_contextual_audit: VLM-verdict → AuditIssue (моковый gateway, без сети).

Рендер слайдов идёт через реальный soffice→pdftoppm — тесты помечены
медленными относительно unit-сюиты, но держатся в рамках секунд
(1 слайд на фикстуре).
"""

from __future__ import annotations

import asyncio

from deckdna.audit.contextual import (
    PROMPT_NAME,
    SlideChecksResult,
    run_contextual_audit,
)
from deckdna.errors import DeckDNAError
from deckdna.providers.mock import MockProvider

PPTX = "tests/fixtures/pptx/synthetic_unseen.pptx"


def _verdicts(*checks):
    return {
        "verdicts": [
            {
                "check": str(num),
                "verdict": verdict,
                "rationale": rationale,
                "confidence": confidence,
            }
            for num, verdict, rationale, confidence in checks
        ]
    }


async def test_fail_verdict_becomes_contextual_issue():
    """fail с confidence ≥ порога → issue с frozen rule_code, deterministic=False."""
    gw = MockProvider(
        fixtures={
            PROMPT_NAME: _verdicts(
                ("1", "fail", "Заголовок — тема, а не вывод", 0.92),
                ("2", "pass", "", 0.9),
                ("4", "uncertain", "текста недостаточно", 0.5),
            )
        }
    )
    issues = await run_contextual_audit(PPTX, gw, first=1, last=1)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.rule_code == "content.conclusion_title"
    assert issue.severity == "warning"
    assert issue.deterministic is False
    assert issue.slide_index == 0
    assert issue.confidence == 0.92
    assert issue.evidence[0]["kind"] == "model_verdict"
    # gateway вызван с промптом slide_checks и data-URL картинкой
    assert len(gw.calls) == 1
    assert gw.calls[0]["prompt"] == "slide_checks"
    assert gw.calls[0]["images"][0].startswith("data:image/png;base64,")


async def test_uncertain_and_low_confidence_are_not_issues():
    """uncertain — честная неопределённость, не issue; fail ниже порога — тоже."""
    gw = MockProvider(
        fixtures={
            PROMPT_NAME: _verdicts(
                ("4", "uncertain", "нет evidence на руках", 0.9),
                ("7", "fail", "возможная утечка", 0.3),
            )
        }
    )
    issues = await run_contextual_audit(
        PPTX, gw, first=1, last=1, confidence_threshold=0.5
    )
    assert issues == []


async def test_payload_carries_slide_context():
    """Провайдеру уходит slide_text/title_intent/evidence/language в payload."""
    seen: list[dict] = []

    def _fixture(payload):
        seen.append(payload)
        return _verdicts(("5", "pass", "", 1.0))

    gw = MockProvider(fixtures={PROMPT_NAME: _fixture})
    await run_contextual_audit(PPTX, gw, first=1, last=1, deck_language="ru")
    payload = seen[0]
    assert payload["deck_language"] == "ru"
    assert payload["slide_text"].strip()  # текст слайда реально извлечён
    assert payload["title_intent"] == ""  # deck_plan не передан — честно пусто
    assert payload["evidence_excerpt"] == ""


async def test_all_verdicts_map_to_frozen_rules():
    """Каждая проверка 1-10 → свой frozen rule_code с severity Appendix."""
    gw = MockProvider(
        fixtures={
            PROMPT_NAME: _verdicts(
                *[(str(n), "fail", f"r{n}", 0.9) for n in range(1, 11)]
            )
        }
    )
    issues = await run_contextual_audit(PPTX, gw, first=1, last=1)
    codes = {i.rule_code for i in issues}
    assert codes == {
        "content.conclusion_title",
        "content.title_alignment",
        "content.single_message",
        "content.source_support",
        "content.nonempty",
        "content.visual_relevance",
        "content.prompt_leakage",
        "content.spelling",
        "content.data_relevance",
        "deck.logical_flow",
    }
    by_code = {i.rule_code: i for i in issues}
    assert by_code["content.source_support"].severity == "blocker"
    assert by_code["content.title_alignment"].severity == "error"


async def test_result_type_is_structured():
    """fixture в виде dict валидируется в SlideChecksResult (как настоящий gateway)."""
    gw = MockProvider(fixtures={PROMPT_NAME: _verdicts(("5", "pass", "", 1.0))})
    out = await gw.vision_json(PROMPT_NAME, ["img"], {}, SlideChecksResult)
    assert isinstance(out, SlideChecksResult)


async def test_retries_once_on_structured_output_invalid():
    """Один сбойный ответ не роняет весь аудит — один retry того же запроса."""
    attempts = {"n": 0}

    def _flaky(payload):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise DeckDNAError(
                code="structured_output_invalid",
                message="provider returned invalid structured output",
                stage="providers",
            )
        return _verdicts(("5", "pass", "", 1.0))

    gw = MockProvider(fixtures={PROMPT_NAME: _flaky})
    issues = await run_contextual_audit(PPTX, gw, first=1, last=1)
    assert issues == []  # pass verdict — no issue, but the call succeeded on retry
    assert attempts["n"] == 2


async def test_second_failure_after_retry_still_raises():
    """Retry — не бесконечный: второй подряд structured_output_invalid фатален."""

    def _always_broken(payload):
        raise DeckDNAError(
            code="structured_output_invalid",
            message="provider returned invalid structured output",
            stage="providers",
        )

    gw = MockProvider(fixtures={PROMPT_NAME: _always_broken})
    try:
        await run_contextual_audit(PPTX, gw, first=1, last=1)
    except DeckDNAError as exc:
        assert exc.code == "structured_output_invalid"
    else:
        raise AssertionError("expected DeckDNAError to propagate after exhausted retry")


async def test_non_retryable_error_code_is_not_retried():
    """Другие коды ошибок (не structured_output_invalid) не глотаются retry-логикой."""
    attempts = {"n": 0}

    def _auth_failure(payload):
        attempts["n"] += 1
        raise DeckDNAError(
            code="provider_unavailable", message="HTTP 401", stage="providers"
        )

    gw = MockProvider(fixtures={PROMPT_NAME: _auth_failure})
    try:
        await run_contextual_audit(PPTX, gw, first=1, last=1)
    except DeckDNAError as exc:
        assert exc.code == "provider_unavailable"
    else:
        raise AssertionError("expected DeckDNAError to propagate")
    assert attempts["n"] == 1  # no retry attempted for a non-transient code


class _ConcurrencyTrackingGateway:
    """Fake gateway — измеряет одновременно летящие vision_json-вызовы.

    Каждый вызов реально спит (``asyncio.sleep``), поэтому пересечение
    вызовов во времени наблюдаемо: без конкурентности (последовательный
    ``for``-цикл) наблюдаемый максимум всегда был бы 1.
    """

    def __init__(self, *, delay: float = 0.05) -> None:
        self.delay = delay
        self.in_flight = 0
        self.max_in_flight = 0
        self.call_count = 0

    async def vision_json(self, prompt_name, images, payload, schema):
        self.call_count += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
            return SlideChecksResult(verdicts=[])
        finally:
            self.in_flight -= 1


async def test_slides_are_checked_concurrently_not_sequentially():
    """max_concurrent>1 реально даёт пересекающиеся во времени вызовы."""
    gw = _ConcurrencyTrackingGateway()
    issues = await run_contextual_audit(PPTX, gw, first=1, last=4, max_concurrent=4)
    assert issues == []
    assert gw.call_count == 4
    assert gw.max_in_flight > 1, "calls never overlapped — audit is still sequential"


async def test_max_concurrent_bounds_in_flight_calls():
    """max_concurrent реально ограничивает потолок, не просто документация."""
    gw = _ConcurrencyTrackingGateway()
    await run_contextual_audit(PPTX, gw, first=1, last=4, max_concurrent=2)
    assert gw.max_in_flight <= 2


async def test_concurrent_issues_stay_ordered_and_numbered_by_slide():
    """Issue id/seq остаются позиционными по слайду независимо от порядка завершения."""
    gw = MockProvider(
        fixtures={PROMPT_NAME: _verdicts(("5", "fail", "не по делу", 0.9))}
    )
    issues = await run_contextual_audit(PPTX, gw, first=1, last=4, max_concurrent=4)
    assert [i.slide_index for i in issues] == [0, 1, 2, 3]
    assert [i.id.rsplit("-", 1)[-1] for i in issues] == ["1", "2", "3", "4"]


async def test_contextual_issue_matches_frozen_schema():
    import json
    from pathlib import Path

    from jsonschema import validate

    gw = MockProvider(fixtures={PROMPT_NAME: _verdicts(("2", "fail", "missing formula", 0.9))})
    issues = await run_contextual_audit(PPTX, gw, first=2, last=2)
    assert issues[0].slide_index == 1  # same zero-based index as deterministic audit/UI
    schema = json.loads(await asyncio.to_thread(Path('schemas/audit-issue.schema.json').read_text))
    validate(issues[0].to_dict(), schema)


async def test_incomplete_audit_is_disclosed():
    gw = MockProvider(fixtures={PROMPT_NAME: _verdicts(("2", "uncertain", "no evidence", 0.5))})
    diagnostics = {}
    await run_contextual_audit(PPTX, gw, first=1, last=1, diagnostics=diagnostics)
    assert diagnostics == dict(
        requested_slides=1,
        completed_slides=1,
        unverified_slides=[],
        uncertain_checks=1,
        missing_checks=9,
        duplicate_checks=0,
        invalid_checks=0,
        complete=False,
    )
    diagnostics = {}
    await run_contextual_audit(PPTX, gw, first=1, last=1, budget_s=0, diagnostics=diagnostics)
    assert diagnostics['completed_slides'] == 0
    assert diagnostics['unverified_slides'] == [0]
    assert diagnostics['complete'] is False


async def test_complete_audit_requires_all_unique_checks():
    gw = MockProvider(
        fixtures={
            PROMPT_NAME: _verdicts(
                *[(str(number), "pass", "", 1.0) for number in range(1, 11)]
            )
        }
    )
    diagnostics = {}
    await run_contextual_audit(PPTX, gw, first=1, last=1, diagnostics=diagnostics)
    assert diagnostics["complete"] is True
    assert diagnostics["missing_checks"] == 0
    assert diagnostics["duplicate_checks"] == 0
    assert diagnostics["invalid_checks"] == 0


async def test_duplicate_and_unknown_checks_cannot_complete_audit():
    gw = MockProvider(
        fixtures={
            PROMPT_NAME: _verdicts(
                *[(str(number), "pass", "", 1.0) for number in range(1, 11)],
                ("4", "fail", "duplicate source-support failure", 0.9),
                ("99", "fail", "unknown check", 0.9),
            )
        }
    )
    diagnostics = {}
    issues = await run_contextual_audit(PPTX, gw, first=1, last=1, diagnostics=diagnostics)
    assert issues == [], "duplicate verdict must not manufacture an issue"
    assert diagnostics["missing_checks"] == 0
    assert diagnostics["duplicate_checks"] == 1
    assert diagnostics["invalid_checks"] == 1
    assert diagnostics["complete"] is False
