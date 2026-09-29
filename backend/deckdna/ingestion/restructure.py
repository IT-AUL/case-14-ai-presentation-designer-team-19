"""Структура любого материала строит модель, а не угадывает парсер.

Парсер каждого формата (PDF, DOCX, TXT, MD…) надёжно умеет одно: достать
текст, таблицы и картинки в порядке чтения. Угадывать по кеглю, жирности
и капсу, где заголовок, — значит подстраиваться под конкретный файл:
методичка с капсом, экспорт Google Docs с жирными заголовками того же
кегля, стенограмма без заголовков вовсе — у каждого свой способ сломаться.

Поэтому здесь, при доступной модели, структура строится одинаково для
любого входа:

1. текст пакета детерминированно режется на предложения в порядке чтения
   (они и есть факты — дословно, с исходной ``source_ref``); у каждого —
   заголовок, под которым его нашёл парсер, как подсказка, а не правило;
2. модель (промпт ``content_outline``) только группирует НОМЕРА
   предложений в разделы и даёт разделам короткие названия — переписать
   или выдумать факт она не может; длинный документ идёт окнами;
3. таблицы, графики, картинки остаются на своём месте потока — в разделе
   предложения, за которым стояли;
4. без модели или при негодном ответе окна — структура парсера (подряд
   идущие предложения под одним его заголовком), а если заголовков нет —
   нарезка по ~6 предложений.

Блок раздела — пункт списка или абзац из 1–2 предложений: граф фактов
получает узел на мысль, а не на страницу.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass

from pydantic import BaseModel, Field

from deckdna.contracts.content_pack import Block, ContentPack, Kind, Section

logger = logging.getLogger(__name__)

PROMPT_NAME = "content_outline"
MIN_PROSE_CHARS = 800  # короче — и так один-два слайда, структура парсера годится
_SENTENCES_PER_BLOCK = 2
_FALLBACK_SECTION_SIZE = 6
_WINDOW = 250  # предложений на один вызов модели
_MAX_CONCURRENT = 6
_PROSE = (Kind.paragraph, Kind.list, Kind.quote, Kind.note)
_SENT_RE = re.compile(r"(?<=[.!?…])\s+(?=[«\"(]?[A-ZА-ЯЁ0-9])")
_NOISE_RE = re.compile(r"^(слайд|slide|стр\.?|page)\s*\d+\.?$", re.I)

_cache: dict[str, ContentPack] = {}


class OutlineSection(BaseModel):
    heading: str = Field(min_length=1)
    sentences: list[int] = Field(min_length=1)


class Outline(BaseModel):
    title: str | None = None
    sections: list[OutlineSection] = Field(min_length=1)


@dataclass
class _Sent:
    text: str
    block: Block
    hint: str  # заголовок парсера над предложением
    item: bool  # пункт списка в источнике


def _block_text(block: Block) -> str:
    if block.kind == Kind.list:
        return "\n".join(block.items or [])
    return block.text or ""


def _prose_chars(pack: ContentPack) -> int:
    return sum(len(_block_text(b)) for s in pack.sections for b in s.blocks if b.kind in _PROSE)


def needs_restructure(pack: ContentPack) -> bool:
    """Без модели: пересобирать только сплошной текст — меньше двух
    озаглавленных разделов при ≥ ``MIN_PROSE_CHARS`` символов прозы."""
    headed = sum(1 for s in pack.sections if (s.heading or "").strip())
    return headed < 2 and _prose_chars(pack) >= MIN_PROSE_CHARS


def split_sentences(text: str) -> list[str]:
    out: list[str] = []
    for line in re.split(r"\n+", text):
        line = re.sub(r"^\s*[-•–*]\s+", "", line).strip()
        if not line:
            continue
        for sent in _SENT_RE.split(line):
            sent = sent.strip()
            if sent and not _NOISE_RE.match(sent):
                out.append(sent)
    return out


def _stream(pack: ContentPack) -> tuple[list[_Sent], list[tuple[int, Block]]]:
    """(предложения в порядке чтения, [(позиция, не-прозаический блок)]):
    позиция — индекс предложения, после которого блок стоял (-1 — в начале)."""
    sents: list[_Sent] = []
    anchored: list[tuple[int, Block]] = []
    for section in pack.sections:
        hint = (section.heading or "").strip()
        for block in section.blocks:
            if block.kind not in _PROSE:
                anchored.append((len(sents) - 1, block))
                continue
            if block.kind == Kind.list:
                for item in block.items or []:
                    for sent in split_sentences(item):
                        sents.append(_Sent(sent, block, hint, True))
            else:
                for sent in split_sentences(block.text or ""):
                    sents.append(_Sent(sent, block, hint, False))
    return sents, anchored


def _heading_from(sentence: str) -> str:
    words = re.sub(r"[«»\"():;,.!?…]", " ", sentence).split()
    head = " ".join(words[:5])
    return head[:1].upper() + head[1:] if head else "Раздел"


def _fallback_groups(sents: list[_Sent], lo: int, hi: int) -> list[tuple[str, list[int]]]:
    """Структура парсера для окна [lo, hi): подряд идущие предложения под
    одним его заголовком; без заголовков — нарезка по ~6 предложений."""
    if len({s.hint for s in sents[lo:hi] if s.hint}) >= 2:
        groups: list[tuple[str, list[int]]] = []
        for i in range(lo, hi):
            if groups and sents[i].hint == groups[-1][0]:
                groups[-1][1].append(i)
            else:
                groups.append((sents[i].hint, [i]))
        return [(h or _heading_from(sents[idx[0]].text), idx) for h, idx in groups]
    size = _FALLBACK_SECTION_SIZE
    return [
        (_heading_from(sents[i].text), list(range(i, min(hi, i + size))))
        for i in range(lo, hi, size)
    ]


def _valid_groups(
    outline: Outline, lo: int, hi: int
) -> list[tuple[str, list[int]]] | None:
    n = hi - lo
    used: set[int] = set()
    groups: list[tuple[str, list[int]]] = []
    for sec in outline.sections:
        idx = sorted({lo + i for i in sec.sentences if 0 <= i < n and lo + i not in used})
        heading = sec.heading.strip().rstrip(".")
        if not idx or not heading or len(heading) > 80:
            continue
        used.update(idx)
        groups.append((heading, idx))
    # Every source sentence is evidence.  Accepting a mostly-complete
    # outline would silently drop the uncovered tail when ``_build`` creates
    # sections from ``used`` only.  A malformed model answer must therefore
    # fall back to the lossless deterministic grouping below.
    if len(groups) < 2 or len(used) < n:
        return None
    groups.sort(key=lambda g: g[1][0])
    return groups


def _blocks_for(sents: list[_Sent], idx: list[int]) -> list[Block]:
    """Подряд идущие пункты списка одного источника — список; прочее —
    абзацы по 1–2 предложения."""
    blocks: list[Block] = []
    run: list[int] = []

    def flush() -> None:
        if not run:
            return
        first = sents[run[0]]
        if first.item:
            blocks.append(
                Block(kind=Kind.list, items=[sents[i].text for i in run],
                      source_ref=first.block.source_ref)
            )
        else:
            for k in range(0, len(run), _SENTENCES_PER_BLOCK):
                chunk = run[k : k + _SENTENCES_PER_BLOCK]
                blocks.append(
                    Block(kind=Kind.paragraph, text=" ".join(sents[i].text for i in chunk),
                          source_ref=sents[chunk[0]].block.source_ref)
                )
        run.clear()

    for i in idx:
        if run and (
            sents[i].item != sents[run[-1]].item or sents[i].block is not sents[run[-1]].block
        ):
            flush()
        run.append(i)
    flush()
    return blocks


def _build(
    pack: ContentPack,
    sents: list[_Sent],
    anchored: list[tuple[int, Block]],
    groups: list[tuple[str, list[int]]],
    title: str | None,
) -> ContentPack:
    owner = {i: gi for gi, (_h, idx) in enumerate(groups) for i in idx}
    extra: dict[int, list[Block]] = {}
    for pos, block in anchored:
        # в раздел предложения, за которым блок стоял (или ближайшего раньше)
        gi = next((owner[p] for p in range(pos, -1, -1) if p in owner), 0)
        extra.setdefault(gi, []).append(block)
    sections = [
        Section(
            id=f"sec-{gi + 1}", heading=heading, level=1,
            blocks=_blocks_for(sents, idx) + extra.get(gi, []),
        )
        for gi, (heading, idx) in enumerate(groups)
    ]
    return pack.model_copy(
        update={
            "sections": sections,
            "title_hint": pack.title_hint or (title.strip() if title else None),
        }
    )


def _can_use(gateway: object | None) -> bool:
    return gateway is not None and (
        getattr(gateway, "provider_name", None) != "mock"
        or PROMPT_NAME in (getattr(gateway, "fixtures", None) or {})
    )


async def _outline_window(
    gateway: object, sents: list[_Sent], lo: int, hi: int, language: str,
    sem: asyncio.Semaphore,
) -> tuple[list[tuple[str, list[int]]] | None, str | None]:
    n = hi - lo
    payload = {
        "sentences": [
            {"i": i - lo, "text": sents[i].text, "h": sents[i].hint} for i in range(lo, hi)
        ],
        "min_sections": max(2, min(4, n // 6)),
        "max_sections": max(3, min(10, n // 3)),
        "language": language,
    }
    async with sem:
        try:
            outline = await gateway.text_json(PROMPT_NAME, payload, Outline)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — сбой окна = структура парсера для окна
            logger.warning("content outline window %d-%d failed (%s)", lo, hi, exc)
            return None, None
    groups = _valid_groups(outline, lo, hi)
    return groups, (outline.title if groups else None)


async def restructure_pack(pack: ContentPack, gateway: object | None) -> ContentPack:
    """Пакет с разделами, построенными моделью (или запасной структурой)."""
    usable = _can_use(gateway)
    if _prose_chars(pack) < MIN_PROSE_CHARS or (not usable and not needs_restructure(pack)):
        return pack
    sents, anchored = _stream(pack)
    if len(sents) < 4:
        return pack
    text = "\n".join(f"{s.hint}\t{s.text}" for s in sents)
    key = hashlib.sha256(f"{usable}\n{pack.id}\n{text}".encode()).hexdigest()
    if key in _cache:
        return _cache[key]

    windows = [(lo, min(len(sents), lo + _WINDOW)) for lo in range(0, len(sents), _WINDOW)]
    results: list[tuple[list[tuple[str, list[int]]] | None, str | None]]
    if usable:
        sem = asyncio.Semaphore(_MAX_CONCURRENT)
        results = await asyncio.gather(
            *(_outline_window(gateway, sents, lo, hi, pack.language or "ru", sem)
              for lo, hi in windows)
        )
    else:
        results = [(None, None)] * len(windows)
    groups: list[tuple[str, list[int]]] = []
    model_windows = 0
    for (lo, hi), (win_groups, _title) in zip(windows, results, strict=True):
        if win_groups is None:
            win_groups = _fallback_groups(sents, lo, hi)
        else:
            model_windows += 1
        groups.extend(win_groups)
    title = next((t for _g, t in results if t), None)
    result = _build(pack, sents, anchored, groups, title)
    if model_windows == len(windows) or not usable:
        _cache[key] = result  # неудачный ответ модели не кешируем
    logger.info(
        "content outline: %d sentences → %d sections (%d/%d windows by the model)",
        len(sents), len(groups), model_windows, len(windows),
    )
    return result


def restructure_pack_sync(pack: ContentPack, gateway: object | None) -> ContentPack:
    return asyncio.run(restructure_pack(pack, gateway))
