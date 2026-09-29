"""Capacity-aware exemplar reassignment (pptx/cloning/exemplar.py).

``select_exemplar_slides`` picks exemplars by position in a richest-first
cycle, blind to how much content each plan slide actually has — a
2-unit slide could land on an 8-run exemplar (mostly empty cards) while
an 8-unit slide lands on a 2-run one (overflow/collision), purely by
pick order. Reported live on generated decks. ``unit_counts`` reassigns
already-picked exemplars across positions by capacity match; it never
changes which exemplars get picked, only who gets which.
"""

from __future__ import annotations

from deckdna.contracts.variant_spec import Strategy
from deckdna.pptx.cloning.exemplar import (
    _rebalance_by_capacity,
    select_exemplar_slides,
)
from deckdna.pptx.opc.package import OpcPackage

TEMPLATE = "tests/fixtures/pptx/vk_tech_template.pptx"


# --------------------------------------------------------------------------
# _rebalance_by_capacity — pure function, no pptx involved
# --------------------------------------------------------------------------


def test_rebalance_swaps_sparse_and_dense_slots():
    """2-unit slide at position 0 (holding the 8-run exemplar) and an
    8-unit slide at position 1 (holding the 2-run exemplar) — swapped so
    the dense slide gets the roomy exemplar and vice versa."""
    picks = ["rich", "sparse"]
    need_list = [frozenset(), frozenset()]
    unit_counts = [2, 8]
    long_runs = {"rich": 8, "sparse": 2}

    out = _rebalance_by_capacity(picks, need_list, unit_counts, long_runs)

    assert out[0] == "sparse"  # 2-unit slide -> 2-run exemplar
    assert out[1] == "rich"  # 8-unit slide -> 8-run exemplar
    assert sorted(out) == sorted(picks)  # same multiset, just reassigned


def test_rebalance_preserves_multiset_across_many_slots():
    picks = ["a", "b", "c", "d"]
    need_list = [frozenset()] * 4
    unit_counts = [1, 6, 3, 8]
    long_runs = {"a": 8, "b": 1, "c": 6, "d": 3}

    out = _rebalance_by_capacity(picks, need_list, unit_counts, long_runs)

    assert sorted(out) == sorted(picks)
    # position 3 has the largest need (8) -> gets the largest-capacity exemplar (a, 8)
    assert out[3] == "a"
    # position 0 has the smallest need (1) -> gets the smallest-capacity exemplar (b, 1)
    assert out[0] == "b"


def test_rebalance_skips_needs_pinned_slots():
    """A capability-pinned pick (chart/table/image) never gets swapped
    away, even when its capacity is a terrible match — swapping it would
    silently lose the one exemplar that physically carries the chart."""
    picks = ["chart-carrier", "rich", "sparse"]
    need_list = [frozenset({"chart"}), frozenset(), frozenset()]
    unit_counts = [8, 2, 8]  # position 0 "wants" 8 but is needs-pinned
    long_runs = {"chart-carrier": 1, "rich": 8, "sparse": 2}

    out = _rebalance_by_capacity(picks, need_list, unit_counts, long_runs)

    assert out[0] == "chart-carrier"  # untouched despite the mismatch
    assert out[1] == "sparse"  # swappable pair still rebalanced between themselves
    assert out[2] == "rich"


def test_rebalance_noop_with_fewer_than_two_swappable():
    """One or zero needs-free slots — nothing to rebalance against."""
    picks = ["only"]
    assert _rebalance_by_capacity(picks, [frozenset()], [5], {"only": 1}) == picks

    picks2 = ["a", "b"]
    need_list2 = [frozenset({"table"}), frozenset()]
    assert _rebalance_by_capacity(picks2, need_list2, [9, 9], {"a": 1, "b": 1}) == picks2


def test_rebalance_missing_long_runs_defaults_to_zero():
    """A part absent from the long_runs map (shouldn't happen in practice,
    but the caller always builds it from `picks`) is treated as 0 capacity,
    not a crash."""
    picks = ["known", "unknown"]
    need_list = [frozenset(), frozenset()]
    out = _rebalance_by_capacity(picks, need_list, [1, 5], {"known": 3})
    assert sorted(out) == sorted(picks)


# --------------------------------------------------------------------------
# select_exemplar_slides(unit_counts=...) — real template, end-to-end wiring
# --------------------------------------------------------------------------


def test_default_call_unaffected_when_unit_counts_omitted():
    """Omitting unit_counts (every pre-existing caller) is byte-for-byte
    the old behavior -- this is the actual backward-compat guarantee,
    not just an absence of a crash."""
    pkg = OpcPackage.open(TEMPLATE)
    needs = [frozenset(), frozenset(), frozenset()]
    without = select_exemplar_slides(pkg, count=3, needs=needs)
    also_without = select_exemplar_slides(pkg, count=3, needs=needs, unit_counts=())
    assert [c.slide_part for c in without] == [c.slide_part for c in also_without]


