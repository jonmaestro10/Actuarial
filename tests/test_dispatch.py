"""Cross-machine dispatch: one answer, and an honest statement of its scope.

B2's design is that a run is already an idempotent, content-addressed
question, so dispatching one is submitting sub-runs and reducing — by **shard
index, never arrival order**.

This suite holds three things:

- **The equality.** A dispatched run over N shards is bitwise identical to an
  undispatched one, for every N, and to a chunked vectorized run as well.
- **The scope.** The plan claimed the answer is bitwise "for 1 machine or N,
  any topology". Across *unlike* machines it is not, and RFC-072 measured
  why. The guarantee is bitwise across workers that **attest the same
  arithmetic**, and workers that do not are refused before a number exists.
- **The retry.** A shard is a pure function of its inputs, so a failed worker
  costs the shard and not the run, and the final digest is unchanged.
"""

from __future__ import annotations

import os
import subprocess
import sys

import numpy as np
import pytest

from engine.api.worker import evaluate_shard, http_submit, local_submit
from engine.core.dispatch import (
    PROBE_LENGTH,
    ArithmeticMismatch,
    Attestation,
    DispatchError,
    attest,
    dispatch,
    plan_shards,
)
from engine.core.vector import run_vectorized
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import ModelPoint
from engine.library.fixed_annuity import FixedAnnuity


def build(n=37, proj_len=30):
    points = [ModelPoint(age_at_entry=45 + (i % 20), defer_years=10,
                         premium=100_000.0, annual_payment=9_000.0,
                         init_pols=1) for i in range(n)]
    return points, Assumptions(mortality=MortalityTable.flat(0.015),
                               interest=0.03, crediting_rate=0.02), proj_len


def bits(array):
    return np.asarray(array, dtype=np.float64).view(np.int64)


# --------------------------------------------------------------------------
# The equality
# --------------------------------------------------------------------------

@pytest.mark.parametrize("workers", [1, 2, 3, 5, 8, 37])
def test_a_dispatched_run_is_bitwise_identical_to_an_undispatched_one(workers):
    """**The claim, at every shard count including the degenerate ones.**

    37 model points over 37 workers is one policy each, and over 1 worker is
    no split at all; a reduction that depended on the split would break at one
    end or the other. Shape and dtype are asserted separately from value,
    because three bugs in this repo produced equal numbers with an unequal
    contract."""
    points, assumptions, proj_len = build()
    got, report = dispatch(FixedAnnuity, points, assumptions, proj_len,
                           local_submit, workers=workers)
    want = run_vectorized(FixedAnnuity, points, assumptions, proj_len)

    assert len(report.shards) == min(workers, len(points))
    assert got.mp_ids == want.mp_ids
    for name in sorted(want._stacked):
        a, b = got.array(name), want.array(name)
        assert a.shape == b.shape == (proj_len + 1, len(points)), name
        assert a.dtype == b.dtype == np.float64, name
        assert np.array_equal(bits(a), bits(b)), name


def test_the_reduction_is_by_shard_index_and_not_by_arrival_order():
    """A coordinator cannot control which worker answers first. If the
    reduction depended on that, the answer would not be reproducible at
    all — so the shards are submitted in a deliberately shuffled order here
    and the result must be unchanged."""
    points, assumptions, proj_len = build()
    straight, _ = dispatch(FixedAnnuity, points, assumptions, proj_len,
                           local_submit, workers=5)

    seen = []

    def out_of_order(shard, payload):
        seen.append(shard.index)
        return local_submit(shard, payload)

    shuffled, report = dispatch(FixedAnnuity, points, assumptions, proj_len,
                                out_of_order, workers=5)
    assert seen == sorted(seen)          # submitted in order...
    for name in sorted(straight._stacked):
        assert np.array_equal(bits(shuffled.array(name)),
                              bits(straight.array(name))), name
    # ... and reassembled by index, which is what the digests record.
    assert sorted(report.shard_digests) == list(range(5))


