"""Reproducibility: the run registry and the digest under it.

PLAN.md §2.3 makes reproducibility the backbone of the accuracy story, so
these tests are about the two ways a fingerprint can lie rather than about
the plumbing:

- **A digest that is not stable** certifies nothing. Python randomises string
  hashing per interpreter, so the first thing checked here is that a digest
  computed in a *separate process with a different seed* is the same digest.
- **A digest that is not sensitive** is worse than none, because it invites
  you to stop checking. Every input that can move a number is perturbed one
  at a time and required to change the ``run_id``; anything the encoder does
  not understand raises rather than being skipped.

The pair ``(run_id, results_digest)`` is the assertion the registry exists
to make: same question, same answer. A repeat run that produced something
different is a determinism failure, and ``RunRegistry.add`` refuses it.
"""

import json
import os
import subprocess
import sys
import textwrap
from datetime import date

import numpy as np
import pytest

from engine.core.fingerprint import (
    UnfingerprintableError,
    fingerprint,
    source_fingerprint,
)
from engine.core.registry import (
    NonDeterministicRunError,
    RunRecord,
    RunRegistry,
    record_run,
)
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import ModelPoint
from engine.data.mortality import MortalityBasis
from engine.data.scenarios import ScenarioSet
from engine.library.term_life import TermLife
from engine.library.unit_linked import UnitLinkedGMDB

QX = {age: min(0.0006 * 1.095 ** (age - 20), 0.5) for age in range(20, 111)}
PROJ_LEN = 30
OUTPUTS = ["pols_if", "claims", "premiums", "expenses"]


def assumptions(**overrides):
    fields = dict(mortality=MortalityTable(QX), lapse=0.04, interest=0.03,
                  expense_per_policy=50.0)
    fields.update(overrides)
    return Assumptions(**fields)


def points(**overrides):
    base = dict(age_at_entry=45, term_years=20, sum_assured=250_000.0,
                annual_premium=1_100.0, init_pols=1)
    base.update(overrides)
    return [
        ModelPoint(id="T1", **base),
        ModelPoint(id="T2", **{**base, "age_at_entry": 55, "term_years": 10}),
    ]


def do_run(**overrides):
    kwargs = dict(
        model_cls=TermLife, modelpoints=points(), assumptions=assumptions(),
        proj_len=PROJ_LEN, outputs=OUTPUTS,
    )
    kwargs.update(overrides)
    return record_run(
        kwargs.pop("model_cls"), kwargs.pop("modelpoints"),
        kwargs.pop("assumptions"), kwargs.pop("proj_len"), **kwargs
    )


# --- the digest must be stable ---------------------------------------------


def test_a_digest_survives_a_different_interpreter_and_hash_seed():
    """The one that rules out ``hash()``. Python salts string hashing per
    process, so a digest built on it would silently differ between the run
    that produced a result and the run that audits it."""
    script = textwrap.dedent(
        """
        from engine.core.fingerprint import fingerprint
        from engine.data.assumptions import Assumptions, MortalityTable
        qx = {age: min(0.0006 * 1.095 ** (age - 20), 0.5) for age in range(20, 111)}
        a = Assumptions(mortality=MortalityTable(qx), lapse=0.04,
                        interest=0.03, expense_per_policy=50.0)
        print(fingerprint(a))
        """
    )
    here = fingerprint(assumptions())
    for seed in ("0", "1", "12345"):
        environment = {**os.environ, "PYTHONHASHSEED": seed}
        out = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True,
            env=environment, check=True,
        )
        assert out.stdout.strip() == here, f"PYTHONHASHSEED={seed}"


def test_content_not_identity_decides_the_digest():
    assert fingerprint(assumptions()) == fingerprint(assumptions())
    assert fingerprint(points()) == fingerprint(points())
    # Dict insertion order must not matter; sequence order must.
    assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})
    assert fingerprint([1, 2]) != fingerprint([2, 1])


def test_structurally_different_values_do_not_collide():
    assert len({fingerprint(v) for v in ([1, 2], (1, 2), "12", {1: 2}, {1, 2})}) == 5
    assert fingerprint(1) != fingerprint(1.0)
    assert fingerprint(0) != fingerprint(False)
    assert fingerprint(None) != fingerprint("None")


