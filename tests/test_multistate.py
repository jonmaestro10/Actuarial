"""Multi-state Markov models, and the one word that separates them.

PLAN §5.2 asks for a multi-state Markov engine for the health and protection
family. It is the step past RFC-004's multiple decrements, and the
difference is **recovery**: a decrement model can express falling ill but
not getting better, because its populations only ever shrink.

What this file pins:

**The invariant.** Rows of a transition matrix sum to one, so everybody in a
state at the start of a period is somewhere at the end of it. Total
occupancy is then conserved for the whole projection, exactly.

**The bridge back.** A two-state chain with an absorbing exit reproduces the
decrement model's survivorship to the last bit — so this generalises the old
engine rather than sitting beside it.

**The trap in running it monthly.** An annual matrix does not become a
monthly one by dividing; the monthly matrix is the twelfth matrix *root*.
The naive version is wrong by five percentage points here. And worse, a
valid annual matrix need not have a valid monthly one at all — the embedding
problem, which bites on entirely plausible sickness data and is refused
rather than papered over.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.core.runner import run
from engine.core.vector import run_vectorized
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import ModelPoint, from_dicts
from engine.data.multistate import (
    StateSpace,
    TransitionMatrix,
    occupancy,
)
from engine.library.income_protection import IncomeProtection

STATES = StateSpace(["healthy", "sick", "dead"], absorbing=["dead"])
RATES = {("healthy", "sick"): 0.04, ("healthy", "dead"): 0.005,
         ("sick", "healthy"): 0.30, ("sick", "dead"): 0.05}


def matrix(overrides=None):
    """The base sickness matrix, optionally with some rates replaced.

    Overrides arrive as a dict rather than as keywords because the keys are
    ``(from, to)`` tuples.
    """
    rates = dict(RATES)
    rates.update(overrides or {})
    return TransitionMatrix.from_rates(rates, STATES)


def assumptions(freq=1, **kw):
    row = dict(mortality=MortalityTable.flat(0.01), interest=0.03,
               transitions=matrix(), freq=freq)
    row.update(kw)
    return Assumptions(**row)


def point(**kw):
    row = {"id": "IP", "age_at_entry": 40, "term_years": 25,
           "annual_premium": 600.0, "annual_benefit": 20_000.0,
           "init_pols": 1}
    row.update(kw)
    return ModelPoint(**row)


def one(value) -> float:
    return float(np.asarray(value).reshape(-1)[0])


# --- the state space -----------------------------------------------------


def test_a_state_space_needs_at_least_two_states():
    with pytest.raises(ValueError, match="at least two states"):
        StateSpace(["alive"])


def test_duplicate_and_unknown_states_raise():
    with pytest.raises(ValueError, match="duplicate state names"):
        StateSpace(["a", "b", "a"])
    with pytest.raises(ValueError, match="unknown absorbing"):
        StateSpace(["a", "b"], absorbing=["c"])


def test_an_unknown_state_lists_the_ones_that_exist():
    with pytest.raises(KeyError, match=r"\['healthy', 'sick', 'dead'\]"):
        STATES.of("disabled")


# --- the invariant -------------------------------------------------------


def test_rows_must_sum_to_one_and_the_message_says_where():
    leaky = np.array([[0.9, 0.05, 0.04], [0.3, 0.65, 0.05], [0.0, 0.0, 1.0]])
    with pytest.raises(ValueError, match="rows must sum to 1"):
        TransitionMatrix(leaky, STATES)
    with pytest.raises(ValueError, match="state healthy"):
        TransitionMatrix(leaky, STATES)


def test_probabilities_outside_zero_to_one_raise():
    with pytest.raises(ValueError, match="below zero"):
        TransitionMatrix(
            np.array([[1.2, -0.2, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]),
            STATES,
        )


def test_an_absorbing_state_may_not_be_left():
    escaping = np.array([[0.95, 0.04, 0.01], [0.3, 0.65, 0.05],
                         [0.01, 0.0, 0.99]])
    with pytest.raises(ValueError, match="declared absorbing"):
        TransitionMatrix(escaping, STATES)


def test_the_diagonal_is_what_the_transitions_leave_behind():
    """Stating it separately invites it to disagree with the row beside
    it, so ``from_rates`` refuses and fills it in."""
    built = matrix()
    assert built.p("healthy", "healthy") == pytest.approx(1 - 0.045, rel=1e-15)
    assert built.p("sick", "sick") == pytest.approx(1 - 0.35, rel=1e-15)
    with pytest.raises(ValueError, match="do not state the diagonal"):
        TransitionMatrix.from_rates({("sick", "sick"): 0.6}, STATES)


def test_transitions_out_of_a_state_cannot_exceed_it():
    with pytest.raises(ValueError, match="more than the whole state"):
        TransitionMatrix.from_rates(
            {("healthy", "sick"): 0.7, ("healthy", "dead"): 0.5}, STATES
        )


def test_occupancy_is_conserved_exactly():
    path = occupancy(np.array([1.0, 0.0, 0.0]), matrix(), 40)
    assert np.abs(path.sum(axis=1) - 1.0).max() < 1e-14
    assert np.all(path >= 0.0)
    # The absorbing state only grows; the others need not.
    assert np.all(np.diff(path[:, 2]) >= 0.0)


# --- the bridge back to decrements ---------------------------------------


def test_a_two_state_chain_is_the_decrement_model():
    """The generalisation has to contain what it generalises. A single live
    state with an absorbing exit is survivorship, and reproduces
    ``(1 - q) ** t`` to the last bit."""
    q = 0.013
    alive = StateSpace(["alive", "dead"], absorbing=["dead"])
    chain = TransitionMatrix.from_rates({("alive", "dead"): q}, alive)
    path = occupancy(np.array([1.0, 0.0]), chain, 30)
    survival = np.array([(1.0 - q) ** t for t in range(31)])
    assert np.abs(path[:, 0] - survival).max() < 1e-14
    # And with no recovery the live state is monotone, as a decrement is.
    assert np.all(np.diff(path[:, 0]) <= 0.0)


def test_recovery_is_what_a_decrement_model_cannot_express():
    """With recovery on, the sick population rises and then falls — it is
    not a survivorship and cannot be written as a running product."""
    path = occupancy(np.array([1.0, 0.0, 0.0]), matrix(), 40)
    sick = path[:, 1]
    assert sick[1] > sick[0]
    peak = int(sick.argmax())
    assert 0 < peak < 40
    assert not np.all(np.diff(sick) >= 0.0)


# --- the matrix root -----------------------------------------------------


def test_the_monthly_matrix_is_the_twelfth_root():
    annual = matrix()
    monthly = annual.root(12)
    assert np.abs(monthly.power(12).matrix - annual.matrix).max() < 1e-13


def test_dividing_the_matrix_by_twelve_is_badly_wrong():
    """Quantified rather than asserted. Element-wise division ignores every
    path that leaves a state and comes back inside the year, which is the
    whole thing a multi-state model exists to capture."""
    annual = matrix()
    naive = annual.matrix / 12.0
    np.fill_diagonal(naive, 0.0)
    np.fill_diagonal(naive, 1.0 - naive.sum(axis=1))
    error = np.abs(np.linalg.matrix_power(naive, 12) - annual.matrix).max()
    assert error > 0.05          # five percentage points, on a probability


def test_the_first_root_is_the_matrix_itself():
    annual = matrix()
    assert annual.root(1) is annual


@pytest.mark.parametrize("bad", [0, -3])
def test_a_root_below_one_raises(bad):
    with pytest.raises(ValueError, match="must be >= 1"):
        matrix().root(bad)


# --- the embedding problem -----------------------------------------------


def test_a_valid_annual_matrix_need_not_have_a_valid_monthly_one():
    """The embedding problem, on entirely plausible sickness data: 85%
    of the sick recover within a year. There is no Markov chain on a
    monthly step that reproduces that annual matrix, and the honest answer
    is to say so rather than return a negative probability."""
    quick = TransitionMatrix.from_rates(
        {("healthy", "sick"): 0.02, ("healthy", "dead"): 0.004,
         ("sick", "healthy"): 0.85, ("sick", "dead"): 0.10}, STATES,
    )
    with pytest.raises(ValueError, match="negative probability"):
        quick.root(12)
    # And it is a property of the frequency, not of the matrix: annually it
    # is a perfectly good chain.
    assert np.abs(quick.matrix.sum(axis=1) - 1.0).max() < 1e-15
    assert quick.root(1) is quick


def test_a_complex_root_is_refused_as_such():
    faster = TransitionMatrix.from_rates(
        {("healthy", "sick"): 0.10, ("healthy", "dead"): 0.004,
         ("sick", "healthy"): 0.98, ("sick", "dead"): 0.01}, STATES,
    )
    with pytest.raises(ValueError, match="complex"):
        faster.root(12)


def test_a_singular_matrix_has_no_root():
    collapsing = TransitionMatrix(
        np.array([[0.0, 0.0, 1.0], [0.0, 0.0, 1.0], [0.0, 0.0, 1.0]]), STATES,
    )
    with pytest.raises(ValueError, match="singular"):
        collapsing.root(4)


# --- age dependence ------------------------------------------------------


def age_dependent():
    blocks = []
    for age in range(30, 61):
        death = 0.002 * 1.08 ** (age - 30)
        blocks.append(TransitionMatrix.from_rates(
            {("healthy", "sick"): 0.02 + 0.001 * (age - 30),
             ("healthy", "dead"): death,
             ("sick", "healthy"): 0.35, ("sick", "dead"): death * 6},
            STATES).matrix)
    return TransitionMatrix(np.stack(blocks), STATES, min_age=30)


def test_an_age_dependent_matrix_is_held_flat_above_and_raises_below():
    by_age = age_dependent()
    assert by_age.age_dependent
    assert by_age.p("healthy", "dead", 45) > by_age.p("healthy", "dead", 35)
    assert by_age.p("healthy", "dead", 99) == by_age.p("healthy", "dead", 60)
    with pytest.raises(KeyError, match="below the first tabulated age"):
        by_age.p("healthy", "dead", 20)
    with pytest.raises(ValueError, match="varies by age; pass one"):
        by_age.at()


def test_occupancy_follows_the_ages_it_is_given():
    by_age = age_dependent()
    ages = list(range(30, 70))
    path = occupancy(np.array([1.0, 0.0, 0.0]), by_age, 39, ages=ages)
    assert np.abs(path.sum(axis=1) - 1.0).max() < 1e-14
    assert path[-1, 2] > path[20, 2]


# --- through the template ------------------------------------------------


def test_the_template_conserves_the_population_for_the_whole_projection():
    """States are not masked at the end of the term: a policy's term is a
    property of its cashflows, not of the life. So the invariant holds all
    the way to the horizon."""
    model = IncomeProtection(point(), assumptions(), 40)
    for t in range(41):
        assert one(model.lives(t)) == pytest.approx(1.0, abs=1e-13)
    assert one(model.healthy(30)) > 0.0        # past the 25-year term


def test_premiums_stop_when_a_life_falls_sick():
    """Waiver of premium is not a rider here — premiums are a cashflow of
    the healthy state, so a sick life pays nothing by construction."""
    model = IncomeProtection(point(), assumptions(), 30)
    for t in range(25):
        assert one(model.premiums(t)) == pytest.approx(
            one(model.healthy(t)) * 600.0, rel=1e-14
        )
        assert one(model.benefits(t)) == pytest.approx(
            one(model.sick(t)) * 20_000.0, rel=1e-14
        )


def test_cashflows_stop_at_the_end_of_the_term_but_the_chain_does_not():
    model = IncomeProtection(point(term_years=10), assumptions(), 20)
    assert one(model.premiums(9)) > 0.0
    assert one(model.premiums(10)) == 0.0
    assert one(model.benefits(10)) == 0.0
    assert one(model.lives(20)) == pytest.approx(1.0, abs=1e-13)


def test_recoveries_are_a_real_output():
    model = IncomeProtection(point(), assumptions(), 30)
    assert one(model.incidence(0)) == pytest.approx(0.04, rel=1e-14)
    assert one(model.recoveries(0)) == 0.0        # nobody sick yet
    assert one(model.recoveries(5)) > 0.0
    assert one(model.recoveries(5)) == pytest.approx(
        one(model.sick(5)) * 0.30, rel=1e-14
    )


def test_the_template_matches_the_forward_recursion_run_directly():
    model = IncomeProtection(point(), assumptions(), 20)
    path = occupancy(np.array([1.0, 0.0, 0.0]), matrix(), 20)
    for t in range(21):
        assert one(model.healthy(t)) == pytest.approx(path[t, 0], rel=1e-14)
        assert one(model.sick(t)) == pytest.approx(path[t, 1], rel=1e-14)
        assert one(model.dead(t)) == pytest.approx(path[t, 2], rel=1e-14)


# --- sub-annual ----------------------------------------------------------


def test_freq_one_uses_the_annual_matrix_itself():
    a = assumptions(freq=1)
    assert a.periodic_transitions() is a.transitions


def test_a_monthly_projection_lands_on_the_annual_one_at_anniversaries():
    """The point of taking the matrix root: twelve monthly steps compose to
    exactly one annual step, so a monthly projection agrees with the annual
    one wherever they are both defined."""
    annual = IncomeProtection(point(), assumptions(freq=1), 25)
    monthly = IncomeProtection(point(), assumptions(freq=12), 25 * 12)
    for year in range(26):
        for name in ("healthy", "sick", "dead"):
            assert one(getattr(monthly, name)(year * 12)) == pytest.approx(
                one(getattr(annual, name)(year)), rel=1e-12
            ), f"{name} at year {year}"


def test_a_monthly_projection_conserves_the_population_too():
    model = IncomeProtection(point(), assumptions(freq=12), 120)
    for t in range(0, 121, 7):
        assert one(model.lives(t)) == pytest.approx(1.0, abs=1e-12)


def test_an_assumption_set_without_a_matrix_says_so():
    a = Assumptions(mortality=MortalityTable.flat(0.01), interest=0.03)
    with pytest.raises(ValueError, match="carries no transition matrix"):
        a.periodic_transitions()


# --- executors and the registry ------------------------------------------


def test_the_two_executors_agree_bitwise():
    points = from_dicts([
        {"id": f"IP{i}", "age_at_entry": 35 + 5 * i, "term_years": 20,
         "annual_premium": 500.0 * (i + 1),
         "annual_benefit": 15_000.0 * (i + 1), "init_pols": 1}
        for i in range(4)
    ])
    a = assumptions()
    outputs = ["healthy", "sick", "dead", "premiums", "benefits", "recoveries"]
    interpreted = run(IncomeProtection, points, a, 25, outputs=outputs)
    vectorized = run_vectorized(IncomeProtection, points, a, 25,
                                outputs=outputs)
    for name in outputs:
        assert np.array_equal(
            np.array([mp[name] for mp in interpreted.per_mp]).T,
            np.asarray(vectorized.array(name)),
        ), name


def test_the_run_registry_tells_transition_bases_apart():
    from engine.core.registry import record_run

    points = from_dicts([point().__dict__])
    _, base = record_run(IncomeProtection, points, assumptions(), 25,
                         outputs=["benefits"])
    _, worse = record_run(
        IncomeProtection, points,
        assumptions(transitions=matrix({("healthy", "sick"): 0.06})), 25,
        outputs=["benefits"],
    )
    assert base.run_id != worse.run_id
    assert base.results_digest != worse.results_digest
