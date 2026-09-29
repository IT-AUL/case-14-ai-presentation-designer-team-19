"""Метрики паспорта, добавленные по contract D3 (deckdna.evaluation.measures)."""

from __future__ import annotations

from deckdna.audit.issues import AuditIssue
from deckdna.evaluation import measures


def _issue(rule: str, slide: int) -> AuditIssue:
    return AuditIssue(
        rule_code=rule,
        severity="warning",
        message="m",
        audit_run_id="run",
        id=f"{rule}:{slide}",
        slide_index=slide,
    )


class TestNumbers:
    def test_extracts_only_significant_numbers(self):
        found = measures.extract_numbers("Шаг 1: рост на 30 % до 1 200 000 ₽, доля 12,5% и 2 млн")
        assert "30%" in found and "1200000₽" in found
        assert "12.5%" in found and "2млн" in found
        assert "1" not in found  # нумерация — не факт

    def test_formatting_variants_are_the_same_number(self):
        assert measures.extract_numbers("1 200") == measures.extract_numbers("1 200")
        assert measures.extract_numbers("12,50") == measures.extract_numbers("12.5")

    def test_verified_and_failed_against_source(self):
        check = measures.check_numbers(
            "Выручка выросла на 30% до 1 200. Клиентов 4500.",
            "В 2025 году выручка выросла на 30 % и достигла 1200.",
        )
        assert check.verified == 2 and check.failed == 1
        assert check.failed_numbers == ("4500",)

    def test_unit_written_as_a_word_still_supports_the_number(self):
        assert measures.check_numbers("рост 30%", "рост на 30 процентов").failed == 0

    def test_no_numbers_means_nothing_to_fail(self):
        check = measures.check_numbers("Без цифр", "Тоже без цифр")
        assert (check.verified, check.failed) == (0, 0)

    def test_global_match_does_not_prove_claim_context(self):
        check = measures.check_numbers(
            "Выручка выросла на 30%.",
            "Доля возвратов составила 30%. Выручка снизилась.",
        )
        assert check.verified == 1
        assert check.failed == 0


class TestStyleFidelity:
    def test_share_of_slides_without_violations(self):
        issues = [
            _issue("template.color_palette", 0),
            _issue("template.color_palette", 0),  # тот же слайд — считается один раз
            _issue("template.font_scale", 1),
            _issue("template.font_family", 2),
            _issue("text.overflow", 3),  # не про стиль
        ]
        fid = measures.style_fidelity(issues, slide_count=4)
        assert fid == {
            "palette_compliance": 0.75,
            "font_compliance": 0.5,
            "layout_origin_compliance": 1.0,
            "anchor_compliance": 1.0,
        }
        assert measures.style_fidelity_score(fid) == 0.8125

    def test_empty_deck_has_no_fidelity(self):
        assert measures.style_fidelity([], 0) is None
        assert measures.style_fidelity_score(None) is None


class TestUsage:
    def test_delta_between_snapshots(self):
        before = {"m": {"calls": 2, "input_tokens": 100, "output_tokens": 50}}
        after = {
            "m": {"calls": 5, "input_tokens": 300, "output_tokens": 90},
            "v": {"calls": 1, "input_tokens": 10, "output_tokens": 0},
        }
        d = measures.usage_delta(before, after)
        assert d["model_calls"] == 4 and d["total_tokens"] == 250
        assert {m["model_id"] for m in d["per_model"]} == {"m", "v"}

    def test_no_model_means_zeros_not_none(self):
        d = measures.usage_delta({}, {})
        assert d == {"total_tokens": 0, "model_calls": 0, "per_model": []}
        assert measures.usage_snapshot(None) == {}

    def test_openai_provider_counts_calls_and_tokens(self):
        from deckdna.providers.openai_compat import OpenAICompatibleProvider
        from deckdna.providers.profiles import UsageSource

        p = OpenAICompatibleProvider("http://x", "k", model_text="t")
        assert isinstance(p, UsageSource)
        p._record_usage("t", {"usage": {"prompt_tokens": 7, "completion_tokens": 3}})
        p._record_usage("t", {})  # без блока usage вызов всё равно считается
        assert p.usage_report() == {"t": {"calls": 2, "input_tokens": 7, "output_tokens": 3}}