def test_arrays_are_hashed_by_content_and_shape():
    a = np.arange(6, dtype=np.float64)
    assert fingerprint(a) == fingerprint(a.copy())
    assert fingerprint(a) != fingerprint(a.reshape(2, 3))
    assert fingerprint(a) != fingerprint(a.astype(np.float32))
    assert fingerprint(a.reshape(2, 3)) == fingerprint(
        np.asfortranarray(a.reshape(2, 3))
    )  # layout is not content


def test_an_unknown_type_raises_rather_than_being_skipped():
    """A digest that quietly drops what it cannot read certifies less than it
    appears to."""

    class Opaque:
        __slots__ = ()

    with pytest.raises(UnfingerprintableError, match="__fingerprint__"):
        fingerprint(Opaque())
    with pytest.raises(UnfingerprintableError):
        fingerprint({"assumption": Opaque()})


def test_evaluation_history_does_not_change_an_assumption_set():
    """A ``MortalityBasis`` fills lookup caches on demand. If those counted
    towards its identity, an assumption set would change simply by being
    used — and every fingerprint taken after a projection would differ from
    the one taken before it."""
    basis = MortalityBasis(
        {"M": QX}, year_start=2014,
        improvement={"M": {year: {age: 0.01 for age in QX}
                           for year in range(2015, 2031)}},
    )
    before = fingerprint(basis)
    basis.q_at(np.array([70]), sex=["M"], year=2050)
    basis.survival_curve([date(1956, 1, 1)], [date(2021, 1, 1)], ["M"], 12, 240)
    assert fingerprint(basis) == before


# --- the digest must be sensitive ------------------------------------------


def test_repeating_a_run_reproduces_both_digests():
    first_result, first = do_run()
    second_result, second = do_run()
    assert first.run_id == second.run_id
    assert first.results_digest == second.results_digest
    assert first.matches(second)
    for name in OUTPUTS:
        assert np.array_equal(
            first_result.array(name), second_result.array(name)
        )


@pytest.mark.parametrize(
    "label,overrides",
    [
        ("a single mortality rate",
         dict(assumptions=lambda: assumptions(
             mortality=MortalityTable({**QX, 70: QX[70] * 1.000001})))),
        ("the interest rate", dict(assumptions=lambda: assumptions(interest=0.031))),
        ("the lapse rate", dict(assumptions=lambda: assumptions(lapse=0.041))),
        ("the payment frequency", dict(assumptions=lambda: assumptions(freq=12))),
        ("a model point field",
         dict(modelpoints=lambda: points(sum_assured=250_001.0))),
        ("the model point order",
         dict(modelpoints=lambda: list(reversed(points())))),
        ("the projection length", dict(proj_len=31)),
        ("the requested outputs", dict(outputs=["pols_if", "claims"])),
    ],
)
def test_changing_any_input_changes_the_run_id(label, overrides):
    baseline = do_run()[1]
    resolved = {
        key: value() if callable(value) else value
        for key, value in overrides.items()
    }
    changed = do_run(**resolved)[1]
    assert changed.run_id != baseline.run_id, label


def test_changing_a_formula_changes_the_run_id():
    """The model's source is part of its identity: two products that differ
    only in a formula must not be recorded as the same run."""

    class Cheaper(TermLife):
        @property
        def _marker(self):
            return "a different product"

    baseline = do_run()[1]
    variant = do_run(model_cls=Cheaper)[1]
    assert variant.model_source_digest != baseline.model_source_digest
    assert variant.run_id != baseline.run_id
    # A subclass that adds nothing at all still differs by its own source
    # line, which is the conservative direction.
    assert source_fingerprint(TermLife) != source_fingerprint(Cheaper)


