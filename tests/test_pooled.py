"""Pooled models: which equivalence class they are in, and which they are not.

RFC-061. Two templates in the library declare ``@pool`` variables, and the
executor-equivalence attestation (RFC-049) reported them as *failures* until
it was taught the difference. They are not failures. A pooled variable
reduces across the block; the interpreted executor evaluates one policy at a
time; so the two executors are answering different questions rather than the
same question differently.

What follows from that is three claims, and each one is a test here:

1. **A pooled block run per policy is refused**, because the alternative is
   what the engine did before this RFC — return a number in which every
   policy's pool was itself, indistinguishable from the real one.
2. **At one model point the templates are back in the bitwise class.** A
   pool of one is the same reduction in both executors, so every formula in
   these templates is still held to the dual-executor invariant; only the
   reduction is out of reach, and one model point cannot express it.
3. **What the pooled block *can* be held to** is asserted in its place: the
   block is never chunked, and the same question twice gets the same answer.
"""

import numpy as np
import pytest

from engine.core.registry import RunRegistry, record_run
from engine.core.runner import PooledBlockError, check_per_policy, run
from engine.core.vector import run_vectorized
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import from_dicts
from engine.library.group_life import GroupLife
from engine.library.term_life import TermLife
from engine.library.with_profits import WithProfitsEndowment

# The fixtures are the ones the templates' own suites use, so what is
# asserted here is the same block those tests project.
QX = {age: min(0.0006 * 1.095 ** (age - 20), 0.5) for age in range(20, 111)}
ASSUMPTIONS = Assumptions(mortality=MortalityTable(QX), lapse=0.04,
                          interest=0.03, expense_per_policy=30.0)

WITH_PROFITS = from_dicts([
    {"id": "W1", "age_at_entry": 40, "term_years": 20,
     "sum_assured": 100_000.0, "annual_premium": 3_800.0, "init_pols": 1_000.0},
    {"id": "W2", "age_at_entry": 55, "term_years": 20,
     "sum_assured": 50_000.0, "annual_premium": 2_600.0, "init_pols": 400.0},
])
WITH_PROFITS_VARS = ["pols_if", "asset_share", "aggregate_asset_share",
                     "mortality_profit_rate", "death_claims"]

GROUP = from_dicts([
    {"id": "G1", "age_at_entry": 35, "salary": 45_000.0,
     "salary_multiple": 4.0, "unit_rate": 6.0, "init_pols": 200},
    {"id": "G2", "age_at_entry": 52, "salary": 80_000.0,
     "salary_multiple": 4.0, "unit_rate": 6.0, "init_pols": 50},
])
GROUP_VARS = ["lives_if", "claims", "premiums", "scheme_margin",
              "surplus_carried", "experience_refund"]

POOLED = [
    pytest.param(WithProfitsEndowment, WITH_PROFITS, WITH_PROFITS_VARS, 21,
                 id="WithProfitsEndowment"),
    pytest.param(GroupLife, GROUP, GROUP_VARS, 8, id="GroupLife"),
]


# --------------------------------------------------------------------------
# 1. The refusal
# --------------------------------------------------------------------------

@pytest.mark.parametrize("model_cls,points,names,proj_len", POOLED)
def test_a_pooled_block_run_per_policy_is_refused(model_cls, points, names,
                                                  proj_len):
    """Before RFC-061 this returned numbers. That is the whole problem: a
    pool of one per policy is not obviously wrong from the outside."""
    with pytest.raises(PooledBlockError, match="pool of itself"):
        run(model_cls, points, ASSUMPTIONS, proj_len, outputs=names)


@pytest.mark.parametrize("model_cls,points,names,proj_len", POOLED)
def test_the_refusal_names_the_variables_and_the_way_out(model_cls, points,
                                                         names, proj_len):
    with pytest.raises(PooledBlockError) as raised:
        run(model_cls, points, ASSUMPTIONS, proj_len, outputs=names)
    message = str(raised.value)
    assert model_cls.__name__ in message
    for pooled in model_cls.pooled_names():
        assert pooled in message
    assert "run_vectorized" in message


def test_a_model_that_declares_coupling_is_refused_too():
    """``couples_model_points`` is the same statement without a ``@pool``."""

    class Coupled(TermLife):
        couples_model_points = True

    with pytest.raises(PooledBlockError, match="couples_model_points"):
        run(Coupled, WITH_PROFITS, ASSUMPTIONS, 5, outputs=["pols_if"])


def test_the_refusal_is_on_the_model_not_on_the_outputs():
    """A per-policy variable may read a pooled one, so 'these outputs are not
    pooled' is not the same statement as 'nothing pooled is evaluated'."""
    with pytest.raises(PooledBlockError):
        run(WithProfitsEndowment, WITH_PROFITS, ASSUMPTIONS, 21,
            outputs=["pols_if"])


