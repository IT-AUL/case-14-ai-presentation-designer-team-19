"""Stage 3 of ADR-016 (Structure -> Writer -> Layout-fit -> Audit):
per-slide exemplar assignment + text adaptation.

Takes the slide's ACTUAL WRITTEN content (Stage 2, ``content_writer.py``
-- real sentences, not a volume estimate) and a candidate exemplar pool,
and decides (a) which exemplar to use and (b) how to fit the written
text into its real shapes (trim/expand as needed). This is deliberately
different from both of today's mechanisms it can replace:

* ``rerank.py`` picks an exemplar from ``purpose``/``title_intent``/
  ``key_message`` BEFORE any real content_units text exists — a volume
  ESTIMATE, not a fact (see ADR-016's problem statement).
* ``text_fit.py`` mechanically shortens text AFTER compose, with no
  understanding of the slide's intent — just "make it shorter".

One call PER SLIDE (not batched like Stage 1) — see ADR-016's
parallelism section; every slide's call is independent once Stage 2 has
written its content, meant to fire together via one ``asyncio.gather``.

The candidate pool is pre-filtered DETERMINISTICALLY by
``_PURPOSE_ARCHETYPE_HINTS`` (ADR-015, reused from ``rerank.py``) down to
a handful of archetype-matching candidates before the model ever sees
it — keeps its job narrow (pick among a few pre-vetted options and adapt
text), not "evaluate 16 candidates from scratch".

Exemplars are exclusive WITHIN one variant (two slides must not clone the
same physical template slide) — concurrent per-slide calls resolve out
of order, so a candidate is claimed atomically under an ``asyncio.Lock``
as each call finishes, not in one end-of-batch pass the way ``rerank.py``
resolves duplicates today.

Drop-in with the existing compose path: returns the same
``list[ExemplarChoice]`` shape ``generate_deck()`` already accepts from
``rerank.py``, plus an updated ``DeckPlan`` (content_units/title_intent
adapted) — ``generate_deck()`` itself needs no changes.
"""

from __future__ import annotations

import asyncio
import logging
import re

from pydantic import BaseModel, Field

from deckdna.contracts.deck_plan import ContentUnit, DeckPlan, Kind, Purpose, SlidePlan
from deckdna.contracts.design_dna import DesignDNA
from deckdna.contracts.variant_spec import Strategy
from deckdna.evaluation.measures import (
    ends_with_unqualified_range,
    extract_numbers,
    starts_with_capital,
)
from deckdna.pptx.cloning.exemplar import (
    ExemplarChoice,
    _presentation_slide_size,
    _ranked_candidate_pool,
    content_horizontal_span,
    has_offslide_content_slots,
    is_team_roster_exemplar,
    layout_archetype,
    run_profile,
    select_exemplar_slides,
    shape_count,
    slide_layout_map,
    slide_parts,
)
from deckdna.pptx.cloning.rerank import (
    _PURPOSE_ARCHETYPE_HINTS,
    _candidate_card,
    _candidate_pool,
)
from deckdna.pptx.composing.text_replace import count_content_slots
from deckdna.pptx.opc.package import OpcPackage
from deckdna.providers.base import ModelGateway
from deckdna.template.meta import meta_slides

logger = logging.getLogger(__name__)

LAYOUT_FIT_PROMPT = "layout_fit"
MAX_CANDIDATES_PER_SLIDE = 4
MAX_POOL_CANDIDATES = 16
MAX_CONCURRENT_FIT_CALLS = 8
_MAX_GROWTH_RATIO = 1.8
# See _is_short_label_exemplar's docstring: below this, an exemplar's
# existing runs are icon/diagram-node-style short labels, not sentences.
_MIN_PROSE_RUN_LEN = 20


class LayoutFitAnswer(BaseModel):
    """Ответ модели на ОДИН слайд — какой кандидат и как под него
    адаптирован текст."""

    candidate_index: int
    title: str = Field(min_length=1)
    bullets: list[str] = Field(min_length=1)


def can_rewrite(gateway: object) -> bool:
    """Тот же паттерн, что у ``content_writer.can_rewrite``/
    ``text_fit.can_rewrite``: offline MockProvider без явного fixture'а
    для этого промпта синтезирует схема-валидную, но бессмысленную
    абракадабру (обычно без чисел) — числовая проверка её не ловит."""
    if getattr(gateway, "provider_name", None) == "mock":
        return LAYOUT_FIT_PROMPT in (getattr(gateway, "fixtures", None) or {})
    return gateway is not None


