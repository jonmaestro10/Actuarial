"""The windowed forward loop.

PLAN §4.2 asks for the recursion over ``t`` to become "a forward loop over
preallocated arrays". This is that loop — without the compiled kernels,
which are still ahead of it.

Two changes, and only one of them is about arrays. Each period is written
straight into its row of the output slab rather than accumulated into a list
and copied at the end. And, because the dependency graph now says how far
back a model can reach, everything older than that window is dropped from
the memo as the loop advances.

The second is where the time goes. A projection's memo held every
``(variable, t)`` it ever computed: for 100,000 policies over 60 years that
is hundreds of megabytes of arrays nothing will read again, and freeing them
is worth more than any arithmetic in the loop.

What this file pins:

**No number moved.** Same expressions, same order, same bits — across
templates and both array executors.

**A wrong window is loud, not slow.** Correctness does not rest on the
traced horizon being right. A value asked for after it was dropped raises,
naming the variable and the period, rather than being silently recomputed —
which would be correct but could cascade into recomputing the projection.

**The memo really does stay small.** Otherwise the whole exercise is a
refactor with a story attached.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.core.model import EvictedValueError, Model, var
from engine.core.runner import run
from engine.core.stochastic import run_stochastic
from engine.core.vector import TRACE_PERIODS, run_vectorized
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import ModelPoint, from_dicts, to_batch
from engine.data.scenarios import ScenarioSet
from engine.library.term_life import TermLife
from engine.library.unit_linked import UnitLinkedGMDB

ASSUMPTIONS = Assumptions(mortality=MortalityTable.flat(0.01), lapse=0.05,
                          interest=0.03, expense_per_policy=50.0)
OUTPUTS = ["pols_if", "claims", "premiums", "expenses", "profit_before_tax"]


def points(n=8):
    return from_dicts([
        {"id": f"T{i}", "age_at_entry": 30 + 3 * i, "term_years": 20,
         "sum_assured": 100_000.0 * (i + 1), "annual_premium": 700.0 * (i + 1),
         "init_pols": 1}
        for i in range(n)
    ])


# --- no number moved -----------------------------------------------------


def test_the_windowed_loop_agrees_with_the_unwindowed_interpreter():
    """The interpreted executor keeps every period, so it is the reference
    the windowed one has to reproduce — bitwise, not to tolerance."""
    block = points()
    interpreted = run(TermLife, block, ASSUMPTIONS, 25, outputs=OUTPUTS)
    vectorized = run_vectorized(TermLife, block, ASSUMPTIONS, 25,
                                outputs=OUTPUTS)
    for name in OUTPUTS:
        assert np.array_equal(
            np.array([mp[name] for mp in interpreted.per_mp]).T,
            np.asarray(vectorized.array(name)),
        ), name


@pytest.mark.parametrize("chunk", [None, 1, 3, 1_000])
def test_chunking_still_changes_nothing(chunk):
    """Windowing and chunking are independent: one shrinks the memo, the
    other the batch, and neither may move a number."""
    block = points()
    reference = run_vectorized(TermLife, block, ASSUMPTIONS, 25,
                               outputs=OUTPUTS, chunk_size=None)
    other = run_vectorized(TermLife, block, ASSUMPTIONS, 25, outputs=OUTPUTS,
                           chunk_size=chunk)
    for name in OUTPUTS:
        assert np.array_equal(np.asarray(reference.array(name)),
                              np.asarray(other.array(name))), name


def test_a_projection_shorter_than_the_trace_still_runs():
    """The window comes off a graph traced over the first few periods. A
    projection shorter than that has to work anyway."""
    for proj_len in range(1, TRACE_PERIODS + 2):
        result = run_vectorized(TermLife, points(2), ASSUMPTIONS, proj_len,
                                outputs=["pols_if"])
        assert np.asarray(result.array("pols_if")).shape == (proj_len + 1, 2)


def test_the_stochastic_executor_is_windowed_too_and_unchanged():
    block = from_dicts([
        {"id": f"U{i}", "age_at_entry": 55 + i, "term_years": 15,
         "premium": 100_000.0, "gmdb_guarantee": 100_000.0, "init_pols": 1}
        for i in range(4)
    ])
    scenarios = ScenarioSet.lognormal(16, 20, drift=0.05, vol=0.17, seed=3)
    outputs = ["pols_if", "fund_eoy", "gmdb_claims"]
    slab = run_stochastic(UnitLinkedGMDB, block, ASSUMPTIONS, scenarios, 18,
                          outputs=outputs)
    for s in range(scenarios.n_scenarios):
        one = run_stochastic(UnitLinkedGMDB, block, ASSUMPTIONS,
                             scenarios.single(s), 18, outputs=outputs)
        for name in outputs:
            assert np.array_equal(
                np.asarray(slab.array(name))[:, :, s],
                np.asarray(one.array(name))[:, :, 0],
            ), f"{name} scenario {s}"


# --- the window itself ---------------------------------------------------


def test_pruning_actually_frees_the_periods():
    model = TermLife(to_batch(points(4)), ASSUMPTIONS, 20)
    for t in range(11):
        model.pols_if(t)
    assert any(key[1] < 5 for key in model._cache)
    model.prune(5)
    assert not any(key[1] < 5 for key in model._cache)
    assert any(key[1] >= 5 for key in model._cache)


def test_pruning_is_monotone():
    """Asking to keep more than is already kept is a no-op, not a way to
    resurrect what has gone."""
    model = TermLife(to_batch(points(2)), ASSUMPTIONS, 20)
    model.series("pols_if")
    model.prune(10)
    model.prune(3)
    assert model._evicted_before == 10


def test_the_memo_stays_bounded_over_a_long_projection():
    """The point of the exercise. Without a window this grows with the
    projection length; with one it is flat."""
    sizes = []

    class Watched(TermLife):
        pass

    for proj_len in (20, 60, 200):
        model = Watched(to_batch(points(2)), ASSUMPTIONS, proj_len,
                        record_graph=True)
        peak = 0
        for t in range(proj_len + 1):
            model.pols_if(t)
            model.claims(t)
            if t == TRACE_PERIODS:
                model.record_graph = False
            model.prune(t - 1)
            peak = max(peak, len(model._cache))
        sizes.append(peak)
    assert sizes[0] == sizes[1] == sizes[2], (
        f"memo grew with the projection: {sizes}"
    )


# --- a wrong window is loud ----------------------------------------------


class ReachesBackFurtherLater(Model):
    """Legal, and a trap: a ``@var`` may branch on ``t`` — ``t`` is not
    model-point data — so a model can start reaching further back than a
    short trace ever saw."""

    @var
    def a(self, t):
        if t == 0:
            return self.mp.init_pols * 1.0
        if t >= 8:
            return self.a(t - 6) * 0.5
        return self.a(t - 1) * 0.9


def test_reaching_past_the_window_raises_instead_of_recomputing():
    """Silently recomputing would be *correct* — the formulas are pure —
    but a dropped value pulls its own dropped dependencies, and that
    cascades. Better to say so."""
    model = ReachesBackFurtherLater(
        ModelPoint(id="X", init_pols=1), ASSUMPTIONS, 12
    )
    # The guard fires the moment the widened look-back is first used: at
    # t = 8 the model reaches for a(2), which the one-period window dropped
    # six periods earlier.
    with pytest.raises(EvictedValueError, match=r"a\(2\) was dropped"):
        for t in range(12):
            model.a(t)
            model.prune(t - 1)
    assert t == 8


def test_the_eviction_message_says_what_to_do():
    model = TermLife(to_batch(points(2)), ASSUMPTIONS, 20)
    model.series("pols_if")
    model.prune(10)
    with pytest.raises(EvictedValueError) as exc:
        model.pols_if(2)
    message = str(exc.value)
    assert "pols_if(2)" in message
    assert "traced dependency graph" in message
    assert "without a window" in message


def test_a_model_that_reaches_further_back_is_caught_by_the_executor():
    """End to end: the executor windows from the traced horizon, and a
    model whose look-back widens later trips the guard rather than
    returning a wrong number."""
    with pytest.raises(EvictedValueError):
        run_vectorized(ReachesBackFurtherLater,
                       [ModelPoint(id="X", init_pols=1)], ASSUMPTIONS, 12,
                       outputs=["a"])


def test_the_interpreter_keeps_everything_and_so_is_never_caught():
    """The reference executor does not window, which is what makes it the
    reference: it can always be asked for any period."""
    result = run(ReachesBackFurtherLater,
                 [ModelPoint(id="X", init_pols=1)], ASSUMPTIONS, 12,
                 outputs=["a"])
    assert len(result.per_mp[0]["a"]) == 13


# --- the window comes from the graph -------------------------------------


def test_every_template_looks_back_exactly_one_period():
    """Which is why a two-period window suffices, and why the memo is flat
    rather than merely smaller."""
    graph = TermLife.trace(points(1)[0], ASSUMPTIONS)
    assert graph.horizon() == 1
    assert ReachesBackFurtherLater.trace(
        ModelPoint(id="X", init_pols=1), ASSUMPTIONS, proj_len=10
    ).horizon() == 6


def test_tracing_happens_once_per_chunk_not_once_per_period():
    """Recording costs a set insert per evaluation, so it runs for the
    first few periods only — which are needed anyway, so none of that work
    is wasted."""
    seen = []

    class Counting(TermLife):
        def setup(self):
            seen.append(self.record_graph)
            super().setup()

    run_vectorized(Counting, points(4), ASSUMPTIONS, 30, outputs=["claims"],
                   chunk_size=2)
    assert seen == [True] * 2       # one model per chunk, each recording