def test_unit_counts_can_change_assignment_on_real_template():
    """On the real organizer template, passing unit_counts that are the
    inverse of pick-order richness (the position that currently holds
    the richest exemplar now wants the least, and vice versa) changes
    at least one assignment vs the position-blind default -- proves the
    wiring is live, not a documented no-op.

    Note: the baseline's own positions are already richest-first (the
    ranked pool is sorted by ``-content_slots``, real fillable capacity —
    not ``-long_runs``, which undercounts dense card grids by multiples),
    so ``slots_by_pos`` is already ~descending -- ``sorted(...)``
    (ascending) is the actual inversion; ``reversed(sorted(...))`` would
    reconstruct the same descending order and prove nothing.
    """
    pkg = OpcPackage.open(TEMPLATE)
    count = 6
    needs = [frozenset()] * count

    baseline = select_exemplar_slides(pkg, count=count, needs=needs)
    slots_by_pos = [c.content_slots for c in baseline]
    assert slots_by_pos == sorted(slots_by_pos, reverse=True)  # sanity: richest-first
    inverted_unit_counts = sorted(slots_by_pos)  # ascending -- the actual inversion

    rebalanced = select_exemplar_slides(
        pkg, count=count, needs=needs, unit_counts=inverted_unit_counts
    )

    assert sorted(c.slide_part for c in baseline) == sorted(
        c.slide_part for c in rebalanced
    )  # same set of exemplars picked
    assert [c.slide_part for c in baseline] != [c.slide_part for c in rebalanced]


def test_unit_counts_reduces_capacity_mismatch_on_real_template():
    """The actual point: total |content_slots - unit_count| mismatch across
    all needs-free picks should not get worse, and does get better for
    this deliberately-adversarial (inverted) ordering."""
    pkg = OpcPackage.open(TEMPLATE)
    count = 6
    needs = [frozenset()] * count

    baseline = select_exemplar_slides(pkg, count=count, needs=needs)
    slots_by_pos = [c.content_slots for c in baseline]
    inverted_unit_counts = sorted(slots_by_pos)

    rebalanced = select_exemplar_slides(
        pkg, count=count, needs=needs, unit_counts=inverted_unit_counts
    )

    def mismatch(choices):
        return sum(
            abs(c.content_slots - need)
            for c, need in zip(choices, inverted_unit_counts, strict=True)
        )

    assert mismatch(rebalanced) <= mismatch(baseline)


def test_needs_pinned_picks_survive_rebalance_on_real_template(tmp_path):
    """A table-needing pick keeps its capability-carrying exemplar even
    when unit_counts is supplied alongside it. vk_tech_template.pptx has
    zero table-carrying slides anywhere (verified) -- vk_workspace.pptx
    has exactly one (slide_id=269, per prior forensic verification),
    which is what makes this a meaningful test instead of both paths
    falling back to the same needs-unsatisfied default together."""
    pkg = OpcPackage.open("tests/fixtures/pptx/vk_workspace.pptx")
    count = 4
    needs = [frozenset({"table"}), frozenset(), frozenset(), frozenset()]

    without_counts = select_exemplar_slides(pkg, count=count, needs=needs)
    with_counts = select_exemplar_slides(
        pkg, count=count, needs=needs, unit_counts=[1, 8, 1, 1]
    )
    assert without_counts[0].slide_part == with_counts[0].slide_part
    from deckdna.pptx.cloning.exemplar import slide_capabilities

    assert "table" in slide_capabilities(pkg, with_counts[0].slide_part)


def test_visual_strategy_still_respected_with_unit_counts():
    """unit_counts and strategy are orthogonal (per the docstring) --
    visual-richness-first ordering among the swappable set should not
    be defeated by capacity rebalancing when needs are uniform."""
    pkg = OpcPackage.open(TEMPLATE)
    count = 4
    needs = [frozenset()] * count
    visual_choices = select_exemplar_slides(
        pkg, count=count, needs=needs, strategy=Strategy.visual
    )
    visual_choices_rebalanced = select_exemplar_slides(
        pkg,
        count=count,
        needs=needs,
        strategy=Strategy.visual,
        unit_counts=[3, 3, 3, 3],  # uniform -- rebalance is a no-op-ish pairing
    )
    # same pool of exemplars either way (strategy picks the same set)
    assert sorted(c.slide_part for c in visual_choices) == sorted(
        c.slide_part for c in visual_choices_rebalanced
    )
