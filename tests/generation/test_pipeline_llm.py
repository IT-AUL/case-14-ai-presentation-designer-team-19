"""LLM/VLM-режим пайплайна: generate(gateway=...) через MockProvider.

Детерминированный путь (gateway=None) проверен в test_pipeline.py —
здесь проверяем, что передача gateway включает plan_deck_llm +
run_contextual_audit, а невалидный LLM-план честно откатывается.
"""

import shutil
from pathlib import Path

import pytest
from deckdna.contracts.deck_plan import Brief
from deckdna.generation.pipeline import _provenance_inputs, generate
from deckdna.ingestion.content_parsers import parse_file
from deckdna.planning.story_director import LLM_PLANNER_VERSION, PLANNER_VERSION, plan_deck
from deckdna.providers.mock import MockProvider

FIXTURE = Path("tests/fixtures/pptx/vk_tech_template.pptx")
CONTENT = Path("tests/fixtures/content/poc_article.md")
BRIEF = {
    "purpose": "Показать сквозной пайплайн DeckDNA",
    "audience": "эксперты VK Tech",
    "language": "ru",
    "target_slide_count": 12,
}

HAS_SOFFICE = shutil.which("soffice") is not None
needs_render = pytest.mark.skipif(
    not HAS_SOFFICE or not FIXTURE.exists(),
    reason="pipeline needs the organizer fixture + soffice (pdf/png render)",
)


def _storyline_fixture():
    """Callable-фикстура: per-batch валидный SlideOutlineBatch-ответ «LLM»,
    заземлённый на реальные evidence_ids из присланного payload."""

    def _respond(payload: dict) -> dict:
        nodes = payload["evidence_graph"]["nodes"]
        claim_ids = [n["id"] for n in nodes if n["type"] == "claim"] or [
            n["id"] for n in nodes
        ]
        ev = claim_ids[:1]
        return {
            "slides": [
                {
                    "index": hint["index"],
                    "purpose": hint["purpose"],
                    "title_intent": f"LLM: {hint['baseline_title']}",
                    "key_message": "Заключение по данным.",
                    "evidence_ids": ev,
                }
                for hint in payload["batch"]["hints"]
            ]
        }

    return _respond


def _broken_storyline_fixture():
    """Отвечает валидной схемой, но evidence_ids не существуют в графе —
    гейт заземления честно отклоняет КАЖДЫЙ слайд, весь план откатывается
    на детерминированный (эквивалент старого «gate rejects the plan»)."""

    def _respond(payload: dict) -> dict:
        return {
            "slides": [
                {
                    "index": hint["index"],
                    "purpose": hint["purpose"],
                    "title_intent": "x",
                    "key_message": "x",
                    "evidence_ids": ["ev_nonexistent"],
                }
                for hint in payload["batch"]["hints"]
            ]
        }

    return _respond


def _gateway(storyline=None, content_style_fixture=None) -> MockProvider:
    fixtures = {
        "storyline": storyline if storyline is not None else _storyline_fixture(),
        # VLM-вердикт: check 5 (content.nonempty) fail на каждом слайде
        "slide_checks": {
            "verdicts": [
                {
                    "check": "5",
                    "verdict": "fail",
                    "rationale": "VLM: слайд выглядит пустым",
                    "confidence": 0.9,
                }
            ]
        },
    }
    if content_style_fixture is not None:
        fixtures["content_style"] = content_style_fixture
    return MockProvider(fixtures=fixtures)


class _DistinctRoleMockProvider(MockProvider):
    """Различимые model_id по ролям — утечка stale-роли между прогонами
    стала бы видимой (id накапливаются в _used переиспользуемого gateway)."""

    def used_model_ids(self) -> dict[str, str]:
        return {role: f"mock-{role}" for role in self._used}


