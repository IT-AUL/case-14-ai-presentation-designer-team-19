"""История колоды от выводов (ADR-033 §8).

Если в паке есть датасеты, колода строится от выводов, а не от разделов:

1. обложка;
2. «Ключевые выводы» — 3–5 выводов, главный первым (подтверждённые
   пользователем — выше);
3. слайды с данными: заголовок-вывод с настоящими числами, график,
   выбранный кодом (``analysis/recommend.py``), 1–2 сопутствующих факта;
   почему такой график — в заметках докладчика;
4. разделы текста пользователя, если остаётся место;
5. финал «Что предлагаем» — только подтверждённые рекомендации, иначе
   обычный закрывающий слайд.

План детерминированный: модель здесь не участвует, поэтому один и тот же
датасет на любом шаблоне даёт один и тот же тип графика."""

from __future__ import annotations

import hashlib
import logging
import re

from deckdna.analysis.guard import normalize_ru_text
from deckdna.analysis.pipeline import DatasetAnalysis, ViewAnalysis, analyze_pack
from deckdna.analysis.ranking import select_slide_insights
from deckdna.analysis.recommend import CHART_RU, explain_ru
from deckdna.analysis.values import plural
from deckdna.analysis.views import REFERENCE
from deckdna.contracts.content_pack import ContentPack
from deckdna.contracts.content_pack import Kind as PackKind
from deckdna.contracts.dataset import Insight, InsightKind
from deckdna.contracts.deck_plan import (
    Brief,
    ChartSpec,
    ChartType,
    ContentUnit,
    DeckPlan,
    DensityBudget,
    DesiredVisual,
    Kind,
    Level,
    Provenance,
    Purpose,
    SlidePlan,
)
from deckdna.contracts.deck_plan import (
    Section as PlanSection,
)
from deckdna.contracts.variant_spec import Strategy
from deckdna.ingestion.pptx_content import NOTES_RANGE

logger = logging.getLogger(__name__)

PLANNER_VERSION = "data_story/v1"
SCHEMA_VERSION = "1.0"
_MAX_KEY_INSIGHTS = 5
_REF_KINDS = {PackKind.table_ref, PackKind.chart_ref}

# императив цели брифа («показать динамику продаж», «show growth») на
# обложке не нужен — заголовок держит только тему
_VERB_ALT = (
    r"(?:покажите|показать|сделайте|сделать|постройте|построить|расскажите"
    r"|рассказать|проанализируйте|проанализировать|создайте|создать"
    r"|сформируйте|сформировать|подготовьте|подготовить|опишите|описать"
    r"|нарисуйте|нарисовать|сравните|сравнить|show|make|create|build"
    r"|describe|prepare|compare|analy[sz]e)"
)
_ACTION_VERB_RE = re.compile(
    rf"^{_VERB_ALT}\b"
    r"(?:\s+(?:мне|нам)\b)?(?:\s+(?:про|о|об|обо)\b)?\s*",
    re.IGNORECASE,
)
# инструкция после двоеточия/тире («Отчёт по продажам: показать рост») —
# тоже не тема обложки
_TAIL_VERB_RE = re.compile(rf"\s*[:;—–-]\s*{_VERB_ALT}\b.*$", re.IGNORECASE | re.DOTALL)
# «показать динамику» → «динамика»: частые существительные из задач —
# в именительный падеж, остальное остаётся как написал пользователь
_ACC_TO_NOM = {
    "динамику": "динамика",
    "структуру": "структура",
    "долю": "доля",
    "тенденцию": "тенденция",
    "картину": "картина",
    "аналитику": "аналитика",
    "статистику": "статистика",
    "выручку": "выручка",
    "прибыль": "прибыль",
    "отчёт": "отчёт",
    "отчет": "отчет",
    "презентацию": "презентация",
}


def _dedup_facts(items: list) -> list:
    """Убрать выводы, чьи числа — подмножество уже взятого факта
    (повтор одних и тех же цифр разными формулировками)."""
    kept = []
    for ins in items:
        sig = set(ins.numbers)
        if sig and any(sig <= set(prev.numbers) for prev in kept):
            continue
        kept.append(ins)
    return kept


def _dedup_subjects(items: list) -> list:
    """Не больше одного вывода на срез данных: Action Titles без
    двоеточия («Выручка выросла…», «Выручка ускорилась…») всё ещё один
    предмет — резюме не пересказывает его трижды."""
    kept, seen = [], set()
    for ins in items:
        subject = ins.view_id or (ins.headline or ins.fact).split(":", 1)[0].strip().casefold()
        if subject in seen:
            continue
        seen.add(subject)
        kept.append(ins)
    return kept


