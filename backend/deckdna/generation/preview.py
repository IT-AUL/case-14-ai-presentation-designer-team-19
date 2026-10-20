"""Пробные слайды стиля (ADR-034 §5).

Вместо трёх полных колод — 2–3 ключевых слайда в каждой стратегии:
обложка, первый слайд с данными или графиком, обычный текстовый. Образцы
выбираются тем же кодом, что и в полной сборке (``plan_exemplar_choices``
по всему плану), затем верстаются и рендерятся только эти слайды. Выбор
образцов возвращается: полная сборка по ``preview_id`` берёт те же
(``exemplar_overrides``), поэтому пробная вёрстка совпадает с полной.

Тексты карточек стиля — ``configs/variants.default.yaml`` (``title_ru``,
``difference_ru``); рекомендация — по содержанию плана."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from deckdna.contracts.content_pack import ContentPack
from deckdna.contracts.deck_plan import DeckPlan, Kind, Purpose, SlidePlan
from deckdna.contracts.design_dna import DesignDNA
from deckdna.contracts.variant_spec import Strategy

VARIANTS_CONFIG = Path(__file__).resolve().parents[3] / "configs" / "variants.default.yaml"
STRATEGIES = ("faithful", "balanced", "visual")
_TEXT_PURPOSES = {
    Purpose.custom, Purpose.overview, Purpose.problem, Purpose.solution, Purpose.benefits,
    Purpose.process, Purpose.timeline, Purpose.team, Purpose.comparison,
}


@lru_cache(maxsize=1)
def style_texts() -> dict[str, dict[str, str]]:
    raw = yaml.safe_load(VARIANTS_CONFIG.read_text(encoding="utf-8"))
    return {
        v["strategy"]: {"title": v.get("title_ru") or v["strategy"],
                        "difference": v.get("difference_ru") or v.get("description", "")}
        for v in raw.get("variants", [])
    }


def key_slides(plan: DeckPlan, limit: int = 3) -> list[str]:
    """Обложка, первый слайд с данными/графиком, обычный текстовый."""
    slides = plan.slides
    picked: list[str] = []
    if slides:
        picked.append(slides[0].id)
    data = next(
        (s for s in slides if any(u.kind in (Kind.chart, Kind.table) for u in s.content_units)),
        None,
    )
    if data is not None and data.id not in picked:
        picked.append(data.id)
    text = next(
        (
            s for s in slides
            if s.id not in picked and s.purpose in _TEXT_PURPOSES
            and sum(1 for u in s.content_units if u.kind in (Kind.bullet, Kind.paragraph)) >= 2
        ),
        None,
    ) or next(
        (s for s in slides[1:] if s.id not in picked and s.purpose != Purpose.thank_you), None
    )
    if text is not None:
        picked.append(text.id)
    return picked[:limit]


def recommend_style(plan: DeckPlan) -> tuple[str, str]:
    """Стиль по содержанию плана: много данных → визуальный, много текста →
    строгий, иначе сбалансированный. (стратегия, причина для строки
    помощника)."""
    content = [s for s in plan.slides if s.purpose not in (Purpose.title, Purpose.thank_you)]
    if not content:
        return "balanced", "Материала немного — подойдёт сбалансированный стиль."
    visual = sum(
        1 for s in content
        if any(u.kind in (Kind.chart, Kind.table, Kind.diagram, Kind.image)
               for u in s.content_units)
    )
    chars = [
        sum(len(u.text or "") for u in s.content_units if u.kind != Kind.title) for s in content
    ]
    avg = sum(chars) / len(chars)
    if visual / len(content) >= 0.4:
        return "visual", (
            f"Советую визуальный: на {visual} из {len(content)} слайдов графики и схемы."
        )
    if avg >= 260:
        return "faithful", (
            "Советую строгий: слайды плотные по тексту, ему нужны привычные раскладки."
        )
    return "balanced", "Советую сбалансированный: текста и визуала поровну."


@dataclass
class StylePreview:
    strategy: str
    slides: list[dict[str, Any]] = field(default_factory=list)  # {slide_id, png, exemplar}
    seconds: float = 0.0
    error: str | None = None

    @property
    def exemplars(self) -> dict[str, str]:
        return {s["slide_id"]: s["exemplar"] for s in self.slides if s.get("exemplar")}


def _sanitize_cover_slide(slide: SlidePlan) -> SlidePlan:
    if slide.purpose != Purpose.title:
        return slide
    title_norm = (slide.title_intent or "").strip().casefold()
    kept = []
    has_subtitle = False
    for u in slide.content_units:
        if u.kind == Kind.title:
            if not title_norm and u.text:
                title_norm = u.text.strip().casefold()
            kept.append(u)
            continue
        text_norm = (u.text or "").strip().casefold()
        if not text_norm or text_norm == title_norm or has_subtitle:
            continue
        kept.append(u.model_copy(update={"kind": Kind.subtitle, "role": "subtitle"}))
        has_subtitle = True
    return slide.model_copy(update={"content_units": kept})


def _one_style(
    template_path: Path, pack: ContentPack, plan: DeckPlan, strategy: str,
    slide_ids: list[str], out_dir: Path, design_dna: DesignDNA | None,
) -> StylePreview:
    from deckdna.pptx.composing.minimal import generate_deck, plan_exemplar_choices
    from deckdna.pptx.exporting.previews import pdf_page_pngs
    from deckdna.pptx.exporting.render import render_pdf
    from deckdna.template.synthesize import prepared_template_path

    started = time.monotonic()
    result = StylePreview(strategy=strategy)
    strat = Strategy(strategy)
    out_dir.mkdir(parents=True, exist_ok=True)
    effective_template, _ = prepared_template_path(Path(template_path), out_dir)
    sanitized_slides = [_sanitize_cover_slide(s) for s in plan.slides]
    clean_plan = plan.model_copy(update={"slides": sanitized_slides})
    choices = plan_exemplar_choices(
        effective_template, clean_plan, tables=pack.tables, charts=pack.charts,
        diagrams=pack.diagrams, assets=pack.assets, strategy=strat, design_dna=design_dna,
    )
    index = [i for i, s in enumerate(clean_plan.slides) if s.id in set(slide_ids)]
    subset = [clean_plan.slides[i].model_copy(update={"index": k}) for k, i in enumerate(index)]
    sub_plan = clean_plan.model_copy(update={
        "slides": subset,
        "brief": clean_plan.brief.model_copy(update={"target_slide_count": max(3, len(subset))}),
    })
    pptx = out_dir / "preview.pptx"
    generate_deck(
        effective_template, sub_plan, pptx, tables=pack.tables, charts=pack.charts,
        diagrams=pack.diagrams, assets=pack.assets, strategy=strat,
        exemplar_choices=[choices[i] for i in index], design_dna=design_dna,
    )
    pngs = pdf_page_pngs(render_pdf(pptx, out_dir / "preview.pdf"))
    for k, i in enumerate(index):
        result.slides.append({
            "slide_id": plan.slides[i].id,
            "index": plan.slides[i].index,
            "png": pngs[k] if k < len(pngs) else None,
            "exemplar": choices[i].slide_part,
        })
    result.seconds = round(time.monotonic() - started, 2)
    return result


def render_style_previews(
    template_path: str | Path,
    pack: ContentPack,
    plan: DeckPlan,
    out_dir: str | Path,
    *,
    slide_ids: list[str] | None = None,
    strategies: tuple[str, ...] = STRATEGIES,
    design_dna: DesignDNA | None = None,
    on_ready: Any = None,
) -> dict[str, Any]:
    """Три стиля параллельно. ``on_ready(strategy, slide)`` — по мере
    готовности стиля (событие ``preview.ready``)."""
    template_path = Path(template_path)
    out_dir = Path(out_dir)
    ids = slide_ids or key_slides(plan)
    started = time.monotonic()

    def run(strategy: str) -> StylePreview:
        try:
            preview = _one_style(template_path, pack, plan, strategy, ids,
                                 out_dir / strategy, design_dna)
        except Exception as exc:  # noqa: BLE001 — один стиль не валит остальные
            return StylePreview(strategy=strategy, error=f"{type(exc).__name__}: {exc}")
        if on_ready is not None:
            for slide in preview.slides:
                on_ready(strategy, slide)
        return preview

    with ThreadPoolExecutor(max_workers=len(strategies)) as pool:
        previews = list(pool.map(run, strategies))
    recommended, why = recommend_style(plan)
    texts = style_texts()
    return {
        "slide_ids": ids,
        "recommended": recommended,
        "assistant_note": why,
        "seconds": round(time.monotonic() - started, 2),
        "styles": [
            {
                "strategy": p.strategy,
                "title": texts.get(p.strategy, {}).get("title", p.strategy),
                "difference": texts.get(p.strategy, {}).get("difference", ""),
                "recommended": p.strategy == recommended,
                "previews": p.slides,
                "exemplars": p.exemplars,
                "seconds": p.seconds,
                "error": p.error,
            }
            for p in previews
        ],
    }


__all__ = ["key_slides", "recommend_style", "render_style_previews", "style_texts"]