@needs_render
def test_shared_gateway_provenance_is_scoped_per_generate(monkeypatch, tmp_path):
    """Portable skill переиспользует один gateway на 3 варианта:
    used_model_ids() накапливается между generate() — паспорт каждой
    колоды обязан содержать только роли, чьи стадии приняты в ЭТОМ
    прогоне, а не накопленные за всё время жизни gateway.

    Пин на pipeline_version="v1" — тест проверяет storyline-специфичный
    provenance (см. assert ниже), с v3 (дефолт с 27.09) это deck_structure
    + content_writer вместо storyline."""
    import deckdna.generation.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "planning_pipeline_version", lambda: "v1")
    gateway = _DistinctRoleMockProvider(
        fixtures={
            "storyline": _storyline_fixture(),
            "slide_checks": {"verdicts": []},
        }
    )

    # Прогон 1: принятый LLM-план + VLM-аудит → text+vision честно.
    rep1 = generate(FIXTURE, CONTENT, dict(BRIEF), tmp_path / "v1", gateway=gateway)
    prov1 = rep1["quality_passport"]["provenance"]
    assert rep1["planner"] == LLM_PLANNER_VERSION
    assert prov1["prompt_versions"] == {
        "storyline": "2.0.1",
        "contextual_slide_audit": "1.1.0",
    }
    assert {p["model_id"] for p in prov1["model_profiles"]} == {
        "mock-text",
        "mock-vision",
    }

    # Прогон 2, тем же gateway: storyline отвергнута гейтом заземления
    # (без фикстуры MockProvider синтезирует "mock_evidence_ids_..." —
    # ни один не существует в графе) → deterministic fallback на КАЖДЫЙ
    # слайд; накопленная 'text'-роль из прогона 1 не должна просочиться —
    # стадия не приняла вывод модели.
    del gateway.fixtures["storyline"]
    rep2 = generate(FIXTURE, CONTENT, dict(BRIEF), tmp_path / "v2", gateway=gateway)
    prov2 = rep2["quality_passport"]["provenance"]
    assert rep2["planner"] == PLANNER_VERSION  # fallback, не LLM
    assert "storyline" not in prov2["prompt_versions"]
    assert prov2["prompt_versions"] == {"contextual_slide_audit": "1.1.0"}
    assert {p["model_id"] for p in prov2["model_profiles"]} == {"mock-vision"}

    # Контроль накопления: gateway действительно помнит обе роли —
    # фильтрация по принятым стадиям, а не по отсутствию записи.
    assert set(gateway.used_model_ids()) == {"text", "vision"}


@needs_render
def test_llm_mode_uses_llm_planner_and_merges_contextual_issues(monkeypatch, tmp_path):
    """Пин на v1 — тест проверяет storyline/exemplar_rerank-специфичный
    prompt-набор; v3 (дефолт с 27.09) имеет свою e2e-проверку,
    test_pipeline_selects_v3_layout_fit_when_configured ниже."""
    import deckdna.generation.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "planning_pipeline_version", lambda: "v1")
    gateway = _gateway()
    report = generate(FIXTURE, CONTENT, dict(BRIEF), tmp_path / "llm", gateway=gateway)

    assert report["planner"] == LLM_PLANNER_VERSION
    assert report["contextual_audit"]["ran"] is True
    # VLM-fail на каждом из 12 слайдов → 12 недетерминированных issues
    ctx = [i for i in report["audit_issues"] if not i["deterministic"]]
    assert report["contextual_audit"]["issues"] == len(ctx) == BRIEF["target_slide_count"]
    assert all(i["rule_code"] == "content.nonempty" for i in ctx)
    assert all(i["severity"] == "error" for i in ctx)
    # провайдер реально вызван на обе стадии
    methods = {c["method"] for c in gateway.calls}
    assert {"text_json", "vision_json"} <= methods
    assert {c["prompt"] for c in gateway.calls} == {"storyline", "slide_checks", "exemplar_rerank"}

    # provenance паспорта — только фактически использованные промпты/модели
    prov = report["quality_passport"]["provenance"]
    assert prov["prompt_versions"] == {
        "storyline": "2.0.1",
        "contextual_slide_audit": "1.1.0",
    }
    # mock вне license-manifest → license/size_b остаются None и не сериализуются
    assert prov["model_profiles"] == [{"model_id": "mock", "provider": "mock"}]


@needs_render
def test_content_style_rewrites_bullets_and_shows_in_provenance(monkeypatch, tmp_path):
    """content_style — новая стадия ПОСЛЕ plan_deck_llm, ДО exemplar
    rerank/compose (см. planning/content_style.py). Без fixture'а стадия
    молчит (can_rewrite гейтит MockProvider) — с fixture'ом она должна
    реально переписать content_units и попасть в provenance наравне с
    остальными принятыми LLM-стадиями (storyline/exemplar_rerank).

    Пин на v1 — content_style деliberately пропускается на v3 (дефолт
    с 27.09): Stage 3 layout_fit уже пишет финальный адаптированный
    текст, второй стилистический проход избыточен (см. ADR-016)."""
    import deckdna.generation.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "planning_pipeline_version", lambda: "v1")

    def _style_fixture(payload: dict) -> dict:
        return {
            "items": [
                {"index": it["index"], "text": f"Стилизовано: {it['text'][:30]}"}
                for it in payload["items"]
            ]
        }

    gateway = _gateway(content_style_fixture=_style_fixture)
    report = generate(FIXTURE, CONTENT, dict(BRIEF), tmp_path / "styled", gateway=gateway)

    assert report["planner"] == LLM_PLANNER_VERSION
    assert {c["prompt"] for c in gateway.calls} == {
        "storyline",
        "slide_checks",
        "exemplar_rerank",
        "content_style",
    }
    prov = report["quality_passport"]["provenance"]
    assert prov["prompt_versions"]["content_style"] == "1.0.0"