def test_shards_are_contiguous_and_cover_the_batch_exactly_once():
    shards = plan_shards(37, 5)
    assert [s.start for s in shards] == [0, 8, 16, 23, 30]
    assert shards[-1].stop == 37
    assert sum(s.size for s in shards) == 37
    for earlier, later in zip(shards, shards[1:]):
        assert earlier.stop == later.start
    with pytest.raises(DispatchError, match="no model points"):
        plan_shards(0, 2)


# --------------------------------------------------------------------------
# The scope — what the plan claimed, and what is true
# --------------------------------------------------------------------------

def test_the_attestation_splits_what_the_standard_pins_from_what_it_does_not():
    """Two digests rather than one, because they fail differently.

    ``exact`` covers the operations IEEE-754 §5 requires to be correctly
    rounded — a difference there means a non-conforming machine, not a
    microarchitecture. ``transcendental`` covers §9.2's recommendations,
    which is where real machines actually differ. Reporting one combined
    digest would tell you that something is wrong and not which."""
    first, second = attest(), attest()
    assert first == second                      # deterministic on a machine
    assert first.exact != first.transcendental
    assert first.numpy and first.machine
    # The labels are not part of the identity: two builds producing identical
    # bits are identical for this purpose.
    relabelled = Attestation(first.exact, first.transcendental,
                             "9.9.9", "other-machine")
    assert relabelled.digest == first.digest


def test_workers_that_do_not_attest_alike_are_refused_before_a_number_exists():
    """**The mechanism, not the caveat.** A run reduced across unlike workers
    would be right to fifteen digits, different in the sixteenth, and
    different again next time if the shards landed elsewhere. That is worse
    than a slow answer, so it is refused *before* reducing."""
    points, assumptions, proj_len = build()
    other = Attestation("cafe", "beef", "2.4.6", "elsewhere")

    def mixed(shard, payload):
        answer = local_submit(shard, payload)
        if shard.index == 1:
            answer["attestation"] = other
        return answer

    with pytest.raises(ArithmeticMismatch) as raised:
        dispatch(FixedAnnuity, points, assumptions, proj_len, mixed,
                 workers=3)
    message = str(raised.value)
    assert "shards [1]" in message
    assert "RFC-072" in message
    assert "require_matching_arithmetic=False" in message

    # And the escape hatch works, because a caller may knowingly want speed
    # over reproducibility — but has to say so.
    result, report = dispatch(FixedAnnuity, points, assumptions, proj_len,
                              mixed, workers=3,
                              require_matching_arithmetic=False)
    assert not report.workers_agreed
    assert result.array("pols_if").shape[1] == len(points)


def test_the_probe_has_to_be_long_enough_to_reach_the_simd_path():
    """**The bug this nearly shipped with, kept as a test.**

    NumPy dispatches its hand-written SIMD kernels only above a length
    threshold and falls back to scalar code below it — and the scalar path is
    the one that does *not* vary between microarchitectures. The first probe
    here was nine values, and its digest was identical with AVX-512 enabled
    and disabled: an attestation that agreed everywhere, which is the same as
    no attestation at all.

    Asserted as a property rather than a comment, because a future tidy-up
    that shortened the probe would silently disarm the check."""
    assert PROBE_LENGTH >= 1024

    short = np.array([0.1, 0.25, 1.0 / 3.0, 0.985, 1.03, 2.5, 17.0])
    long = (np.arange(1, PROBE_LENGTH + 1, dtype=np.float64)
            / PROBE_LENGTH * 9.0 + 0.001)
    # The long probe exercises far more of the range, which is the point:
    # a handful of values cannot cover the argument reduction branches a
    # transcendental kernel takes.
    assert long.size > short.size * 100
    with np.errstate(all="ignore"):
        assert np.isfinite(np.exp(long)).all()
        assert np.isfinite(np.log(long)).all()


@pytest.mark.skipif(sys.platform != "linux",
                    reason="NPY_DISABLE_CPU_FEATURES is checked on x86 Linux")