# «so what» под графиком, когда у главного факта нет ни meaning, ни
# implication (модель не участвовала): детерминированная рекомендация
# по типу факта, числа не выдумываются
_SO_WHAT = {
    "growth": "Стоит сохранить драйверы: удерживать темп текущего роста",
    "decline": "Стоит разобрать причины снижения: проверить факторы до следующего периода",
    "flat": "Стабильность без роста: нужны новые драйверы",
    "peak": "Стоит объяснить пик периода: воспроизвести его условия",
    "acceleration": "Стоит усиливать ведущий канал — рост ускоряется",
    "deceleration": "Стоит проверить насыщение канала — рост замедляется",
    "leader": "Стоит сфокусироваться на лидере: пересмотреть хвост распределения",
    "gap": "Разрыв с остальными велик: нужно опереться на лидера в планах",
    "concentration": "Результат сосредоточен в нескольких позициях: зависимость нужно снижать",
    "spread": "Разброс узкий: нужно планировать от типичного значения",
    "wide_spread": (
        "Диапазон сопоставим с медианой: нужно планировать с запасом "
        "и контролировать нижнюю границу"
    ),
    "missing": "Качество данных: нужно закрыть пропуски до выводов по метрике",
    "outlier": "Стоит проверить выбросы: исключить аномалии до принятия решений",
    "correlation": "Связь показателей заметна: причинность нужно проверить отдельно",
    "no_correlation": "Связи нет: драйвер нужно искать в другом факторе",
    "even": "Доли равны: приоритет нужно задавать стратегией, а не текущей структурой",
    "driver": "Главный фактор — точка управления: нужно сфокусировать контроль на нём",
    "reference": "Детализация справочника: полный список нужно смотреть в приложении",
    "cross_table": "Сверка таблиц: динамика согласована, нужно планировать оба контура синхронно",
}

_SO_WHAT_ALT: dict[str, list[str]] = {
    "growth": [
        _SO_WHAT["growth"],
        "Стоит закрепить динамику: масштабировать растущие сегменты",
        "Стоит обновить базовый план: заложить достигнутый уровень показателя",
    ],
    "decline": [
        _SO_WHAT["decline"],
        "Стоит пересмотреть удержание: остановить просадку ключевой метрики",
        "Стоит выделить очаги спада: проверить сегменты с наибольшим вкладом",
    ],
    "leader": [
        _SO_WHAT["leader"],
        "Стоит приоритизировать первый сегмент: направить ресурсы в точку спроса",
        "Стоит удерживать долю лидера: параллельно развивать вторую позицию",
    ],
    "concentration": [
        _SO_WHAT["concentration"],
        "Стоит диверсифицировать портфель: высокая доля топ-3 создаёт риск концентрации",
    ],
    "wide_spread": [
        _SO_WHAT["wide_spread"],
        "Стоит считать бюджет по квартилям: широкий разброс делает среднее ненадёжным",
    ],
    "spread": [
        _SO_WHAT["spread"],
        "Стоит использовать медиану как норматив: узкий коридор значений предсказуем",
    ],
}

_TREND_VERB_RE = re.compile(
    r"^(?P<subj>[^:—–]{2,42}?)\s+"
    r"(?P<verb>(?:вырос|выросла|выросли|снизился|снизилась|снизились"
    r"|сократился|сократилась|сократились|увеличился|увеличилась|увеличились"
    r"|держится|держатся|остаётся|остаются|достиг|достигла|достигли"
    r"|ускорился|ускорилась|замедлился|замедлилась|движутся|лидирует)\b.*)$",
    re.IGNORECASE,
)


def _so_what_for_slide(fact_type: str | None, used: set[str]) -> str | None:
    """Выбрать детерминированный вывод без повтора штампа между слайдами (F-82)."""
    key = fact_type or ""
    variants = _SO_WHAT_ALT.get(key) or ([_SO_WHAT[key]] if key in _SO_WHAT else [])
    if not variants:
        return None
    for cand in variants:
        if cand not in used:
            used.add(cand)
            return cand
    return None


_LABEL_SPLIT_RE = re.compile(r"^(?P<label>[^:—–]{2,48}?)\s*(?::|\s[—–])\s+(?P<body>\S.*)$")
_WORD_2_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)


def _has_card_label(text: str) -> bool:
    m = _LABEL_SPLIT_RE.match((text or "").strip())
    if not m:
        return False
    label = m.group("label").strip()
    body = m.group("body").strip()
    if len(label.split()) > 6:
        return False
    return bool(_WORD_2_RE.search(label) and _WORD_2_RE.search(body))


