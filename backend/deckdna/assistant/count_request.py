"""Conservative count parsing over the complete author's request, not a quote."""
from __future__ import annotations

import re
from dataclasses import dataclass

_COUNT = re.compile(r"\b(\d+)\s*[-]?\s*(?:слайд\w*|slides?)\b", re.I)
_QUALIFIER = re.compile(
    r"\b(не более|не больше|максимум|до|at most|up to|ровно|строго|exactly|"
    r"примерно|около|порядка|about|around)\s*$", re.I,
)
_MAXIMUM = {"не более", "не больше", "максимум", "до", "at most", "up to"}
_EXACT = {"ровно", "строго", "exactly"}
_RANGE = re.compile(r"\d+\s*(?:[-–—]|до|или|or|to|и|and)\s*\d+", re.I)
_NEGATION = re.compile(r"\b(?:не|not|don't|never|без)\b", re.I)
_REFERENCE = re.compile(
    r"\b(?:шаблон\w*|документ\w*|источник\w*|пример(?:е|а|ом|ы|ов)?|template\w*|document\w*|source)\b",
    re.I,
)
_ACTION = re.compile(r"\b(?:сделай\w*|создай\w*|нужн\w*|хочу|собери|make|create|need|want)\b", re.I)
_QUOTED = re.compile(r'«[^»]*»|"[^"\n]*"|“[^”]*”')
_PURE_PREFIX = re.compile(
    r"^(?:(?:сделай(?:те)?|создай(?:те)?|собери(?:те)?|нужно|хочу|пусть будет|"
    r"make|create|please|use)\s+)*(?:презентаци\w*\s+)?(?:на\s+)?$", re.I,
)


@dataclass(frozen=True)
class CountRequest:
    status: str = "absent"
    count: int | None = None
    policy: str | None = None
    quote: str = ""
    pure: bool = False


def read_count_request(text: str) -> CountRequest:
    candidates = []
    for match in _COUNT.finditer(text):
        if any(q.start() <= match.start() < q.end() for q in _QUOTED.finditer(text)):
            continue
        prefix = re.split(r"[,.;!?\n]", text[:match.start()])[-1]
        reference = list(_REFERENCE.finditer(prefix))
        if reference and not _ACTION.search(prefix[reference[-1].end():]):
            continue
        candidates.append((match, prefix))
    if not candidates:
        return CountRequest()
    if len(candidates) != 1:
        return CountRequest(status="ambiguous")
    match, prefix = candidates[0]
    qualifier = _QUALIFIER.search(prefix)
    before = prefix[:qualifier.start()] if qualifier else prefix
    tail = re.split(r"[.;!?\n]", text[match.end():])[0]
    rejected_tail = re.search(
        r"^\s*(?:не\s+(?:нужн\w*|надо|требуется)|not\s+(?:needed|required))\b", tail, re.I,
    )
    alternative = re.search(r"\b(?:или|or|лучше)\s+(?:до\s+|ровно\s+)?\d+\b", tail, re.I)
    if (_RANGE.search(prefix + match.group(1)) or _NEGATION.search(before)
            or rejected_tail or alternative):
        return CountRequest(status="ambiguous")
    if len(match.group(1)) > 2:
        return CountRequest(status="ambiguous")
    count = int(match.group(1))
    if not 3 <= count <= 40:
        return CountRequest(status="ambiguous")
    if qualifier is None:
        # Plain counts keep the legacy model-assisted extraction path.
        return CountRequest(status="plain", count=count, quote=match.group(0))
    word = qualifier.group(1).casefold()
    policy = "maximum" if word in _MAXIMUM else "exact" if word in _EXACT else "preferred"
    start = match.start() - len(prefix) + qualifier.start()
    quote = text[start:match.end()]
    pure = bool(_PURE_PREFIX.fullmatch(text[:start].strip() + " ")
                and not text[match.end():].strip(" .!?"))
    # Empty prefix is also a complete count command ("ровно 6 слайдов").
    pure = pure or (not text[:start].strip() and not text[match.end():].strip(" .!?"))
    return CountRequest("accepted", count, policy, quote, pure)