def _bare(numbers: set[str]) -> set[str]:
    return {re.sub(r"[^\d.]", "", n) for n in numbers}


# Found live (27.09): compressing to fit a
# smaller candidate sometimes produced a fragment instead of a full
# thought -- lowercase-starting, verb-less noun phrase, or a truncated
# predicate ("лента по истории пересылок и реакций." / "Контроль CDN и
# модерация" / "Часть не открывает ссылку, теряется") -- violating the
# prompt's own "never a fragment" rule with nothing catching it. A real
# grammar check is out of scope, but a Russian sentence essentially never
# legitimately starts lowercase -- catches at least this one confirmed,
# reproducible defect. ``starts_with_capital`` (evaluation/measures.py)
# is shared with content_writer.py's own Stage 2 validate() -- found
# live AGAIN 28.09: this exact "лента по истории..." fragment still
# shipped in a fresh run because Stage 3 doesn't touch every slide, and
# Stage 2's own output had no such check at all.


def validate(source_text: str, answer: LayoutFitAnswer, max_chars: int) -> str | None:
    """Причина отказа или ``None``, если ответ модели годится."""
    bullets = [b.strip() for b in answer.bullets]
    if any(not b for b in bullets):
        return "модель вернула пустой bullet"
    if not answer.title.strip():
        return "модель вернула пустой title"
    if not starts_with_capital(answer.title):
        return f"title начинается со строчной буквы (похоже на обрывок): {answer.title[:40]!r}"
    for b in bullets:
        if not starts_with_capital(b):
            return f"bullet начинается со строчной буквы (похоже на обрывок): {b[:40]!r}"
    for label, value in (("title", answer.title), *(("bullet", b) for b in bullets)):
        if ends_with_unqualified_range(value):
            return f"{label} заканчивается диапазоном без единицы или объекта: {value[:60]!r}"
    body_text = answer.title + " " + " ".join(bullets)
    invented = _bare(extract_numbers(body_text)) - _bare(extract_numbers(source_text))
    if invented:
        return "модель добавила числа, которых не было в исходном тексте: " + ", ".join(
            sorted(invented)
        )
    missing = _bare(extract_numbers(source_text)) - _bare(extract_numbers(body_text))
    if missing:
        return "модель потеряла числа из исходного текста: " + ", ".join(sorted(missing))
    if any(b.endswith(("…", "...", ":")) for b in bullets):
        return "модель вернула обрывок вместо завершённого тезиса"
    total = len(answer.title) + sum(len(b) for b in bullets)
    if total > max_chars * _MAX_GROWTH_RATIO:
        return f"результат сильно длиннее исходника: {total} симв."
    return None


def _is_short_label_exemplar(candidate: dict) -> bool:
    """True when this exemplar's EXISTING runs are short labels (icon/
    diagram-node style: a SmartArt-like grouped diagram, a badge grid,
    ...), not full sentences.

    Found live (27.09): a template's diagram exemplar reported
    ``content_slots=37`` (real, non-decorative text runs — no lie there)
    but ``mean_run_len≈11`` — 37 tiny diagram-node labels, not room for 5
    full sentences. `layout_fit` had no signal that these slots were
    small, picked this exemplar for genuine prose content (high nominal
    capacity looked attractive), and dutifully compressed 5 real
    sentences down to 2-3-word labels ("Видео хранится и раздаётся через
    тот же CDN-контур..." -> "Тот же CDN") to fit — the same class of bug
    already fixed once this session for text_fit.py's own floor
    (`_RATIO_FLOOR`/`_UNFIXABLE_SOURCE_LEN`), now found in Stage 3 too.
    Stage 2 (content_writer.py) writes real sentences, not labels, so an
    exemplar built for short labels is a LAST RESORT for that content,
    not a natural first pick — deprioritized here, not excluded (a
    genuinely label-shaped need, if one ever exists, still has a path)."""
    return candidate.get("mean_run_len", 0) < _MIN_PROSE_RUN_LEN


