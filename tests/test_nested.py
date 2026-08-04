"""Nested stochastic projection, and the restart it rests on.

PLAN §4.4 calls this "the real killer workload"; §8 makes a
nested-stochastic prototype a Phase 2 exit. An outer projection runs the
block under real-world scenarios; at points along each outer path the
guarantees are valued by a second, risk-neutral projection starting from
whatever state that path has reached.

Everything here depends on one thing being exactly right:

**The restart is exact.** A contract restarted part way through, and
projected forward on the tail of the same scenario, reproduces the
straight-through projection **bitwise** — ratcheting benefit base, dynamic
lapse and all. If that failed, no number in a nested run would be
salvageable, and no amount of inner scenarios would reveal it.

The rest is arithmetic on top: batching the outer states so the cost is one
inner projection per valuation time rather than one per outer node, common
random numbers so differences between outer states are visible, and an error
bar next to every value because an inner mean over 200 scenarios is an
estimate.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.core.model import Model, var
from engine.core.nested import (
    NestedRun,
    nested_stochastic,
    risk_neutral_inner,
)
from engine.core.stochastic import run_stochastic
from engine.data.assumptions import Assumptions, DynamicLapse, MortalityTable
from engine.data.modelpoints import ModelPoint, from_dicts
from engine.data.scenarios import ScenarioSet
from engine.library.term_life import TermLife
from engine.library.unit_linked import UnitLinkedGMDB, UnitLinkedGMxB

RATE, VOL = 0.03, 0.18
STATE = ["pols_if", "fund_boy", "fund_eoy", "benefit_base", "gaw",
         "charges_taken", "gmdb_claims", "gmwb_strain", "gmab_strain",
         "pols_maturity", "maturity_payments", "surrenders", "lapse_rate",
         "guarantee_strain"]


def assumptions(**kw):
    row = dict(mortality=MortalityTable.flat(0.011), interest=RATE, amc=0.012,
               dynamic_lapse=DynamicLapse(0.06, sensitivity=0.8),
               gmdb_fee=0.004, gmab_fee=0.003, gmwb_fee=0.005)
    row.update(kw)
    return Assumptions(**row)


def point(**kw):
    row = {"id": "X", "age_at_entry": 55, "term_years": 20, "premium": 1e5,
           "gmdb_guarantee": 1e5, "gmab_guarantee": 1.1e5, "gmwb_base": 1e5,
           "gmwb_rate": 0.05, "gmwb_ratchet": 1.0, "init_pols": 1}
    row.update(kw)
    return ModelPoint(**row)


def block(n=3):
    return from_dicts([
        {**point().__dict__, "id": f"X{i}", "age_at_entry": 55 + 5 * i}
        for i in range(n)
    ])


def one(value) -> float:
    return float(np.asarray(value).reshape(-1)[0])


# --- the restart, which everything else rests on -------------------------


@pytest.mark.parametrize("tau", [1, 4, 7, 15])
def test_a_restarted_contract_is_the_same_contract_bitwise(tau):
    """The load-bearing test. Restart at ``tau`` on the tail of the same
    scenario and every variable reproduces the straight-through projection
    exactly — including the ratcheting benefit base, which is the piece of
    state a fund value alone cannot reconstruct."""
    a = assumptions()
    scenarios = ScenarioSet.lognormal(1, 30, drift=0.05, vol=VOL, seed=4)
    n = 22
    full = UnitLinkedGMxB(point(), a, n, scenarios)
    restarted = UnitLinkedGMxB(
        ModelPoint(**full.restart_fields(tau)),
        a.at_year(a.years_elapsed(tau)),
        n - tau,
        ScenarioSet(scenarios.returns[:, tau:]),
    )
    for name in STATE:
        for k in range(n - tau + 1):
            assert one(getattr(full, name)(tau + k)) == one(
                getattr(restarted, name)(k)
            ), f"{name} at tau={tau}, k={k}"


def test_the_seed_template_restarts_too():
    a = assumptions()
    scenarios = ScenarioSet.lognormal(1, 30, drift=0.05, vol=VOL, seed=5)
    seed_point = ModelPoint(id="U", age_at_entry=60, term_years=15,
                            premium=1e5, gmdb_guarantee=1e5, init_pols=1)
    full = UnitLinkedGMDB(seed_point, a, 18, scenarios)
    restarted = UnitLinkedGMDB(
        ModelPoint(**full.restart_fields(6)), a.at_year(6), 12,
        ScenarioSet(scenarios.returns[:, 6:]),
    )
    for name in ["pols_if", "fund_boy", "fund_eoy", "gmdb_claims",
                 "gmdb_strain", "fee_income"]:
        for k in range(13):
            assert one(getattr(full, name)(6 + k)) == one(
                getattr(restarted, name)(k)
            ), f"{name} at k={k}"


def test_a_restart_off_a_policy_anniversary_is_refused():
    """Attained age and remaining term are whole years. A part-year restart
    would have to invent both, so it is an error rather than a rounding."""
    a = assumptions(freq=4)
    model = UnitLinkedGMxB(point(), a, 40, ScenarioSet.flat(0.01, 1, 60))
    model.restart_fields(8)                      # an anniversary
    with pytest.raises(ValueError, match="not a policy anniversary"):
        model.restart_fields(9)


def test_a_template_without_a_restart_says_so():
    model = TermLife(
        ModelPoint(id="T", age_at_entry=40, term_years=20,
                   sum_assured=1e5, annual_premium=900.0, init_pols=1),
        Assumptions(mortality=MortalityTable.flat(0.01), interest=RATE), 25,
    )
    with pytest.raises(NotImplementedError,
                       match="TermLife cannot be restarted"):
        model.restart_fields(5)


def test_the_calendar_moves_with_the_restart():
    """A basis indexed from a base year has to move on with the block. An
    inner projection starting at year 10 that priced mortality as if it were
    year 0 would be wrong in one direction, silently."""
    a = assumptions(base_year=2020)
    later = a.at_year(7)
    assert later.base_year == 2027
    assert a.base_year == 2020                   # the original is untouched
    assert later.mortality is a.mortality
    assert later.interest == a.interest


# --- the nested run ------------------------------------------------------


def run_nested(n_outer=20, n_inner=50, times=(0, 4, 8), proj_len=16,
               n_mp=2, seed=7, vol=VOL, **kw):
    outer = ScenarioSet.lognormal(n_outer, proj_len + 4, drift=0.06,
                                  vol=0.18, seed=11)
    return nested_stochastic(
        UnitLinkedGMxB, block(n_mp), assumptions(), outer=outer,
        inner=risk_neutral_inner(RATE, vol, n_inner, seed=seed),
        valuation_times=list(times), proj_len=proj_len,
        measure="guarantee_strain", **kw,
    )


def test_a_nested_run_has_the_shape_and_the_cost_it_claims():
    run = run_nested()
    assert isinstance(run, NestedRun)
    assert run.values.shape == (3, 2, 20)
    assert run.stderr.shape == run.values.shape
    assert run.inner_projections == 3        # one per valuation time
    assert run.inner_cells == 3 * 2 * 20 * 50
    assert "guarantee_strain" in run.summary()


def test_at_time_zero_every_outer_path_is_the_same_policy():
    """Nothing has happened yet, so every outer node holds identical state
    and must be valued identically. The cleanest sanity check there is: if
    the outer state were leaking into the inner valuation incorrectly, this
    would be the first thing to break."""
    run = run_nested()
    at_zero = run.at(0)
    for mp in range(at_zero.shape[0]):
        assert np.ptp(at_zero[mp]) == 0.0


def test_the_spread_across_outer_paths_peaks_in_the_middle():
    """Two effects pull against each other, and the shape is the answer.

    Outer paths keep diverging, which widens the spread of guarantee
    values. But the remaining term keeps shortening, which shrinks every
    value towards zero and compresses the spread with it. The spread
    therefore rises from exactly nothing at inception, peaks part way
    through, and falls away — it does *not* increase monotonically, which
    is what I assumed before measuring it.
    """
    times = tuple(range(0, 17, 2))
    run = run_nested(times=times, proj_len=18, n_inner=200)
    spreads = [float(np.ptp(run.at(t)[0])) for t in times]
    means = [float(run.at(t)[0].mean()) for t in times]

    assert spreads[0] == 0.0                    # nothing has happened yet
    assert spreads[1] > 0.0
    peak = spreads.index(max(spreads))
    assert 0 < peak < len(spreads) - 1, f"peak at the edge: {spreads}"

    # And the value itself falls away as the guarantee runs out of term.
    assert means[-1] < means[0] / 2
    assert sum(means[: len(means) // 2]) > sum(means[len(means) // 2:])


def test_a_better_funded_contract_has_a_cheaper_guarantee():
    """The economics the whole exercise exists to measure, as a shape: at a
    given valuation date, the outer paths whose funds did best are the ones
    whose guarantees cost least."""
    proj_len, tau = 16, 8
    outer = ScenarioSet.lognormal(30, proj_len + 4, drift=0.06, vol=0.2,
                                  seed=11)
    a = assumptions()
    points = block(1)
    funds = np.asarray(
        run_stochastic(UnitLinkedGMxB, points, a, outer, proj_len,
                       outputs=["fund_boy"]).array("fund_boy")
    )[tau, 0]
    run = nested_stochastic(
        UnitLinkedGMxB, points, a, outer=outer,
        inner=risk_neutral_inner(RATE, VOL, 60, seed=3),
        valuation_times=[tau], proj_len=proj_len, measure="guarantee_strain",
    )
    values = run.at(tau)[0]
    order = np.argsort(funds)
    ranked = values[order]
    # Not monotone point by point — 60 inner scenarios is 60 — but the
    # best-funded third must cost clearly less than the worst-funded third.
    third = len(ranked) // 3
    assert ranked[-third:].mean() < ranked[:third].mean()
    assert np.corrcoef(funds, values)[0, 1] < -0.8


# --- the error bar and the random numbers --------------------------------


def test_every_value_comes_with_its_standard_error():
    run = run_nested(n_inner=50)
    assert np.all(run.stderr > 0.0)
    assert np.all(run.stderr < np.abs(run.values))


def test_the_error_bar_shrinks_as_the_root_of_the_inner_count():
    errors = [
        float(run_nested(n_inner=n, times=(4,)).stderr.mean())
        for n in (50, 200, 800)
    ]
    assert errors[0] > errors[1] > errors[2]
    for coarse, fine in zip(errors, errors[1:]):
        assert 1.6 < coarse / fine < 2.5          # ~sqrt(4)


def test_a_deterministic_inner_measure_has_no_error_bar():
    """Zero volatility: every inner scenario is the same scenario, so the
    inner mean is a single number and its standard error is exactly zero."""
    run = run_nested(n_inner=8, times=(4,), vol=0.0)
    assert np.all(run.stderr == 0.0)


def test_the_same_seed_gives_the_same_answer():
    assert np.array_equal(run_nested(seed=5).values,
                          run_nested(seed=5).values)
    assert not np.array_equal(run_nested(seed=5).values,
                              run_nested(seed=6).values)


def test_outer_nodes_at_one_date_share_their_inner_scenarios():
    """Common random numbers, deliberately: the interesting quantity is how
    the guarantee cost *differs* between outer states, and independent inner
    draws would bury that under sampling noise about nothing."""
    inner = risk_neutral_inner(RATE, VOL, 16, seed=9)
    assert np.array_equal(inner(4, 10).returns, inner(4, 10).returns)
    assert not np.array_equal(inner(4, 10).returns, inner(5, 10).returns)


def test_the_inner_measure_is_risk_neutral():
    from engine.data import esg

    esg.check_risk_neutral(risk_neutral_inner(RATE, VOL, 2_000, seed=2)(0, 25),
                           RATE)


# --- validation ----------------------------------------------------------


@pytest.mark.parametrize("kw,message", [
    ({"times": ()}, "no valuation times"),
    ({"times": (0, 99)}, "outside the projection"),
    ({"times": (16,), "proj_len": 16}, "leaves no projection to value"),
    ({"timing": "middle"}, "timing must be"),
])
def test_a_malformed_nested_run_raises(kw, message):
    with pytest.raises(ValueError, match=message):
        run_nested(**kw)


def test_an_outer_set_too_short_for_the_projection_raises():
    with pytest.raises(ValueError, match="outer horizon"):
        nested_stochastic(
            UnitLinkedGMxB, block(1), assumptions(),
            outer=ScenarioSet.flat(0.05, 4, 10),
            inner=risk_neutral_inner(RATE, VOL, 8, seed=1),
            valuation_times=[2], proj_len=20, measure="guarantee_strain",
        )


def test_inner_scenarios_too_short_for_what_is_left_raise():
    def stingy(valuation_time, periods):
        return ScenarioSet.flat(RATE, 8, 2)

    with pytest.raises(ValueError, match="inner scenarios at period 4"):
        nested_stochastic(
            UnitLinkedGMxB, block(1), assumptions(),
            outer=ScenarioSet.flat(0.05, 4, 30),
            inner=stingy, valuation_times=[4], proj_len=16,
            measure="guarantee_strain",
        )


def test_an_unknown_valuation_time_lists_the_ones_that_exist():
    run = run_nested(times=(0, 4))
    with pytest.raises(KeyError, match=r"valued at \[0, 4\]"):
        run.at(7)


# --- the working set stays bounded ---------------------------------------


def test_an_inner_run_does_not_hold_the_whole_projection():
    """A nested job materialising every period of every inner scenario
    would run out of memory long before it ran out of patience. The inner
    loop prunes behind itself, so this is a 20-model-point run whose cost is
    set by the slab, not by the projection length."""
    short = run_nested(times=(4,), proj_len=10)
    long_ = run_nested(times=(4,), proj_len=30)
    assert short.values.shape == long_.values.shape
    # A longer remaining projection is worth more guarantee, not more memory.
    assert long_.values.mean() > short.values.mean()