@needs_render
def test_pipeline_selects_v2_planner_when_configured(monkeypatch, tmp_path):
    """ADR-016: planning.pipeline_version="v2" (config-gated, default
    stays "v1" — see configs/generation.default.yaml) routes generate()
    through structure.py + content_writer.py instead of story_director's
    plan_deck_llm — deck_structure/content_writer prompts fire, not
    storyline, and the deck plan carries LLM-synthesized content_units."""
    import deckdna.generation.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "planning_pipeline_version", lambda: "v2")

    def _structure_fixture(payload: dict) -> dict:
        claims = [n["id"] for n in payload["evidence_graph"]["nodes"] if n["type"] == "claim"]
        return {
            "slides": [
                {
                    "index": h["index"],
                    "purpose": h["purpose"],
                    "slide_brief": f"job for slide {h['index']}",
                    "evidence_ids": claims[:1],
                }
                for h in payload["batch"]["hints"]
            ]
        }

    def _writer_fixture(payload: dict) -> dict:
        return {
            "title_intent": f"ADR016: {payload['slide_brief']}",
            "key_message": "Синтезированный вывод.",
            "bullets": ["Синтезированный тезис из evidence-пула."],
        }

    gateway = MockProvider(
        fixtures={
            "deck_structure": _structure_fixture,
            "content_writer": _writer_fixture,
            "slide_checks": {"verdicts": []},
        }
    )
    out_dir = tmp_path / "v2"
    report = generate(FIXTURE, CONTENT, dict(BRIEF), out_dir, gateway=gateway)

    assert report["planner"] == LLM_PLANNER_VERSION
    called_prompts = {c["prompt"] for c in gateway.calls}
    assert "storyline" not in called_prompts
    assert {"deck_structure", "content_writer"} <= called_prompts

    # доказательство, что синтезированный (не verbatim) текст реально
    # дошёл до финального .pptx, не только до промежуточного DeckPlan.
    from pptx import Presentation

    prs = Presentation(str(out_dir / "deck.pptx"))
    all_text = "\n".join(
        shape.text_frame.text
        for slide in prs.slides
        for shape in slide.shapes
        if shape.has_text_frame
    )
    assert "Синтезированный тезис" in all_text


@needs_render
def test_pipeline_selects_v3_layout_fit_when_configured(monkeypatch, tmp_path):
    """ADR-016 Stage 3: planning.pipeline_version="v3" additionally routes
    exemplar assignment through layout_fit.py (from the REAL written
    text) instead of rerank.py's exemplar_rerank (from a volume estimate)
    -- and skips content_style (layout_fit already adapts the final
    text). layout_fit prompt fires, exemplar_rerank/content_style/
    storyline do not; the adapted text reaches the final .pptx."""
    import deckdna.generation.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "planning_pipeline_version", lambda: "v3")

    def _structure_fixture(payload: dict) -> dict:
        claims = [n["id"] for n in payload["evidence_graph"]["nodes"] if n["type"] == "claim"]
        return {
            "slides": [
                {
                    "index": h["index"],
                    "purpose": h["purpose"],
                    "slide_brief": f"job for slide {h['index']}",
                    "evidence_ids": claims[:1],
                }
                for h in payload["batch"]["hints"]
            ]
        }

    def _writer_fixture(payload: dict) -> dict:
        return {
            "title_intent": f"W: {payload['slide_brief']}",
            "key_message": "Написанный вывод.",
            "bullets": [
                "Написанный тезис номер один про важное дело подробно.",
                "Написанный тезис номер два про важное дело тоже подробно.",
            ],
        }

    def _fit_fixture(payload: dict) -> dict:
        return {
            "candidate_index": 0,
            "title": f"FIT: {payload['title'][:30]}",
            "bullets": [b[:60] for b in payload["bullets"]],
        }

    gateway = MockProvider(
        fixtures={
            "deck_structure": _structure_fixture,
            "content_writer": _writer_fixture,
            "layout_fit": _fit_fixture,
            "slide_checks": {"verdicts": []},
        }
    )
    out_dir = tmp_path / "v3"
    report = generate(FIXTURE, CONTENT, dict(BRIEF), out_dir, gateway=gateway)

    assert report["planner"] == LLM_PLANNER_VERSION
    assert report["exemplar_rerank"] == "llm"
    called_prompts = {c["prompt"] for c in gateway.calls}
    assert called_prompts == {"deck_structure", "content_writer", "layout_fit", "slide_checks"}

    prov = report["quality_passport"]["provenance"]
    assert prov["prompt_versions"]["layout_fit"] == "1.6.0"
    assert "exemplar_rerank" not in prov["prompt_versions"]
    assert "content_style" not in prov["prompt_versions"]

    from pptx import Presentation

    prs = Presentation(str(out_dir / "deck.pptx"))
    all_text = "\n".join(
        shape.text_frame.text
        for slide in prs.slides
        for shape in slide.shapes
        if shape.has_text_frame
    )
    assert "FIT:" in all_text