def _narrow_candidates(
    all_candidates: list[dict],
    purpose: Purpose,
    need: frozenset[str],
    content_count: int = 0,
    long_text: bool = False,
    max_candidates: int = MAX_CANDIDATES_PER_SLIDE,
    requested_body_slots: int | None = None,
) -> list[dict]:
    """Детерминированное сужение пула ДО обращения к модели: сначала
    капабилити (chart/table/image — жёсткое требование, кандидат без
    него слайду физически не подходит), затем ЖЁСТКОЕ исключение
    entity-specific эталонов (ADR-018 v1.1.0) для не-team слайдов, затем
    archetype-предпочтение по purpose (ADR-015's
    ``_PURPOSE_ARCHETYPE_HINTS``), затем — реальные ли это слоты под
    прозу или короткие подписи диаграммы/бейджей (см.
    ``_is_short_label_exemplar``). Модель выбирает уже из небольшого,
    преимущественно подходящего набора, а не всех 16.

    Live bug (28.09): a team-roster card exemplar got picked for an
    unrelated 5-stage pipeline slide EVEN with a correct
    content_description offered to the model as a soft "prefer
    something else" hint -- it wasn't a hard enough signal. Candidates
    flagged ``is_entity_specific`` (built for one specific person/team/
    event, per exemplar_describe.v1.yaml) are excluded outright for any
    purpose other than ``Purpose.team`` -- never even reach the model.

    ``requested_body_slots`` (live-deck review, 28.09): реальное число
    bullet-юнитов, уже написанных Stage 2 для этого слайда. Без него
    archetype-фильтр охотно отдавал 15-слотовую card-сетку слайду с
    1–3 буллетами — 11 из 13 контентных слайдов вышли с пустыми
    карточками. When >0 the density band replaces archetype as the
    filter: способный пул сужается до ``content_slots`` в
    ``[requested, max(2*requested, requested+1)]`` (пустой subset
    откатывается на весь capable-пул — template fallback не теряется),
    а внутри него ordering = short-label deprioritized -> минимум
    ``|content_slots - requested|`` -> archetype как tie-break.
    ``None``/0 сохраняет прежнее поведение без изменений."""
    capable = [c for c in all_candidates if need <= frozenset(c["capabilities"])]
    if purpose is not Purpose.team:
        capable = [c for c in capable if not c.get("is_entity_specific")]
    if not capable:
        return []
    safe = [c for c in capable if not c.get("has_offslide_content_slots")]
    if safe:
        capable = safe
    preferred = _PURPOSE_ARCHETYPE_HINTS.get(purpose, ())
    order = {arch.value: i for i, arch in enumerate(preferred)}
    if requested_body_slots:
        dense = [
            c
            for c in capable
            if requested_body_slots
            <= (c.get("content_slots") or 0)
            <= max(2 * requested_body_slots, requested_body_slots + 1)
        ]
        pool = sorted(
            dense or capable,
            key=lambda c: (
                _is_short_label_exemplar(c),
                abs((c.get("content_slots") or 0) - requested_body_slots),
                order.get(c["layout_archetype"], len(order)),
            ),
        )
        return pool[:max_candidates]
    if not preferred:
        pool, archetype_rank = capable, {}
    else:
        matching = [c for c in capable if c["layout_archetype"] in order]
        matching_fits = bool(matching) and (not content_count or any(
            c.get("content_slots", 0) >= content_count
            and c.get("content_slots", 0) - content_count <= max(2, content_count)
            for c in matching
        ))
        pool, archetype_rank = (matching, order) if matching_fits else (capable, {})
    if purpose is not Purpose.team:
        prose_capable = [c for c in pool if not _is_short_label_exemplar(c)]
        if prose_capable:
            pool = prose_capable
    pool = sorted(
        pool,
        key=lambda c: (
            max(0, content_count - c.get("content_slots", 0)),
            max(0, c.get("content_slots", 0) - content_count)
            + 3 * (long_text and _is_short_label_exemplar(c))
            + 2 * ("image" in c["capabilities"] and "image" not in need),
            "image" in c["capabilities"] and "image" not in need,
            archetype_rank.get(c["layout_archetype"], 0),
        ),
    )
    if content_count:
        occupancy_limit = max(content_count + 2, content_count * 2)
        reasonable = [
            c for c in pool
            if content_count <= c.get("content_slots", 0) <= occupancy_limit
        ]
        if reasonable:
            pool = reasonable
    if "image" not in need:
        no_donor_artwork = [c for c in pool if "image" not in c["capabilities"]]
        if no_donor_artwork:
            pool = no_donor_artwork
    if content_count >= 2:
        broad = [
            c for c in pool
            if c.get("horizontal_span", (0.0, 1.0))[0] <= 0.35
            and c.get("horizontal_span", (0.0, 1.0))[1] >= 0.65
        ]
        if broad:
            pool = broad
    return pool[:max_candidates]