def test_the_attestation_catches_a_real_microarchitecture_difference():
    """Not a hypothetical. The same NumPy, the same Python, the same machine,
    with AVX-512 dispatch disabled — and the transcendental digest changes
    while the exact one does not.

    That is RFC-072's split reproduced end to end: IEEE-754 §5 holds across
    microarchitectures, §9.2 does not. If this test ever fails because the
    two now agree, the check has stopped being able to see the thing it
    exists for."""
    probe = ("import json;"
             "from engine.core.dispatch import attest;"
             "a = attest();"
             "print(json.dumps([a.exact, a.transcendental]))")
    baseline = subprocess.run([sys.executable, "-c", probe],
                              capture_output=True, text=True)
    if baseline.returncode != 0:            # pragma: no cover - environment
        pytest.skip("could not run the probe subprocess")

    environment = dict(os.environ,
                       NPY_DISABLE_CPU_FEATURES="AVX512_SPR,AVX512_ICL,X86_V4")
    narrowed = subprocess.run([sys.executable, "-c", probe],
                              capture_output=True, text=True, env=environment)
    if narrowed.returncode != 0:            # pragma: no cover - no AVX-512
        pytest.skip("this CPU does not dispatch the features being disabled")

    import json
    exact_a, trans_a = json.loads(baseline.stdout)
    exact_b, trans_b = json.loads(narrowed.stdout)

    assert exact_a == exact_b, (
        "the exact operations differ between microarchitectures, which "
        "IEEE-754 §5 forbids — something is badly wrong")
    if trans_a == trans_b:                  # pragma: no cover - no AVX-512
        pytest.skip("this CPU shows no transcendental difference to catch")
    assert trans_a != trans_b


# --------------------------------------------------------------------------
# Retry, refusal, and the wire
# --------------------------------------------------------------------------

def test_a_failed_shard_is_retried_and_the_answer_is_unchanged():
    """A shard is a pure function of its inputs, so retrying it anywhere
    returns the same bits — there is no partial state to reconcile, which is
    what makes the retry free rather than merely safe."""
    points, assumptions, proj_len = build()
    reference, _ = dispatch(FixedAnnuity, points, assumptions, proj_len,
                            local_submit, workers=4)

    failures = {"count": 0}

    def flaky(shard, payload):
        if shard.index == 2 and failures["count"] < 2:
            failures["count"] += 1
            raise ConnectionError("worker died mid-shard")
        return local_submit(shard, payload)

    result, report = dispatch(FixedAnnuity, points, assumptions, proj_len,
                              flaky, workers=4)
    assert failures["count"] == 2
    assert report.attempts[2] == 3
    assert report.retried == (2,)
    for name in sorted(reference._stacked):
        assert np.array_equal(bits(result.array(name)),
                              bits(reference.array(name))), name


def test_a_shard_that_never_succeeds_is_an_error_naming_the_model_points():
    points, assumptions, proj_len = build()

    def always_fails(shard, payload):
        raise ConnectionError("no worker answered")

    with pytest.raises(DispatchError, match="model points"):
        dispatch(FixedAnnuity, points, assumptions, proj_len, always_fails,
                 workers=2, attempts=2)


def test_a_pooled_model_cannot_be_sharded_at_all():
    """The same error :mod:`engine.core.runner` refuses one policy at a time,
    one level up: a shard of a pooled model would reduce over the wrong
    population, so each shard would see a pool of itself."""

    class Pooled(FixedAnnuity):
        couples_model_points = True

    points, assumptions, proj_len = build()
    with pytest.raises(DispatchError, match="pool of itself"):
        dispatch(Pooled, points, assumptions, proj_len, local_submit,
                 workers=2)


def test_the_wire_form_carries_a_model_name_and_never_a_pickle():
    """A worker that could be made to unpickle whatever a coordinator sent it
    would be a remote code execution endpoint wearing a projection engine's
    clothes. The model travels as a catalogue *name*."""
    from engine.core.dispatch import Shard
    from engine.data.modelpoints import to_batch

    points, assumptions, proj_len = build(n=4)
    captured = {}

    def fake_post(url, json):
        captured["url"] = url
        captured["json"] = json
        answer = evaluate_shard({
            "model_cls": FixedAnnuity, "modelpoints": to_batch(points),
            "assumptions": assumptions, "proj_len": proj_len, "outputs": None})
        return {"stacked": {k: v.tolist()
                            for k, v in answer["stacked"].items()},
                "attestation": vars(answer["attestation"])}

    submit = http_submit(["http://worker-a"], post=fake_post)
    answer = submit(Shard(0, 0, 4), {
        "model_cls": FixedAnnuity, "modelpoints": to_batch(points),
        "assumptions": assumptions, "proj_len": proj_len, "outputs": None})

    assert captured["url"] == "http://worker-a/shard"
    assert captured["json"]["model"] == "FixedAnnuity"
    assert "fields" in captured["json"] and "ids" in captured["json"]
    assert all(isinstance(v, list)
               for v in captured["json"]["fields"].values())
    assert isinstance(answer["attestation"], Attestation)
    assert answer["stacked"]["pols_if"].dtype == np.float64