@needs_render
def test_pipeline_generates_and_embeds_image_when_configured(monkeypatch, tmp_path):
    """ADR-017 e2e: content.image_generation.enabled + strategy=visual
    routes candidate slides through gateway.image_generate() and the
    resulting bytes land as real media in the final .pptx — through the
    same v3 (deck_structure/content_writer/layout_fit) path Phase C
    already covers, proving the two features compose correctly."""
    import deckdna.generation.pipeline as pipeline_module
    from deckdna.contracts.variant_spec import Strategy
    from deckdna.planning import image_brief as image_brief_module

    monkeypatch.setattr(pipeline_module, "planning_pipeline_version", lambda: "v3")
    monkeypatch.setattr(
        image_brief_module,
        "image_generation_config",
        lambda cfg=None: {
            "enabled": True,
            "max_per_deck": 1,
            "style_suffix": "flat illustration, no text",
            "purposes": list(image_brief_module._DEFAULT_PURPOSES),
        },
    )

    def _structure_fixture(payload: dict) -> dict:
        claims = [n["id"] for n in payload["evidence_graph"]["nodes"] if n["type"] == "claim"]
        return {
            "slides": [
                {
                    "index": h["index"],
                    "purpose": h["purpose"],
                    "slide_brief": f"job for slide {h['index']}",
                    "evidence_ids": claims[:1],
                }
                for h in payload["batch"]["hints"]
            ]
        }

    def _writer_fixture(payload: dict) -> dict:
        return {
            "title_intent": f"W: {payload['slide_brief']}",
            "key_message": "Написанный вывод.",
            "bullets": ["Написанный тезис про важное дело подробно и по делу."],
        }

    def _fit_fixture(payload: dict) -> dict:
        return {
            "candidate_index": 0,
            "title": f"FIT: {payload['title'][:30]}",
            "bullets": [b[:60] for b in payload["bullets"]],
        }

    png_payload = (
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    )  # fake but real magic bytes; detect_image_content_type only needs these

    gateway = MockProvider(
        fixtures={
            "deck_structure": _structure_fixture,
            "content_writer": _writer_fixture,
            "layout_fit": _fit_fixture,
            "slide_checks": {"verdicts": []},
            "image_generate": lambda prompt: png_payload,
        }
    )
    out_dir = tmp_path / "img"
    report = generate(
        FIXTURE, CONTENT, dict(BRIEF), out_dir, gateway=gateway, strategy=Strategy.visual
    )

    assert report["planner"] == LLM_PLANNER_VERSION
    image_calls = [c for c in gateway.calls if c["method"] == "image_generate"]
    assert len(image_calls) == 1  # max_per_deck=1

    import zipfile

    with zipfile.ZipFile(out_dir / "deck.pptx") as zf:
        media = [zf.read(n) for n in zf.namelist() if n.startswith("ppt/media/")]
    assert png_payload in media
    # image-role provenance gating itself is covered precisely by
    # test_provenance_inputs_image_role_gated_by_images_generated below
    # (MockProvider collapses every role to the same "mock" model_id here,
    # so this e2e test can't distinguish "image counted" from "not").


@needs_render
def test_invalid_llm_plan_falls_back_to_deterministic(tmp_path):
    report = generate(
        FIXTURE,
        CONTENT,
        dict(BRIEF),
        tmp_path / "fb",
        gateway=_gateway(_broken_storyline_fixture()),
    )

    assert report["planner"] == PLANNER_VERSION  # честный откат
    assert report["slides_out"] == BRIEF["target_slide_count"]
    # storyline НЕ попадает в prompt_versions: его план отвергнут гейтом,
    # а slide_checks-вызов реально отработал и попадает честно.
    prov = report["quality_passport"]["provenance"]
    assert prov["prompt_versions"] == {"contextual_slide_audit": "1.1.0"}
    assert [p["model_id"] for p in prov["model_profiles"]] == ["mock"]