async def _fit_one_slide(
    gateway: ModelGateway,
    slide: SlidePlan,
    candidates: list[dict],
    claimed: set[str],
    own_baseline_part: str,
    lock: asyncio.Lock,
    language: str,
    sem: asyncio.Semaphore,
) -> tuple[ExemplarChoice | None, list[ContentUnit] | None, str | None]:
    """(exemplar, новые content_units, новый title) для ОДНОГО слайда, или
    ``(None, None, None)`` — честный сигнал «Stage 3 не сработал для этого
    слайда», вызывающий код откатывает его на детерминированный baseline
    целиком (та же дисциплина, что у Stage 1/2).

    ``claimed`` содержит slide_part КАЖДОГО финального выбора в колоде —
    инициализируется ВСЕМИ baseline-выборами до первого вызова модели
    (не только успешными Stage-3 подборами), иначе слайд, честно
    откатившийся на baseline, мог бы получить тот же физический эталон,
    что другой слайд только что забрал через Stage 3 — найдено вживую при
    ручной проверке (два слайда клонировали один и тот же slide_part).
    ``own_baseline_part`` — то, что уже зарезервировано ЗА ЭТИМ слайдом;
    не считается «занятым» для него самого (иначе Stage 3 никогда не смог
    бы предложить эталон, если он совпадает с исходным)."""
    async with lock:
        available = [
            c
            for c in candidates
            if c["slide_part"] not in claimed or c["slide_part"] == own_baseline_part
        ]
    if not available:
        return None, None, None

    bullet_units = [
        u for u in slide.content_units if u.kind == Kind.bullet and u.role != "equation"
    ]
    if not bullet_units:
        return None, None, None
    source_bullets = [u.text or "" for u in bullet_units]
    source_text = slide.title_intent + " " + " ".join(source_bullets)
    max_chars = len(source_text)

    payload = {
        "title": slide.title_intent,
        "key_message": slide.key_message,
        "bullets": source_bullets,
        "candidates": [
            {
                "index": i,
                "layout_archetype": c["layout_archetype"],
                "content_slots": c["content_slots"],
                "capabilities": c["capabilities"],
                # typical length of this exemplar's OWN existing text —
                # low means its slots are short labels, not sentences
                # (see LAYOUT_FIT_PROMPT's instructions).
                "typical_label_length": round(c.get("mean_run_len", 0)),
                # Structural stats alone can't catch a candidate that's
                # semantically the wrong KIND of content (e.g. a team
                # roster card -- short labels, plausible content_slots,
                # "grid" archetype like many legitimate candidates -- but
                # its own text is literally "Имя Фамилия"/"Должность",
                # unmistakably person-card fields, not generic facts)
                # picked purely because its structural profile looked
                # plausible. `content_description` (ADR-018: a real
                # LLM-written one-liner, computed once at template
                # analyze time, cached in Design DNA) is preferred when
                # available; `text_preview` (the exemplar's own longest
                # existing run -- a cruder guess, no model needed) is the
                # honest fallback when no Design DNA was passed in. A
                # real-text sample either way lets the model catch this
                # the way a person would, without hardcoding any
                # per-template pattern.
                "sample_text": c.get("content_description") or c.get("text_preview", ""),
                # measured on this slide's own frames: the title at its
                # font size in two lines, one slot at a readable 12pt
                "title_max_chars": c.get("title_max_chars"),
                "bullet_max_chars": c.get("bullet_max_chars"),
            }
            for i, c in enumerate(available)
        ],
        "language": language,
    }
    async with sem:
        try:
            answer = await gateway.text_json(LAYOUT_FIT_PROMPT, payload, LayoutFitAnswer)
        except Exception as exc:  # noqa: BLE001 — сбой провайдера = честный fallback слайда
            logger.warning(
                "layout_fit failed for slide %d (%s); deterministic fallback",
                slide.index,
                exc,
            )
            return None, None, None
    if not isinstance(answer, LayoutFitAnswer) or not (
        0 <= answer.candidate_index < len(available)
    ):
        return None, None, None
    picked = available[answer.candidate_index]
    reason = validate(source_text, answer, max_chars)
    if reason is not None:
        logger.info("layout_fit rejected for slide %d: %s", slide.index, reason)
        return None, None, None
    other_text_count = sum(
        bool(u.text)
        and u.kind not in (Kind.title, Kind.image, Kind.table, Kind.chart, Kind.diagram)
        for u in slide.content_units
        if u not in bullet_units
    )
    available_bullet_slots = max(0, picked["content_slots"] - other_text_count)
    required_bullets = len(source_bullets)
    if len(answer.bullets) != required_bullets or len(answer.bullets) > available_bullet_slots:
        logger.info(
            "layout_fit rejected for slide %d: %d bullets for %d planned/%d slots",
            slide.index, len(answer.bullets), len(source_bullets), available_bullet_slots,
        )
        return None, None, None

    async with lock:
        if picked["slide_part"] in claimed and picked["slide_part"] != own_baseline_part:
            # гонка: другой слайд забрал этот эталон между нашим чтением
            # доступности и этим моментом — честный fallback, не спор за ресурс
            return None, None, None
        # Известный остаточный edge case: если пул кандидатов меньше числа
        # слайдов, select_exemplar_slides() уже сегодня переиспользует один
        # slide_part на НЕСКОЛЬКО baseline-слайдов (its own docstring: "wraps
        # around once exhausted") — тогда discard ниже освобождает часть,
        # всё ещё используемую ДРУГИМ таким слайдом. Не новая проблема (тот
        # же дубль уже есть в today's baseline без Stage 3), но Stage 3
        # теоретически может отдать этот слот третьему слайду и создать
        # коллизию. Не решено — редкий составной случай (маленький пул +
        # дубль baseline + чей-то Stage 3 успех), не блокирует эту фазу.
        claimed.discard(own_baseline_part)
        claimed.add(picked["slide_part"])

    bullets = [b.strip() for b in answer.bullets]
    # One output per input bullet preserves provenance and unit order.
    # Equations and structured references are never sent through rewriting.
    rewritten = iter(bullets)
    new_units = [
        u.model_copy(update={"text": next(rewritten)})
        if u.kind == Kind.bullet and u.role != "equation" else u
        for u in slide.content_units
    ]

    choice = ExemplarChoice(
        slide_part=picked["slide_part"],
        layout_part=picked.get("layout_part") or "",
        shape_count=picked["shape_count"],
        layout_slide_count=picked.get("layout_slide_count", 0),
        long_runs=picked["long_runs"],
        content_slots=picked["content_slots"],
    )
    return choice, new_units, answer.title.strip()