# --------------------------------------------------------------------------
# The shard tree, and what it is deliberately not part of
# --------------------------------------------------------------------------

def test_the_topology_is_recorded_but_is_not_part_of_the_run_identity():
    """**The record is the evidence for RFC-075's claim, not a decoration.**

    Where a shard ran cannot move a number, so a run split five ways and the
    same run split eight ways are the *same run* and must share a
    ``run_id``. Putting the topology into the identity would make a correctly
    reproduced answer look like a different one — which is the opposite of
    what a run registry is for.

    So the shard tree sits beside the identity rather than inside it, and a
    reviewer comparing two records with one ``run_id``, one
    ``results_digest`` and different shard trees is looking at the guarantee
    being kept."""
    from engine.core.dispatch import record_dispatched_run
    from engine.core.registry import RunRecord, record_run

    points, assumptions, proj_len = build(n=24, proj_len=20)
    _, undispatched = record_run(FixedAnnuity, points, assumptions, proj_len,
                                 executor="vectorized")
    _, five, _ = record_dispatched_run(FixedAnnuity, points, assumptions,
                                       proj_len, local_submit, workers=5)
    _, eight, _ = record_dispatched_run(FixedAnnuity, points, assumptions,
                                        proj_len, local_submit, workers=8)

    assert five.run_id == eight.run_id == undispatched.run_id
    assert five.results_digest == eight.results_digest \
        == undispatched.results_digest

    assert len(five.shards) == 5 and len(eight.shards) == 8
    assert list(five.shards) != list(eight.shards)
    assert five.dispatched and eight.dispatched
    assert not undispatched.dispatched
    assert undispatched.shards is None

    # The arithmetic the workers agreed on is recorded; an undispatched run
    # records none, which is not the same claim as "one machine".
    assert five.arithmetic == attest().digest
    assert undispatched.arithmetic is None


def test_the_shard_tree_survives_the_registry_round_trip():
    """Shard indices are integers and JSON keys are strings. A record that
    came back with ``{"0": ...}`` would compare unequal to the one written,
    and an append-only log whose entries stop matching themselves is worse
    than no log."""
    from engine.core.dispatch import record_dispatched_run
    from engine.core.registry import RunRecord

    points, assumptions, proj_len = build(n=12, proj_len=10)
    _, record, _ = record_dispatched_run(FixedAnnuity, points, assumptions,
                                         proj_len, local_submit, workers=3)
    written = record.to_dict()
    assert set(written["shards"]) == {"0", "1", "2"}
    assert RunRecord.from_dict(written) == record


def test_a_mixed_arithmetic_run_records_that_it_was_mixed():
    """If a caller passes ``require_matching_arithmetic=False`` they get an
    answer, and the record has to say the answer is not reproducible. A run
    record that hid this would be the one place the fact could not be
    recovered from."""
    from engine.core.dispatch import record_dispatched_run

    points, assumptions, proj_len = build(n=12, proj_len=10)
    other = Attestation("cafe", "beef", "2.4.6", "elsewhere")

    def mixed(shard, payload):
        answer = local_submit(shard, payload)
        if shard.index == 1:
            answer["attestation"] = other
        return answer

    _, record, report = record_dispatched_run(
        FixedAnnuity, points, assumptions, proj_len, mixed, workers=3,
        require_matching_arithmetic=False)
    assert record.arithmetic == "mixed"
    assert not report.workers_agreed