def _key_finding_bullet(ins: Insight) -> str:
    """Сформировать тезис для «Ключевые выводы» в формате «Предмет: вывод с числом».

    Единая структура «подпись: текст» защищает тезисы от переписывания в
    ``slide_budget_fit`` и исключает инверсию «240 позиций вошли в топ-6» (F-85)."""
    text = normalize_ru_text((ins.headline or ins.fact).strip())
    if _has_card_label(text):
        return text
    if ins.fact_type in ("correlation", "cross_table") and ": r =" in text:
        fixed = (
            text.replace(" связаны обратно: r =", ": обратная связь, r =")
            .replace(" связаны: r =", ": тесная связь, r =")
            .replace(" движутся синхронно: r =", ": синхронная динамика, r =")
            .replace(" движутся в противофазе: r =", ": динамика в противофазе, r =")
        )
        if _has_card_label(fixed):
            return fixed
    m = _TREND_VERB_RE.match(text)
    if m and len(m.group("subj").split()) <= 6:
        candidate = f"{m.group('subj').strip()}: {m.group('verb').strip()}"
        if _has_card_label(candidate):
            return candidate
    if len(text.split()) <= 6:
        return text
    fact_norm = normalize_ru_text(ins.fact.strip())
    if _has_card_label(fact_norm):
        return fact_norm
    return f"Вывод: {text}"


def _speaker_note(headline: str, top, analysis: DatasetAnalysis) -> str:
    """Заметка докладчика — текст для выступления: факт с числами, его
    смысл и (подтверждённая) рекомендация. Технический разбор выбора
    графика («Данные: … Почему … Альтернативы …») в заметки не идёт —
    он остаётся в карточке ``chart`` и ручке explain_slide."""
    parts = [top.fact if top is not None else headline]
    if top is not None and top.meaning:
        parts.append(top.meaning)
    if top is not None and top.implication and top.confirmed:
        parts.append(f"Рекомендация: {top.implication}")
    note = " ".join(p.rstrip(".") + "." for p in parts if p)
    src = analysis.dataset.title
    return f"{note} Источник: {src}." if src else note


def cover_title_text(text: str | None) -> str | None:
    """Цель брифа — часто инструкция («покажите мне динамику продаж»);
    для заголовка обложки оставляем тему без глагола-императива."""
    if not text:
        return None
    text = re.sub(
        r"^(?:(?:предположение|гипотеза|вывод|тезис|assumption|hypothesis):\s*)+",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    )
    cut = _ACTION_VERB_RE.sub("", text).strip(" .,:;–-")
    if not cut:
        return None
    cut = _TAIL_VERB_RE.sub("", cut).strip(" .,:;–-")
    if not cut:
        return None
    words = cut.split()
    if words and words[0].casefold() in _ACC_TO_NOM:
        words[0] = _ACC_TO_NOM[words[0].casefold()]
        cut = " ".join(words)
    return cut[0].upper() + cut[1:]


def brief_text(brief: Brief) -> str:
    """Намерение из брифа: цель, главная мысль, призыв (ADR-034 §3)."""
    parts = [
        brief.purpose,
        getattr(brief, "key_message", None),
        getattr(brief, "call_to_action", None),
    ]
    return " ".join(p for p in parts if p)


def _chart_lookup(pack: ContentPack) -> dict[str, str]:
    """id представления → id графика пака (id мог получить префикс файла
    при склейке паков, представление записано в ``source_ref.range``)."""
    return {c.source_ref.range: c.id for c in pack.charts if c.source_ref.range}


def _table_lookup(pack: ContentPack) -> dict[str, str]:
    return {t.source_ref.range: t.id for t in pack.tables if t.source_ref.range}


def _data_only(section, dataset_files: set[str]) -> bool:
    blocks = [b for b in section.blocks if b.kind != PackKind.note]
    return not blocks or all(
        b.kind in _REF_KINDS and b.source_ref.artifact_id in dataset_files for b in blocks
    )


def why_note(va: ViewAnalysis, analysis: DatasetAnalysis, chosen: str | None = None) -> str:
    """Заметка «почему такой график»: вход → профиль → задача → выбор →
    альтернативы. Её же читает панель «Почему так» (ADR-034 §7)."""
    rec = va.rec
    chosen = chosen or rec.chosen.chart_type
    chosen_rec = next((r for r in rec.ranked if r.chart_type == chosen), rec.chosen)
    alternatives = [
        f"{CHART_RU[r.chart_type]} ({', '.join(r.reasons[:1])})"
        for r in rec.ranked
        if r.chart_type != chosen
    ][:2]
    rejected = [
        f"{CHART_RU.get(r.chart_type, r.chart_type)}: {r.reasons[0]}"
        for r in rec.rejected
        if r.reasons and r.reasons[0] != "ниже по баллу"
    ][:2]
    parts = [
        f"Данные: {analysis.dataset.title} ({analysis.profile.summary_ru()}).",
        f"Задача: {va.task.task_ru}.",
        f"Почему {CHART_RU[chosen]}: {', '.join(chosen_rec.reasons[:3])}.",
    ]
    if va.view.aggregation:
        parts.append(f"Свёртка: {va.view.aggregation}.")
    if alternatives:
        parts.append("Альтернативы: " + "; ".join(alternatives) + ".")
    if rejected:
        parts.append("Не подошли: " + "; ".join(rejected) + ".")
    return " ".join(parts)