# Назначение слайда плана → роль образца в Design DNA (template/dna.py).
# Обложку, раздел и финал нельзя собирать на сетке контентных карточек:
# у шаблона для них есть свои слайды, их роль DNA выводит из признаков.
_PURPOSE_ROLES: dict[Purpose, tuple[str, ...]] = {
    Purpose.title: ("title",),
    Purpose.section_divider: ("section",),
    Purpose.thank_you: ("closing", "contact"),
    Purpose.qa: ("closing",),
    Purpose.cta: ("closing", "contact"),
    Purpose.agenda: ("toc",),
}


_CLOSING_PURPOSES = frozenset({Purpose.thank_you, Purpose.qa, Purpose.cta})


def _images_of(pkg: OpcPackage, slide_part: str) -> set[str]:
    """Картинки слайда и его макета (фон, иллюстрации) — по частям пакета."""
    out: set[str] = set()
    parts = [slide_part]
    for rel, target in pkg.internal_dependencies(slide_part):
        if rel.type_name == "slideLayout":
            parts.append(target)
    for part in parts:
        for rel, target in pkg.internal_dependencies(part):
            if rel.type_name == "image":
                out.add(target)
    return out


def assign_role_exemplars(
    pkg: OpcPackage,
    plan: DeckPlan,
    choices: list[ExemplarChoice],
    design_dna: DesignDNA | None,
) -> list[ExemplarChoice]:
    """Слайдам-«рамкам» (титул, раздел, финал, оглавление) — образец с той
    же ролью из Design DNA, если такой в шаблоне есть и не занят.

    Без DNA или без подходящей роли выбор не меняется. Образцы остаются
    уникальными в пределах колоды; раздел может повторять один образец
    раздела, если в шаблоне он один (так и задумано шаблоном)."""
    if design_dna is None or len(choices) != len(plan.slides):
        return choices
    by_role: dict[str, list[str]] = {}
    structural_meta = meta_slides(pkg, [e.part for e in design_dna.exemplars])
    for ex in sorted(design_dna.exemplars, key=lambda e: e.slide_index):
        if (
            ex.part in pkg.parts
            and not ex.content_is_template_meta
            and ex.part not in structural_meta
        ):
            by_role.setdefault(ex.role, []).append(ex.part)
    if not by_role:
        return choices
    layouts = slide_layout_map(pkg)
    out = list(choices)
    used = {c.slide_part for c in choices}
    title_images: set[str] = set()
    for i, slide in enumerate(plan.slides):
        roles = _PURPOSE_ROLES.get(slide.purpose)
        if not roles:
            continue
        current = choices[i].slide_part
        options = [p for role in roles for p in by_role.get(role, [])]
        closing = slide.purpose in _CLOSING_PURPOSES and bool(title_images)
        if closing and options:
            # финал «зеркалит» обложку: тот же фон/иллюстрация, что у титула
            # (тёмная обложка → тёмный финал, а не светлый с пустой карточкой)
            # (логотип общий у всех — считается число общих картинок)
            options.sort(key=lambda p: -len(_images_of(pkg, p) & title_images))
            if current in options and current == options[0]:
                continue
        elif not options or current in options:
            if slide.purpose == Purpose.title:
                title_images = _images_of(pkg, current)
            continue
        free = [p for p in options if p not in used or p == current]
        # раздел может повторять единственный образец-разделитель шаблона
        pick = free[0] if free else (
            options[0] if slide.purpose is Purpose.section_divider else None
        )
        if pick is None:
            continue
        if slide.purpose == Purpose.title:
            title_images = _images_of(pkg, pick)
        if pick == current:
            continue
        used.discard(current)
        used.add(pick)
        profile = run_profile(pkg, pick)
        out[i] = ExemplarChoice(
            slide_part=pick,
            layout_part=layouts.get(pick) or "",
            shape_count=shape_count(pkg, pick),
            layout_slide_count=choices[i].layout_slide_count,
            long_runs=profile.long_runs,
            content_slots=count_content_slots(pkg.parts[pick], _presentation_slide_size(pkg)),
        )
    return out