@needs_render
def test_deterministic_path_unchanged(tmp_path):
    report = generate(FIXTURE, CONTENT, dict(BRIEF), tmp_path / "det")

    assert report["planner"] == PLANNER_VERSION
    assert report["contextual_audit"] == {"ran": False, "issues": 0}
    assert all(i["deterministic"] for i in report["audit_issues"])
    # ни один промпт/модель не использовались — честно пусто
    prov = report["quality_passport"]["provenance"]
    assert prov["prompt_versions"] == {}
    assert prov["model_profiles"] == []


class _ManifestStubGateway:
    """ModelProfileSource-совместимый стаб: отчитывается о модели из
    configs/model_licenses.yaml (без сетевых вызовов)."""

    provider_name = "stub-endpoint"

    def used_model_ids(self) -> dict[str, str]:
        return {"text": "qwen2.5-32b-instruct", "vision": "qwen2.5-32b-instruct"}


class _TwoModelGateway:
    """Различимые модели по ролям — проверка фильтра по принятым стадиям."""

    provider_name = "stub-endpoint"

    def used_model_ids(self) -> dict[str, str]:
        return {
            "text": "text-model-a",
            "vision": "vision-model-b",
            "embed": "embed-model-c",
            "image": "image-model-d",
        }


def _llm_marked(plan):
    """План как после принятого plan_deck_llm (provenance-метки LLM-пути)."""
    plan.provenance.planner = LLM_PLANNER_VERSION
    plan.provenance.prompt_version = "1.0.0"
    return plan


def test_provenance_inputs_enriches_from_license_manifest():
    pack = parse_file(CONTENT)
    brief = Brief.model_validate(BRIEF)
    plan = _llm_marked(plan_deck(pack, brief))

    pv, profiles = _provenance_inputs(_ManifestStubGateway(), plan, False)

    assert pv == {"storyline": "1.0.0"}
    # text-стадия принята → одна модель, обогащённая manifest-ом
    assert [p.model_dump() for p in profiles] == [
        {
            "model_id": "qwen2.5-32b-instruct",
            "provider": "stub-endpoint",
            "size_b": 32,
            "license": "apache-2.0",
        }
    ]
    # gateway без интроспекции (None / frozen-протокол) — честный пустой список
    assert _provenance_inputs(None, plan, False) == ({"storyline": "1.0.0"}, [])


def test_provenance_inputs_filters_models_by_accepted_stages():
    """used_model_ids может содержать вызовы, чья стадия отвергла вывод:
    text_json вернул схему-валидный, но семантически отвергнутый план →
    deterministic fallback → text-модель НЕ использована."""
    pack = parse_file(CONTENT)
    brief = Brief.model_validate(BRIEF)
    det = plan_deck(pack, brief)  # как после fallback
    llm = _llm_marked(plan_deck(pack, brief))
    gw = _TwoModelGateway()

    def ids(g, p, ctx):
        return [pr.model_id for pr in _provenance_inputs(g, p, ctx)[1]]

    assert ids(gw, llm, True) == ["text-model-a", "vision-model-b"]
    assert ids(gw, det, True) == ["vision-model-b"]  # semantic-invalid fallback
    assert ids(gw, llm, False) == ["text-model-a"]
    # ни одной принятой стадии; embed у пайплайна стадии нет → отброшен
    assert ids(gw, det, False) == []


def test_provenance_inputs_image_role_gated_by_images_generated():
    """ADR-017: gateway.used_model_ids()'s 'image' role only counts when
    the pipeline actually generated at least one image this run (mirrors
    'vision' being gated by contextual_ran) — a gateway that CAN generate
    images but wasn't asked to (feature disabled, wrong strategy, no
    candidates) must not falsely claim the image model as used."""
    pack = parse_file(CONTENT)
    brief = Brief.model_validate(BRIEF)
    det = plan_deck(pack, brief)
    gw = _TwoModelGateway()

    _pv, no_images = _provenance_inputs(gw, det, False, images_generated=False)
    assert "image-model-d" not in [p.model_id for p in no_images]

    _pv, with_images = _provenance_inputs(gw, det, False, images_generated=True)
    assert [p.model_id for p in with_images] == ["image-model-d"]