def test_a_scenario_set_is_part_of_the_run():
    mps = [ModelPoint(id="U1", age_at_entry=55, term_years=20,
                      premium=100_000.0, gmdb_guarantee=100_000.0, init_pols=1)]
    a = Assumptions(mortality=MortalityTable(QX), lapse=0.02, interest=0.03,
                    amc=0.01)
    names = ["pols_if", "fund_boy", "gmdb_claims"]

    def stochastic(seed):
        return record_run(
            UnitLinkedGMDB, mps, a, 20, outputs=names,
            scenarios=ScenarioSet.lognormal(
                n_scenarios=8, horizon=21, drift=0.03, vol=0.2, seed=seed
            ),
        )[1]

    first, again, other = stochastic(7), stochastic(7), stochastic(8)
    assert first.run_id == again.run_id
    assert first.results_digest == again.results_digest
    assert other.run_id != first.run_id
    assert other.results_digest != first.results_digest
    assert first.n_scenarios == 8 and first.scenario_horizon == 21


# --- what run_id deliberately excludes -------------------------------------


@pytest.mark.parametrize("chunk_size", [1, 2, 64, None])
def test_the_chunk_size_changes_neither_the_question_nor_the_answer(chunk_size):
    """Excluding the chunk size from ``run_id`` is a claim that it cannot
    move a number. It is asserted here rather than assumed."""
    baseline = do_run(chunk_size=None)[1]
    chunked = do_run(chunk_size=chunk_size)[1]
    assert chunked.run_id == baseline.run_id
    assert chunked.results_digest == baseline.results_digest


def test_the_clock_is_recorded_but_not_part_of_the_identity():
    first, second = do_run()[1], do_run()[1]
    assert first.run_id == second.run_id
    assert first.created_at and second.created_at


def test_the_two_executors_ask_different_questions_and_get_the_same_answer():
    """The bitwise-equivalence claim, expressed through the registry: the
    executor is part of the run's identity, but not of its result."""
    vectorized = do_run(executor="vectorized")[1]
    interpreted = do_run(executor="interpreted")[1]
    assert interpreted.run_id != vectorized.run_id
    assert interpreted.results_digest == vectorized.results_digest


# --- the registry ----------------------------------------------------------


def test_the_registry_deduplicates_a_repeated_run():
    registry = RunRegistry()
    first = registry.add(do_run()[1])
    again = registry.add(do_run()[1])
    assert len(registry) == 1
    assert again is first
    assert registry.find(first.run_id) is first
    assert registry.find("not a run") is None


def test_the_registry_refuses_a_run_that_contradicts_itself():
    """The failure a per-number tolerance cannot catch: the same question
    answered two different ways."""
    registry = RunRegistry()
    record = do_run()[1]
    registry.add(record)
    tampered = RunRecord(**{**record.to_dict(),
                            "outputs": record.outputs,
                            "results_digest": "0" * 32})
    with pytest.raises(NonDeterministicRunError, match=record.run_id):
        registry.add(tampered)


def test_the_registry_round_trips_through_json(tmp_path):
    registry = RunRegistry()
    registry.add(do_run()[1])
    registry.add(do_run(proj_len=25)[1])
    path = tmp_path / "runs.json"
    registry.to_json(path)

    restored = RunRegistry.from_json(path)
    assert [r.to_dict() for r in restored] == [r.to_dict() for r in registry]
    # And it is readable by anything, not just by us.
    assert len(json.loads(path.read_text())) == 2


def test_the_registry_round_trips_through_parquet(tmp_path):
    pytest.importorskip("pyarrow")
    registry = RunRegistry()
    registry.add(do_run()[1])
    path = tmp_path / "runs.parquet"
    registry.to_parquet(path)
    assert [r.to_dict() for r in RunRegistry.from_parquet(path)] == [
        r.to_dict() for r in registry
    ]


def test_a_record_says_what_it_could_not_capture():
    """``source_digest`` sees a class and its bases, not module-level helpers
    a formula might call. The record carries the code version so a
    deployment can pin what the digest cannot reach."""
    record = do_run(code_version="abc1234")[1]
    assert record.code_version == "abc1234"
    assert record.engine_version
    assert record.model_name == "TermLife"
    assert record.model_module == "engine.library.term_life"


def test_mismatched_executor_and_scenarios_are_refused():
    scenarios = ScenarioSet.flat(0.05, n_scenarios=2, horizon=31)
    with pytest.raises(ValueError, match="scenario set"):
        do_run(executor="stochastic")
    with pytest.raises(ValueError, match="scenarios were supplied"):
        do_run(executor="vectorized", scenarios=scenarios)
    with pytest.raises(ValueError, match="unknown executor"):
        do_run(executor="numba")