async def layout_fit_deck(
    pkg: OpcPackage,
    plan: DeckPlan,
    needs: list[frozenset[str]],
    gateway: ModelGateway,
    *,
    strategy: Strategy = Strategy.balanced,
    max_pool_candidates: int = MAX_POOL_CANDIDATES,
    design_dna: DesignDNA | None = None,
    budgets: dict[str, dict[str, int | None]] | None = None,
) -> tuple[list[ExemplarChoice], DeckPlan]:
    """Stage 3 (ADR-016): (exemplar-выбор, адаптированный DeckPlan).

    ``None``/фолбэк-безопасный на каждом уровне: если модель недоступна,
    пул кандидатов слишком мал, или отдельный слайд не прошёл валидацию
    — этот слайд честно остаётся на детерминированном baseline-выборе
    (``select_exemplar_slides``) с исходным (Stage 1+2) текстом, план
    никогда не портится частично.

    ``design_dna`` (ADR-018, optional): when its exemplars carry a real
    LLM-written ``content_description`` (computed once at template
    analyze time, not here), it's forwarded to each candidate's
    ``sample_text`` in place of the cruder ``text_preview`` heuristic —
    see ``_fit_one_slide``'s payload construction. ``None`` (the CLI/
    skill path, or a template analyzed before this field existed) keeps
    today's ``text_preview`` fallback, unchanged."""
    count = len(plan.slides)
    unit_counts = [
        sum(1 for u in s.content_units if u.text and u.kind not in (
            Kind.title, Kind.image, Kind.table, Kind.chart, Kind.diagram
        )) for s in plan.slides
    ]
    # служебные слайды шаблона (инструкции, палитра, каталог иконок) не
    # эталоны ни для какого слайда колоды
    meta_parts = {
        e.part for e in (design_dna.exemplars if design_dna else [])
        if e.content_is_template_meta
    } | meta_slides(pkg, slide_parts(pkg))
    description_by_part = {
        e.part: (e.content_description, bool(e.content_is_entity_specific))
        for e in (design_dna.exemplars if design_dna else [])
        if e.content_description
    }
    baseline_choices = select_exemplar_slides(
        pkg, count=count, needs=needs, strategy=strategy, unit_counts=unit_counts,
        text_lengths=[
            max(
                (len(u.text or "") for u in s.content_units if u.kind != Kind.title),
                default=0,
            )
            for s in plan.slides
        ],
        purposes=[s.purpose.value for s in plan.slides],
        entity_specific_parts=[
            part for part, (_, specific) in description_by_part.items() if specific
        ] + sorted(meta_parts),
    )
    if len(baseline_choices) != count or not can_rewrite(gateway):
        return assign_role_exemplars(pkg, plan, baseline_choices, design_dna), plan

    ranked, _dominant_layout, _layout_slide_count, profiles = _ranked_candidate_pool(
        pkg, strategy
    )
    pool_parts = _candidate_pool(pkg, ranked, max_candidates=max_pool_candidates)
    # The historical top-N is sorted by richness and can contain only
    # giant grids. Include capacity-nearest exemplars for the *written*
    # slide volumes before the per-slide shortlist is built.
    slide_size = _presentation_slide_size(pkg)
    safe_parts = [p for p in slide_parts(pkg) if not has_offslide_content_slots(pkg, p)]
    nearest_parts = safe_parts or slide_parts(pkg)
    for target in sorted(set(unit_counts)):
        closest = sorted(
            nearest_parts,
            key=lambda part: (
                max(0, target - count_content_slots(pkg.parts[part], slide_size)),
                abs(count_content_slots(pkg.parts[part], slide_size) - target),
            ),
        )[:3]
        for part in closest:
            if part not in pool_parts:
                pool_parts.append(part)
    if len(pool_parts) < 2:
        return assign_role_exemplars(pkg, plan, baseline_choices, design_dna), plan

    layouts = slide_layout_map(pkg)
    all_candidates: list[dict] = []
    pool_parts = [p for p in pool_parts if p not in meta_parts]
    for part in pool_parts:
        card = _candidate_card(pkg, part, layouts, profiles, description_by_part)
        card["is_entity_specific"] = (
            card["is_entity_specific"] or is_team_roster_exemplar(pkg, part)
        )
        card["horizontal_span"] = content_horizontal_span(pkg, part)
        profile = profiles.get(part)
        card["layout_archetype"] = layout_archetype(pkg, part, profile).value
        # геометрический бюджет текста (pptx/composing/text_budget.py)
        card.update((budgets or {}).get(part) or {})
        all_candidates.append(card)

    # ВСЕ baseline-слоты резервируются сразу, не только успешные Stage-3
    # подборы — иначе слайд, честно откатившийся на baseline, мог бы
    # получить тот же slide_part, что другой слайд только что забрал
    # через Stage 3 (см. докстринг _fit_one_slide).
    claimed: set[str] = {c.slide_part for c in baseline_choices}
    lock = asyncio.Lock()
    sem = asyncio.Semaphore(MAX_CONCURRENT_FIT_CALLS)
    needs_padded = list(needs) + [frozenset()] * (count - len(needs))

    async def _task(i: int, slide: SlidePlan):
        narrowed = _narrow_candidates(
            all_candidates, slide.purpose, needs_padded[i], unit_counts[i],
            any(len(u.text or "") > 80 for u in slide.content_units if u.kind != Kind.title),
            requested_body_slots=sum(
                u.kind == Kind.bullet and bool(u.text) for u in slide.content_units
            ),
        )
        fitting = [c for c in narrowed if _fits_rigid(c, _points(slide))]
        if fitting:
            narrowed = fitting
        shortlists[i] = narrowed
        choice, units, title = await _fit_one_slide(
            gateway,
            slide,
            narrowed,
            claimed,
            baseline_choices[i].slide_part,
            lock,
            plan.language or "ru",
            sem,
        )
        return i, choice, units, title

    shortlists: dict[int, list[dict]] = {}
    results = await asyncio.gather(*(_task(i, s) for i, s in enumerate(plan.slides)))

    new_choices = list(baseline_choices)
    new_slides = list(plan.slides)
    changed = False
    for i, choice, units, title in results:
        if choice is None:
            continue
        new_choices[i] = choice
        original = plan.slides[i]
        # _fit_one_slide only ever sees/rewrites Kind.bullet units (its own
        # early gate: no bullet_units -> None, see its docstring) -- any
        # other kind on the slide (image/table/chart/diagram, or a
        # Kind.title unit) is untouched by the model and must survive the
        # rewrite. Replacing content_units wholesale with just the new
        # bullets silently dropped these -- found while wiring ADR-017
        # (a generated Kind.image unit would vanish on any slide Stage 3
        # successfully refit), but it's a real pre-existing bug for
        # user-supplied images/tables/charts/diagrams too.
        new_slides[i] = original.model_copy(
            update={"content_units": units, "title_intent": title}
        )
        changed = True

    new_plan = plan.model_copy(update={"slides": new_slides}) if changed else plan
    new_choices = _avoid_rigid_mismatch(new_plan, new_choices, shortlists, all_candidates)
    new_choices = _diversify(new_plan, new_choices, shortlists, all_candidates)
    return assign_role_exemplars(pkg, new_plan, new_choices, design_dna), new_plan