def chart_card(va: ViewAnalysis, chosen: str | None = None) -> dict:
    """Карточка графика для плана (ADR-034 §4): выбранный тип с причинами
    и альтернативы — чипы «другой график»."""
    chosen = chosen or va.rec.chosen.chart_type
    rec = next((r for r in va.rec.ranked if r.chart_type == chosen), None)
    why = va.why if chosen == va.rec.chosen.chart_type else explain_ru(va.view, va.rec, va.task)
    return {
        "type": chosen,
        "reasons": rec.reasons if rec else ["выбрано пользователем"],
        "alternatives": [
            {"type": r.chart_type, "reasons": r.reasons}
            for r in va.rec.ranked
            if r.chart_type != chosen
        ][:2],
        "view_id": va.view.id,
        "why": why,
    }


_PROSE_CELL = 80


def _prose_reference(view) -> bool:
    """Справочная таблица из длинных текстов (интервью, инструкции, образцы
    заполнения) и пустой бланк на слайде не читаются и вылезают за край:
    такие листы остаются в выгрузке, но слайда данных не получают."""
    if view.shape != REFERENCE:
        return False
    cells = [str(c).strip() for r in view.sample_rows for c in r if str(c).strip()]
    if not cells:
        return True
    long_cells = sum(1 for c in cells if len(c) > _PROSE_CELL)
    if long_cells * 5 >= len(cells):
        return True
    # пустой бланк: в каждой строке одна и та же подсказка во всех колонках
    rows = [[str(c).strip() for c in r if str(c).strip()] for r in view.sample_rows]
    rows = [r for r in rows if r]
    return bool(rows) and all(len(r) >= 2 and len(set(r)) == 1 for r in rows)


def _ordered_views(analyses: list[DatasetAnalysis]) -> list[tuple[DatasetAnalysis, ViewAnalysis]]:
    """Главные представления датасетов первыми, затем остальные по силе.
    Справку берём, только если у датасета нет ничего другого."""
    primary, rest = [], []
    for a in analyses:
        views = [v for v in a.views if v.view.shape != REFERENCE or v.view.primary_hint] or a.views
        primary.append((a, views[0]))
        rest.extend((a, v) for v in views[1:])
    rest.sort(key=lambda av: -av[1].strength)
    return primary + rest


def _insight_order(analyses: list[DatasetAnalysis]):
    insights = [i for a in analyses for i in a.insights if i.kind == InsightKind.fact]
    return sorted(insights, key=lambda i: (not i.confirmed, -i.strength))


def _density(n: int) -> DensityBudget:
    level = Level.sparse if n <= 2 else Level.medium if n <= 4 else Level.dense
    return DensityBudget(level=level, max_items=max(n, 1))