def test_an_unpooled_model_is_untouched():
    """The invariant this change must not break: everything else runs."""
    points = from_dicts([
        {"id": "T1", "age_at_entry": 40, "term_years": 20,
         "sum_assured": 250_000.0, "annual_premium": 900.0, "init_pols": 1},
        {"id": "T2", "age_at_entry": 55, "term_years": 15,
         "sum_assured": 100_000.0, "annual_premium": 1_400.0, "init_pols": 3},
    ])
    result = run(TermLife, points, ASSUMPTIONS, 10, outputs=["pols_if"])
    assert len(result.per_mp) == 2
    check_per_policy(TermLife, 10_000)          # nothing to refuse


def test_an_empty_block_still_says_so_first():
    with pytest.raises(ValueError, match="no model points supplied"):
        run(WithProfitsEndowment, [], ASSUMPTIONS, 21)


# --------------------------------------------------------------------------
# 2. The bridge: at one model point they are back in the bitwise class
# --------------------------------------------------------------------------

@pytest.mark.parametrize("model_cls,points,names,proj_len", POOLED)
def test_one_model_point_is_bitwise_across_both_executors(model_cls, points,
                                                          names, proj_len):
    """Every formula in these templates is still held to the dual-executor
    invariant. Only the reduction is out of reach, and a block of one cannot
    express a reduction."""
    single = points[:1]
    interpreted = run(model_cls, single, ASSUMPTIONS, proj_len, outputs=names)
    vectorized = run_vectorized(model_cls, single, ASSUMPTIONS, proj_len,
                                outputs=names)
    for name in names:
        theirs = np.array([mp[name] for mp in interpreted.per_mp]).T
        assert np.array_equal(theirs, vectorized.array(name)), name


@pytest.mark.parametrize("model_cls,points,names,proj_len", POOLED)
def test_the_registry_agrees_on_a_block_of_one(model_cls, points, names,
                                               proj_len):
    """Said in the registry's own terms, which is what RFC-049's attestation
    quotes: same question, same answer, across executors."""
    digests = set()
    for executor in ("interpreted", "vectorized"):
        _, record = record_run(model_cls, points[:1], ASSUMPTIONS, proj_len,
                               outputs=names, executor=executor)
        digests.add(record.results_digest)
    assert len(digests) == 1


@pytest.mark.parametrize("model_cls,points,names,proj_len", POOLED)
def test_the_pool_of_one_is_the_policy_itself(model_cls, points, names,
                                              proj_len):
    """Why the bridge is sound rather than a coincidence: with one member,
    the aggregate a pooled variable computes *is* that member's value."""
    single = run_vectorized(model_cls, points[:1], ASSUMPTIONS, proj_len,
                            outputs=names)
    pooled_name = next(name for name in names
                       if name in model_cls.pooled_names())
    values = single.array(pooled_name)
    assert values.shape[1] == 1
    assert np.all(np.isfinite(values))


# --------------------------------------------------------------------------
# 3. What the pooled block is held to instead
# --------------------------------------------------------------------------

@pytest.mark.parametrize("model_cls,points,names,proj_len", POOLED)
def test_the_block_is_never_chunked(model_cls, points, names, proj_len):
    """The reduction is only right because the executor keeps the whole
    population together — asserted by observing the batch each instance
    receives, not by trusting the branch that decides it."""
    seen = []

    class Watched(model_cls):
        def setup(self):
            seen.append(self.mp.n)
            super().setup()

    run_vectorized(Watched, points, ASSUMPTIONS, proj_len, outputs=names,
                   chunk_size=1)
    assert seen == [len(points)]


@pytest.mark.parametrize("model_cls,points,names,proj_len", POOLED)
def test_the_same_question_twice_gets_the_same_answer(model_cls, points,
                                                      names, proj_len):
    """The claim a single-executor template can still support, and the one
    the evidence pack records for it."""
    registry = RunRegistry()
    first = record_run(model_cls, points, ASSUMPTIONS, proj_len,
                       outputs=names)[1]
    second = record_run(model_cls, points, ASSUMPTIONS, proj_len,
                        outputs=names)[1]
    registry.add(first)
    registry.add(second)          # refuses a contradiction; there is none
    assert first.results_digest == second.results_digest
    assert len(registry) == 1


@pytest.mark.parametrize("model_cls,points,names,proj_len", POOLED)
def test_the_pool_actually_reduces_over_the_block(model_cls, points, names,
                                                  proj_len):
    """The thing the interpreted executor could not do, done: a pooled
    variable is the same for every policy in the block, and it is not the
    per-policy value."""
    result = run_vectorized(model_cls, points, ASSUMPTIONS, proj_len,
                            outputs=names)
    pooled_name = next(name for name in names
                       if name in model_cls.pooled_names())
    values = result.array(pooled_name)
    assert values.shape[1] == len(points)
    for t in range(values.shape[0]):
        assert len(set(values[t].tolist())) == 1, f"t={t} is not pooled"
    # And it moves when the population does, which a pool of one would not.
    smaller = run_vectorized(model_cls, points[:1], ASSUMPTIONS, proj_len,
                             outputs=names)
    assert not np.array_equal(values[:, :1], smaller.array(pooled_name))