def _fits_rigid(candidate: dict, bullets: int) -> bool:
    """Эталон с графикой, «запечённой» в картинку макета (``layout_art``,
    text_budget.layout_art_ratio), годится только при точном совпадении
    числа пунктов с числом мест: лишнюю карточку на картинке не удалить,
    недостающую — не дорисовать."""
    if not candidate.get("layout_art") or bullets <= 0:
        return True
    # «подпись + текст» на карточку — частая схема и там, где сетка не
    # распознана как карточки (подложки на картинке, а не фигурами)
    return (candidate.get("content_slots") or 0) in (bullets, bullets * 2)


_POINT_KINDS = frozenset({Kind.bullet, Kind.paragraph, Kind.quote})


def _points(slide: SlidePlan) -> int:
    """Пункты тела слайда: маркированные и абзацы (детерминированный
    запасной путь пишет абзацами)."""
    return sum(
        u.kind in _POINT_KINDS and bool(u.text) and u.role != "equation"
        for u in slide.content_units
    )


def _swap_choice(pick: dict) -> ExemplarChoice:
    return ExemplarChoice(
        slide_part=pick["slide_part"],
        layout_part=pick.get("layout_part") or "",
        shape_count=pick["shape_count"],
        layout_slide_count=pick.get("layout_slide_count", 0),
        long_runs=pick["long_runs"],
        content_slots=pick["content_slots"],
    )