def plan_data_deck(
    pack: ContentPack,
    brief: Brief,
    config=None,
    strategy: Strategy = Strategy.balanced,
) -> DeckPlan:
    from deckdna.planning.request_constraints import resolve_count_goal
    from deckdna.planning.story_director import content_groups
    from deckdna.planning.validation import validate_deck_plan

    brief = resolve_count_goal(brief)

    cfg = config
    if cfg is None:
        from deckdna.planning.config import load_generation_config

        cfg = load_generation_config()
    lang = brief.language or pack.language or "ru"
    ru = lang.startswith("ru")
    analyses = analyze_pack(pack, brief_text(brief))
    charts = _chart_lookup(pack)
    tables = _table_lookup(pack)
    meta_ev = [f"{pack.id}:meta"]
    slides: list[SlidePlan] = []

    def add(slide: SlidePlan) -> None:
        slides.append(slide.model_copy(update={"index": len(slides), "id": f"slide-{len(slides)}"}))

    # 1. обложка
    # имя файла («Purchases») — плохой заголовок: цель брифа говорит больше
    from deckdna.planning.story_director import clean_purpose_text, cover_context, cover_topic

    data_title = clean_purpose_text(analyses[0].dataset.title) if analyses else None
    clean_hint = cover_topic(pack.title_hint)
    clean_purpose = clean_purpose_text(brief.purpose)
    clean_key_msg = clean_purpose_text(getattr(brief, "key_message", None))
    first_sec_heading = next(
        (clean_purpose_text(s.heading) for s in pack.sections if clean_purpose_text(s.heading)),
        None,
    )
    clean_purpose = cover_title_text(clean_purpose) or clean_purpose
    title = (
        clean_hint
        or clean_purpose
        or first_sec_heading
        or data_title
        or ("Презентация" if ru else "Presentation")
    )
    eyebrow, subtitle = cover_context(title, brief)
    subtitle = subtitle or clean_key_msg or (clean_purpose if clean_hint else None)
    cover_units = [ContentUnit(role="title", kind=Kind.title, text=title, evidence_ids=meta_ev)]
    if eyebrow:
        cover_units.append(
            ContentUnit(
                role="eyebrow",
                kind=Kind.subtitle,
                text=eyebrow,
                evidence_ids=meta_ev,
            )
        )
    if subtitle and subtitle.strip().casefold() != title.casefold():
        cover_units.append(
            ContentUnit(role="subtitle", kind=Kind.subtitle, text=subtitle, evidence_ids=meta_ev)
        )
    add(
        SlidePlan(
            id="s",
            index=0,
            purpose=Purpose.title,
            title_intent=title,
            key_message=clean_purpose or title,
            evidence_ids=meta_ev,
            content_units=cover_units,
            density_budget=_density(0),
        )
    )

    target = brief.target_slide_count
    ordered = _ordered_views(analyses)
    key_insights = _insight_order(analyses)
    # «Ключевые выводы» — резюме колоды, когда есть хотя бы один слайд
    # данных с парой выводов; исключение — одни карточки KPI (они сами
    # и есть ключевые числа)
    data_views = [va for _, va in ordered if va.rec.chosen.chart_type != "kpi"]
    has_key_slide = len(key_insights) >= 2 and target >= 4 and len(data_views) >= 1
    recommendations = [i for a in analyses for i in a.insights if i.implication and i.confirmed]
    reserved = 1 + int(has_key_slide) + 1  # обложка, выводы, финал
    data_budget = max(1, target - reserved)
    # производные представления (доли того же среза) добавляем только тогда,
    # когда в колоде всего одно основное представление (одиночный категорийный
    # датасет с запросом на несколько слайдов); иначе «тот же разрез в %»
    # дублирует предыдущий слайд (F-87).
    ordered = [av for av in ordered if not _prose_reference(av[1].view)]
    primary_queue = [av for av in ordered if not av[1].view.derived]
    derived_queue = [av for av in ordered if av[1].view.derived] if len(primary_queue) < 2 else []
    chosen_views = (primary_queue + derived_queue)[:data_budget]
    # слайды одного датасета идут подряд, а не вперемешку с соседями;
    # блок KPI — сразу после «Ключевых выводов»: карточки задают масштаб
    # до разбора динамики и структуры
    blocks: dict[str, list] = {}
    for pair in chosen_views:
        blocks.setdefault(pair[0].dataset.id, []).append(pair)
    groups = sorted(blocks.values(), key=lambda b: b[0][1].rec.chosen.chart_type != "kpi")
    chosen_views = [pair for block in groups for pair in block]

    # 2. ключевые выводы: по одному на показанное представление, главный первым
    if has_key_slide:
        # по одному выводу на показанное представление, в порядке колоды:
        # главное (первое представление) первым; подтверждённые
        # пользователем — выше. Производные представления (доли того же
        # среза) сюда не идут — иначе резюме повторяло бы один факт
        # дважды; оставшиеся места добираются сильнейшими выводами.
        derived_ids = {va.view.id for _, va in ordered if va.view.derived}
        picked = []
        for _, va in chosen_views:
            if va.view.derived:
                continue
            best = sorted(va.insights, key=lambda i: (not i.confirmed, -i.strength))
            if best:
                picked.append(best[0])
        confirmed = [
            i
            for i in key_insights
            if i.confirmed and i not in picked and i.view_id not in derived_ids
        ]
        picked = [*confirmed, *picked]
        for ins in key_insights:
            if len(picked) >= _MAX_KEY_INSIGHTS:
                break
            if ins in picked or ins.fact_type == "missing" or ins.view_id in derived_ids:
                continue
            picked.append(ins)
        picked = _dedup_subjects(_dedup_facts(picked))[:_MAX_KEY_INSIGHTS]
        if len(picked) < 2:
            picked = _dedup_subjects(
                _dedup_facts(
                    [
                        i
                        for i in key_insights
                        if i.fact_type != "missing" and i.view_id not in derived_ids
                    ]
                )
            )[:_MAX_KEY_INSIGHTS]
        ev = [f"{pack.id}:{i.id}" for i in picked]
        add(
            SlidePlan(
                id="s",
                index=0,
                purpose=Purpose.overview,
                title_intent="Ключевые выводы" if ru else "Key findings",
                key_message=picked[0].headline or picked[0].fact,
                evidence_ids=ev,
                content_units=[
                    ContentUnit(
                        role="title",
                        kind=Kind.title,
                        text="Ключевые выводы" if ru else "Key findings",
                        evidence_ids=ev[:1],
                    ),
                    *(
                        ContentUnit(
                            role="bullet",
                            kind=Kind.bullet,
                            text=_key_finding_bullet(i) if ru else (i.headline or i.fact),
                            evidence_ids=[f"{pack.id}:{i.id}"],
                        )
                        for i in picked
                    ),
                ],
                desired_visual=DesiredVisual.none,
                density_budget=_density(len(picked)),
                speaker_note=(
                    "Выводы посчитаны по данным; формулировки с числами сверены с файлами."
                ),
                insight_ids=[i.id for i in picked],
            )
        )

    # 3. слайды с данными
    # пропуски данных — видимый буллет на слайде этого датасета (в
    # резюме missing-инсайты не идут, а знать о дыре в данных нужно)
    missing_by_ds: dict[str, list] = {}
    cross_by_ds: dict[str, list] = {}
    for a in analyses:
        missing_by_ds[a.dataset.id] = [i for i in a.insights if i.fact_type == "missing"]
        cross_by_ds[a.dataset.id] = [i for i in a.extra_insights if i.fact_type == "cross_table"]
    used_so_what: set[str] = set()
    for analysis, va in chosen_views:
        view = va.view
        chosen = va.rec.chosen.chart_type
        ds_ev = f"{pack.id}:{analysis.dataset.id}"
        ins_ev = [f"{pack.id}:{i.id}" for i in va.insights]
        ev = [ds_ev, *ins_ev]
        top, supporting = select_slide_insights(
            va.insights,
            view,
            max_supporting=1 if chosen != "kpi" else 0,
        )
        headline = (top.headline or top.fact) if top else va.headline
        if chosen == "kpi":
            # не ярлык «Ключевые показатели», а вывод: сколько чисел
            # собрано и какой показатель их ведёт
            kpis = [i for i in va.insights if i.fact_type == "kpi"]
            if kpis:
                n = len(kpis)
                if n == 1:
                    headline = kpis[0].fact
                elif ru:
                    headline = f"{kpis[0].fact} — и ещё {n - 1} " + plural(
                        n - 1, ("показатель", "показателя", "показателей")
                    )
                else:
                    headline = f"{kpis[0].fact} plus {n - 1} more"
            else:
                headline = "Ключевые показатели" if ru else "Key figures"
        headline = normalize_ru_text(
            clean_purpose_text(headline) or ("Обзор данных" if ru else "Data review")
        )
        body: list[ContentUnit] = [
            ContentUnit(role="title", kind=Kind.title, text=headline, evidence_ids=ev[:1]),
        ]
        chart_id = charts.get(view.id)
        if chosen == "kpi":
            # KPI — карточки: «Показатель: значение» по пункту; вёрстка
            # раскладывает их по карточкам шаблона
            for ins in va.insights:
                body.append(
                    ContentUnit(
                        role="bullet",
                        kind=Kind.bullet,
                        text=ins.fact,
                        evidence_ids=[f"{pack.id}:{ins.id}"],
                    )
                )
            visual = DesiredVisual.none
        elif chosen != "table" and chart_id is not None:
            body.append(
                ContentUnit(
                    role="chart",
                    kind=Kind.chart,
                    chart_ref=chart_id,
                    evidence_ids=[ds_ev],
                    chart_spec=ChartSpec(
                        chart_type=ChartType(chosen),
                        series_count=min(len(view.series) or 1, 5),
                        has_axis_labels=True,
                        has_units=bool(view.unit),
                        # у водопада легенда есть всегда: роли рядов
                        # (рост/снижение/итоги) без неё не читаются
                        has_legend=len(view.series) >= 2 or chosen == "waterfall",
                    ),
                )
            )
            visual = DesiredVisual.chart
        elif tables.get(view.id):
            body.append(
                ContentUnit(
                    role="table", kind=Kind.table, table_ref=tables[view.id], evidence_ids=[ds_ev]
                )
            )
            visual = DesiredVisual.table
        else:
            visual = DesiredVisual.none
        # «Что значит» главного вывода — первым пунктом: заголовок уже несёт
        # сам факт, а смысл — самое ценное для слушателя. Числа в нём сверены
        # с фактом (analysis/interpret.py), поэтому подтверждения не ждёт;
        # подтверждения ждут только следствия-рекомендации (ADR-033 §5).
        if top is not None and chosen != "kpi":
            if top.meaning:
                body.append(
                    ContentUnit(
                        role="bullet",
                        kind=Kind.bullet,
                        text=normalize_ru_text(top.meaning),
                        evidence_ids=[f"{pack.id}:{top.id}"],
                    )
                )
            # рекомендация обязательна: нет подтверждённого следствия —
            # честный «so what» по типу факта без повтора между слайдами
            if not (top.implication and top.confirmed):
                so_what = _so_what_for_slide(top.fact_type, used_so_what)
                if so_what:
                    body.append(
                        ContentUnit(
                            role="bullet",
                            kind=Kind.bullet,
                            text=so_what,
                            evidence_ids=[f"{pack.id}:{top.id}"],
                        )
                    )
        for ins in supporting:
            body.append(
                ContentUnit(
                    role="bullet",
                    kind=Kind.bullet,
                    text=normalize_ru_text(ins.meaning or ins.fact),
                    evidence_ids=[f"{pack.id}:{ins.id}"],
                )
            )
        cross_list = cross_by_ds.get(analysis.dataset.id) or []
        if cross_list and chosen != "kpi":
            cross_ins = cross_list.pop(0)
            ev.append(f"{pack.id}:{cross_ins.id}")
            body.append(
                ContentUnit(
                    role="bullet",
                    kind=Kind.bullet,
                    text=normalize_ru_text(cross_ins.fact),
                    evidence_ids=[f"{pack.id}:{cross_ins.id}"],
                )
            )
        missing = missing_by_ds.get(analysis.dataset.id) or []
        if missing and chosen != "kpi":
            miss = missing.pop(0)
            body.append(
                ContentUnit(
                    role="bullet",
                    kind=Kind.bullet,
                    text=normalize_ru_text(miss.fact),
                    evidence_ids=[f"{pack.id}:{miss.id}"],
                )
            )
        ref_subtitle: str | None = None
        if view.shape == REFERENCE and view.reason:
            n_rows = view.source_rows or analysis.dataset.row_count
            headers = [
                str(h).strip()
                for h in (view.sample_headers or [c.name for c in analysis.dataset.columns])
                if str(h).strip()
            ]
            # в слайд попадают топ-4 колонки (гард читаемости таблиц) —
            # подзаголовок не обещает больше, чем реально отрисовано
            cols = ", ".join(headers[:4])
            pos_word = plural(n_rows, ("позиция", "позиции", "позиций"))
            ref_subtitle = (
                (
                    f"Справочная таблица ({n_rows} {pos_word}): {cols}"
                    if cols
                    else f"Справочная таблица: {n_rows} {pos_word}"
                )
                if ru
                else (
                    f"Reference table ({n_rows} rows): {cols}"
                    if cols
                    else f"Reference table: {n_rows} rows"
                )
            )
            body.append(
                ContentUnit(
                    role="subtitle",
                    kind=Kind.subtitle,
                    text=ref_subtitle,
                    evidence_ids=[ds_ev],
                )
            )
        add(
            SlidePlan(
                id="s",
                index=0,
                purpose=Purpose.data,
                title_intent=headline,
                key_message=(top.fact if top else ref_subtitle or headline),
                evidence_ids=ev,
                content_units=body,
                desired_visual=visual,
                density_budget=_density(len(body) - 1),
                speaker_note=" ".join(
                    [
                        _speaker_note(headline, top, analysis),
                        *(view.notes if view.shape == REFERENCE else []),
                    ]
                ),
                chart=chart_card(va, chosen),
                insight_ids=[i.id for i in va.insights],
            )
        )

    # 4. текст пользователя, если осталось место
    dataset_files = {a.dataset.source_ref.artifact_id for a in analyses}
    room = target - len(slides) - 1
    if room > 0:
        for section, units in content_groups(pack):
            if room <= 0:
                break
            if _data_only(section, dataset_files) or not units:
                continue
            units = [u for u in units if u.kind not in (Kind.table, Kind.chart)]
            if not units:
                continue
            notes = [
                b.text
                for b in section.blocks
                if b.kind == PackKind.note and b.source_ref.range == NOTES_RANGE and b.text
            ]
            ev = sorted({e for u in units for e in u.evidence_ids or []}) or [
                f"{pack.id}:{section.id}"
            ]
            heading = clean_purpose_text(section.heading) or ("Контекст" if ru else "Context")
            add(
                SlidePlan(
                    id="s",
                    index=0,
                    purpose=Purpose.custom,
                    title_intent=heading,
                    key_message=next((u.text for u in units if u.text), heading),
                    evidence_ids=ev,
                    content_units=[
                        ContentUnit(
                            role="title", kind=Kind.title, text=heading, evidence_ids=ev[:1]
                        ),
                        *units,
                    ],
                    density_budget=_density(len(units)),
                    speaker_note=" ".join(notes) or None,
                )
            )
            room -= 1

    # 4а. слайд-приложение «Как выбирались визуализации» (флаг брифа)
    if getattr(brief, "explain_slide", None):
        lines = []
        for slide in slides:
            card = slide.chart or {}
            if slide.purpose == Purpose.data and card.get("type"):
                reason = (card.get("reasons") or [""])[0]
                lines.append(
                    f"{slide.title_intent}: {CHART_RU.get(card['type'], card['type'])}"
                    + (f", {reason}" if reason else "")
                )
        if lines:
            title = "Как выбирались визуализации" if ru else "How the charts were chosen"
            add(
                SlidePlan(
                    id="s",
                    index=0,
                    purpose=Purpose.custom,
                    title_intent=title,
                    key_message=title,
                    evidence_ids=meta_ev,
                    content_units=[
                        ContentUnit(
                            role="title", kind=Kind.title, text=title, evidence_ids=meta_ev
                        ),
                        *(
                            ContentUnit(
                                role="bullet", kind=Kind.bullet, text=t, evidence_ids=meta_ev
                            )
                            for t in lines[:6]
                        ),
                    ],
                    density_budget=_density(len(lines[:6])),
                    speaker_note=" ".join(
                        s.speaker_note or "" for s in slides if s.purpose == Purpose.data
                    )[:1500]
                    or None,
                )
            )

    # 5. финал: 1–3 конкретных шага из подтверждённых следствий либо
    # «Спасибо» + призыв из брифа — без выдуманной рекомендации
    if recommendations:
        final_recs = recommendations[:3]
        ev = [f"{pack.id}:{i.id}" for i in final_recs]
        add(
            SlidePlan(
                id="s",
                index=0,
                purpose=Purpose.cta,
                title_intent="Что предлагаем" if ru else "What we propose",
                key_message=final_recs[0].implication,
                evidence_ids=ev,
                content_units=[
                    ContentUnit(
                        role="title",
                        kind=Kind.title,
                        text="Что предлагаем" if ru else "What we propose",
                        evidence_ids=ev[:1],
                    ),
                    *(
                        ContentUnit(
                            role="bullet",
                            kind=Kind.bullet,
                            text=i.implication,
                            evidence_ids=[f"{pack.id}:{i.id}"],
                        )
                        for i in final_recs
                    ),
                ],
                density_budget=_density(len(final_recs)),
            )
        )
    else:
        thanks = "Спасибо за внимание" if ru else "Thank you"
        cta_text = (getattr(brief, "call_to_action", None) or "").strip()
        add(
            SlidePlan(
                id="s",
                index=0,
                purpose=Purpose.thank_you,
                title_intent=thanks,
                key_message=cta_text or clean_purpose or thanks,
                evidence_ids=meta_ev,
                content_units=[
                    ContentUnit(role="title", kind=Kind.title, text=thanks, evidence_ids=meta_ev),
                    *(
                        [
                            ContentUnit(
                                role="callout",
                                kind=Kind.bullet,
                                text=cta_text,
                                evidence_ids=meta_ev,
                            )
                        ]
                        if cta_text
                        else []
                    ),
                ],
                density_budget=_density(0),
            )
        )

    if len(slides) != target:
        logger.info("data story: %d slides for %d requested", len(slides), target)
        brief = brief.model_copy(update={"target_slide_count": max(len(slides), 3)})
    plan = DeckPlan(
        schema_version=SCHEMA_VERSION,
        id=f"plan-{pack.id.removeprefix('pack-')}-{strategy.value}",
        brief=brief,
        evidence_graph_id=f"pack:{pack.id}",
        objective=clean_purpose or title,
        audience=brief.audience,
        language=lang,
        sections=[
            PlanSection(
                id="data",
                title="Данные" if ru else "Data",
                slide_ids=[s.id for s in slides if s.purpose == Purpose.data],
            )
        ],
        slides=slides,
        provenance=Provenance(
            planner=PLANNER_VERSION,
            prompt_version="v0.1",
            schema_version=SCHEMA_VERSION,
            model_id="deterministic",
            input_hashes=[hashlib.sha256(pack.model_dump_json().encode()).hexdigest()],
        ),
    )
    from deckdna.planning.mandatory import protect_mandatory_sections

    plan = protect_mandatory_sections(plan, pack)
    validate_deck_plan(plan, cfg)
    return plan


__all__ = ["PLANNER_VERSION", "brief_text", "chart_card", "plan_data_deck", "why_note"]
