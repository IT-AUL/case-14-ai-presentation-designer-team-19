"""ADR-017 Phase B: deterministic selection of image-generation candidates.

Deliberately NOT an LLM decision. The ADR's whole point is to keep this
optional, stretch feature's (TZ §2.3.1, top-10 only) LLM surface area at
zero: which slides get a generated image, and what to ask the image
model for, are both computed from already-written plan text plus a
purpose allowlist -- no new model call, no new grounding risk.
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel

from deckdna.contracts.deck_plan import (
    ContentUnit,
    DeckPlan,
    DesiredVisual,
    Kind,
    Purpose,
    SlidePlan,
)
from deckdna.contracts.variant_spec import Strategy
from deckdna.planning.config import GenerationConfig, load_generation_config
from deckdna.providers.base import ModelGateway

logger = logging.getLogger(__name__)

MAX_CONCURRENT_IMAGE_CALLS = 4

_DEFAULT_ENABLED = False
_DEFAULT_MAX_PER_DECK = 2
_DEFAULT_STYLE_SUFFIX = (
    "minimalist flat business illustration, no embedded text, "
    "corporate presentation style"
)
_DEFAULT_PURPOSES = (
    Purpose.overview,
    Purpose.solution,
    Purpose.benefits,
    Purpose.team,
    Purpose.data,
)


def image_generation_config(cfg: GenerationConfig | None = None) -> dict:
    """configs/generation.default.yaml → content.image_generation.

    Off by default (ADR-017 stretch feature, needs a live pilot on a real
    image endpoint before any default flip -- same discipline as
    planning.pipeline_version's v1→v3 flip). Unknown/malformed purpose
    strings are dropped, not fatal -- an honest smaller allowlist beats a
    crash over a typo in a config file."""
    cfg = cfg or load_generation_config()
    section = (cfg.raw.get("content") or {}).get("image_generation") or {}
    purposes_raw = section.get("purposes")
    if purposes_raw is None:
        purposes = list(_DEFAULT_PURPOSES)
    else:
        purposes = []
        for p in purposes_raw:
            try:
                purposes.append(Purpose(p))
            except ValueError:
                continue
    strategies = []
    for name in section.get("strategies") or ["visual"]:
        try:
            strategies.append(Strategy(name))
        except ValueError:
            continue
    return {
        "enabled": bool(section.get("enabled", _DEFAULT_ENABLED)),
        "max_per_deck": int(section.get("max_per_deck", _DEFAULT_MAX_PER_DECK)),
        "style_suffix": str(section.get("style_suffix", _DEFAULT_STYLE_SUFFIX)),
        "purposes": purposes,
        "strategies": strategies,
        "size": str(section.get("size", "1536x1024")),
        "max_text_units": int(section.get("max_text_units", 4)),
    }


SCENE_PROMPT = "image_scene"


class SceneItem(BaseModel):
    i: int
    scene: str


class ImageScenes(BaseModel):
    scenes: list[SceneItem]


def _can_describe(gateway: object) -> bool:
    if not hasattr(gateway, "text_json"):
        return False
    fixtures = getattr(gateway, "fixtures", None)
    return fixtures is None or SCENE_PROMPT in fixtures


async def _scene_prompts(
    gateway: ModelGateway, candidates: list[tuple[SlidePlan, str]], style_suffix: str,
) -> list[str]:
    """Один вызов текстовой модели на колоду: слайд → конкретная сцена на
    английском. Маленькие T2I-модели рисуют абстрактные русские заголовки
    пустым листом; при любой ошибке остаётся детерминированный промпт."""
    prompts = [prompt for _, prompt in candidates]
    if not _can_describe(gateway):
        return prompts
    payload = {"slides": [
        {
            "i": k,
            "title": slide.title_intent,
            "points": [u.text for u in slide.content_units if u.text][:4],
        }
        for k, (slide, _) in enumerate(candidates)
    ]}
    try:
        answer = await gateway.text_json(SCENE_PROMPT, payload, ImageScenes)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 — без сцены остаётся исходный промпт
        logger.warning("image_scene failed (%s); deterministic prompts kept", exc)
        return prompts
    for item in answer.scenes:
        scene = item.scene.strip().rstrip(".")
        if 0 <= item.i < len(prompts) and len(scene.split()) >= 4:
            prompts[item.i] = f"{scene}. {style_suffix}"
    return prompts


def build_prompt(slide: SlidePlan, style_suffix: str) -> str:
    """Deterministic prompt from already-written text + a fixed style
    suffix. Deliberately never asks for text rendered on the image --
    text-to-image models reliably mangle it, and the slide already gets
    its own real title/bullets from the template's own text frames."""
    parts = [p for p in (slide.title_intent, slide.key_message) if p]
    subject = ". ".join(parts) or slide.title_intent
    return f"{subject}. {style_suffix}"


def select_candidates(
    plan: DeckPlan, strategy: Strategy, config: dict | None = None
) -> list[tuple[SlidePlan, str]]:
    """(slide, prompt) pairs for up to ``config["max_per_deck"]`` slides.

    Only ``strategy == visual`` -- the visual variant is the one meant to
    look visual, faithful/balanced are unaffected. Only slides whose
    purpose is in the allowlist AND that don't already carry a
    ``desired_visual`` (a slide with a real user image or table already
    has something to show). Deterministic order (plan order), so the
    same plan always yields the same candidates -- no randomness to make
    reproducible test fixtures awkward.
    """
    cfg = config if config is not None else image_generation_config()
    if strategy not in cfg.get("strategies", [Strategy.visual]) or not cfg["enabled"]:
        return []
    purposes = set(cfg["purposes"])
    max_per_deck = cfg["max_per_deck"]
    candidates: list[tuple[SlidePlan, str]] = []
    for slide in plan.slides:
        if len(candidates) >= max_per_deck:
            break
        if slide.purpose not in purposes:
            continue
        if slide.desired_visual not in (None, DesiredVisual.none):
            continue
        # картинке нужно место: плотный слайд с 6 карточками её не вместит
        text_units = sum(
            1 for u in slide.content_units if u.text and u.kind != Kind.title
        )
        if text_units > cfg.get("max_text_units", 4):
            continue
        candidates.append((slide, build_prompt(slide, cfg["style_suffix"])))
    return candidates


def can_generate(gateway: object) -> bool:
    """Есть ли у шлюза настоящая модель картинок. Mock — только с явной
    фикстурой (иначе тесты получали бы непрошеные вызовы); OpenAI-совместимый
    провайдер — только с моделью картинок или делегатом (DECKDNA_IMAGE_*)."""
    if gateway is None or not hasattr(gateway, "image_generate"):
        return False
    if getattr(gateway, "provider_name", None) == "mock":
        return "image_generate" in (getattr(gateway, "fixtures", None) or {})
    if hasattr(gateway, "_image_delegate") or hasattr(gateway, "_model_image"):
        delegate = getattr(gateway, "_image_delegate", None)
        return bool(delegate or getattr(gateway, "_model_image", ""))
    return True


async def _generate_one(
    gateway: ModelGateway,
    slide: SlidePlan,
    prompt: str,
    sem: asyncio.Semaphore,
    size: str = "1024x1024",
) -> bytes | None:
    """One image_generate call. ``None`` (not an exception) is the honest
    "this slide gets no generated image" signal -- a provider failure, a
    gateway with no image capability, or empty bytes back are all treated
    the same way callers treat every other ADR-016/017 model call: the
    slide simply keeps what it already had, never blocks the variant."""
    async with sem:
        for attempt in range(2):
            try:
                data = await gateway.image_generate(prompt, size=size)
            except Exception as exc:  # noqa: BLE001 — provider failure = honest per-slide skip
                logger.warning(
                    "image_generate failed for slide %d (%s); slide left without a generated image",
                    slide.index,
                    exc,
                )
                return None
            if not data or not is_blank_image(data):
                return data or None
            # пустой холст (модель буквально исполнила «минимализм») в
            # слайд не ставим: один повтор с явным требованием сюжета
            logger.warning(
                "blank generated image for slide %d (attempt %d)", slide.index, attempt + 1
            )
            prompt = f"{prompt}, one clear detailed subject in the center, saturated colors"
    return None


def is_blank_image(data: bytes, min_stddev: float = 6.0) -> bool:
    """Почти однотонное изображение (белый/серый лист) — инвариант
    качества, не эвристика под модель: такой кадр не несёт смысла."""
    try:
        from io import BytesIO

        from PIL import Image, ImageStat

        with Image.open(BytesIO(data)) as im:
            gray = im.convert("L").resize((64, 64))
            return ImageStat.Stat(gray).stddev[0] < min_stddev
    except Exception:  # noqa: BLE001 — нераспознанные байты проверит вставка
        return False


async def generate_images(
    plan: DeckPlan,
    gateway: ModelGateway,
    strategy: Strategy,
    config: dict | None = None,
    palette: list[str] | None = None,
) -> tuple[DeckPlan, dict[str, bytes]]:
    """Generate images for the deterministically-selected candidate
    slides (see :func:`select_candidates`) and fold them into a NEW
    DeckPlan.

    Returns ``(plan, {})`` unchanged whenever there is nothing to do:
    feature disabled, wrong strategy, no candidates, or a gateway that
    doesn't implement ``image_generate`` at all (an older/offline
    provider — checked via ``hasattr`` since ``ModelGateway`` is a
    structural Protocol, not something every caller's mock necessarily
    implements in full). All candidate calls run concurrently
    (``asyncio.gather``, same discipline as Stage 2/3 of ADR-016) —
    there are at most ``max_per_deck`` of them, an order of magnitude
    fewer than the per-slide text calls elsewhere in the pipeline.

    A successful slide gets ONE new ``Kind.image`` content unit appended
    (``asset_ref="generated:<slide.id>"``) and ``desired_visual`` set to
    ``image``; the returned dict maps that same ref to the raw bytes,
    ready for ``generate_deck(..., generated_images=...)``. A slide whose
    call failed keeps its plan entry byte-identical to the input.
    """
    cfg = config if config is not None else image_generation_config()
    candidates = select_candidates(plan, strategy, cfg)
    if not candidates or not can_generate(gateway):
        return plan, {}
    # иллюстрации в цветах шаблона, а не в случайной палитре модели
    colors = ", ".join(c if c.startswith("#") else f"#{c}" for c in (palette or [])[:5])
    tone = f", color palette limited to {colors} and white" if colors else ""
    prompts = await _scene_prompts(gateway, candidates, cfg["style_suffix"])
    sem = asyncio.Semaphore(MAX_CONCURRENT_IMAGE_CALLS)
    results = await asyncio.gather(
        *(
            _generate_one(gateway, slide, prompt + tone, sem, cfg.get("size", "1024x1024"))
            for (slide, _), prompt in zip(candidates, prompts, strict=True)
        )
    )
    generated: dict[str, bytes] = {}
    updated_by_id: dict[str, SlidePlan] = {}
    for (slide, _prompt), data in zip(candidates, results, strict=True):
        if data is None:
            continue
        ref = f"generated:{slide.id}"
        generated[ref] = data
        updated_by_id[slide.id] = slide.model_copy(
            update={
                "content_units": [
                    *slide.content_units,
                    ContentUnit(role="body", kind=Kind.image, asset_ref=ref),
                ],
                "desired_visual": DesiredVisual.image,
            }
        )
    if not generated:
        return plan, {}
    new_slides = [updated_by_id.get(s.id, s) for s in plan.slides]
    return plan.model_copy(update={"slides": new_slides}), generated