def _avoid_rigid_mismatch(
    plan: DeckPlan,
    choices: list[ExemplarChoice],
    shortlists: dict[int, list[dict]],
    all_candidates: list[dict],
) -> list[ExemplarChoice]:
    """Слайд, откатившийся на baseline-эталон с «запечённой» сеткой другого
    размера, переводится на подходящий эталон из своего шортлиста."""
    by_part = {c["slide_part"]: c for c in all_candidates}
    out = list(choices)
    used = {c.slide_part for c in out}
    for i, slide in enumerate(plan.slides):
        current = by_part.get(out[i].slide_part)
        if current is None or slide.purpose in _PURPOSE_ROLES:
            continue
        bullets = _points(slide)
        if _fits_rigid(current, bullets):
            continue
        alt = next(
            (
                c for c in shortlists.get(i, [])
                if c["slide_part"] not in used
                and not c.get("layout_art")
                and (c.get("content_slots") or 0) >= bullets
            ),
            None,
        )
        if alt is None:
            continue
        used.discard(out[i].slide_part)
        used.add(alt["slide_part"])
        out[i] = _swap_choice(by_part[alt["slide_part"]])
    return out


# Больше двух слайдов подряд с одной раскладкой (обычно сетка карточек)
# читаются как копипаст: третий переводится на другой эталон из своего же
# шортлиста (он уже прошёл фильтры ёмкости и капабилити).
_MAX_SAME_ARCHETYPE_RUN = 2


def _diversify(
    plan: DeckPlan,
    choices: list[ExemplarChoice],
    shortlists: dict[int, list[dict]],
    all_candidates: list[dict],
) -> list[ExemplarChoice]:
    arch = {c["slide_part"]: c["layout_archetype"] for c in all_candidates}
    by_part = {c["slide_part"]: c for c in all_candidates}
    out = list(choices)
    used = {c.slide_part for c in out}
    run_arch, run_len = None, 0
    for i, slide in enumerate(plan.slides):
        a = arch.get(out[i].slide_part)
        if slide.purpose in _PURPOSE_ROLES or a is None:
            run_arch, run_len = None, 0
            continue
        run_len = run_len + 1 if a == run_arch else 1
        run_arch = a
        if run_len <= _MAX_SAME_ARCHETYPE_RUN:
            continue
        bullets = _points(slide)
        alt = next(
            (
                c for c in shortlists.get(i, [])
                if c["layout_archetype"] != a
                and c["slide_part"] not in used
                and (c.get("content_slots") or 0) >= bullets
                and _fits_rigid(c, bullets)
            ),
            None,
        )
        if alt is None:
            continue
        pick = by_part[alt["slide_part"]]
        used.discard(out[i].slide_part)
        used.add(pick["slide_part"])
        out[i] = _swap_choice(pick)
        run_arch, run_len = pick["layout_archetype"], 1
    return out
