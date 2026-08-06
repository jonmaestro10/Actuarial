"""RFC-069 — the slab read per policy, and the invariant that was only
unprovable.

A template that precomputes a ``(n_policies, n_periods)`` slab in ``setup()``
and reads it through :meth:`Model.at` used to get a ``(1,)`` array under the
interpreted executor — where the model is bound to a single model point and
every other variable is a scalar — because ``np.atleast_1d`` over scalar
fields manufactures a policy axis of length one. The numbers were identical
to the last bit; the *shape* was not, so ``record_run``'s digest assembly
compared ``(1, n_t, n_mp)`` with ``(n_t, n_mp)`` and the evidence pack
reported three templates as outside an invariant they had never breached.

The fix keys on the **binding**, never the shape: bound to a single model
point, ``at`` returns the column as the scalar it is; bound to a
:class:`~engine.data.modelpoints.ModelPointBatch`, the axis is the block and
stays. That distinction is what this suite pins, in both directions:

- the grant — a slab-reading template now digests identically under both
  executors, and the values agree elementwise, which is the statement that
  the disagreement was only ever about shape;
- the trap not taken — a **vectorized** block of one, and a chunk of one,
  also produce a ``(1,)`` slice, and there it is correct and must survive;
- the refusals — a per-policy model handed a block's slab is refused rather
  than silently read at its first row, and the pooled slab-reader still
  refuses the interpreted executor over a block, exactly as before.
"""

import numpy as np
import pytest

from engine.api.catalogue import build_run, catalogue
from engine.api.examples import EXAMPLES
from engine.core.registry import record_run
from engine.core.runner import PooledBlockError, run
from engine.core.vector import run_vectorized
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import ModelPoint, to_batch
from engine.library.general_insurance import GeneralInsurance
from engine.library.longevity_swap import LongevitySwap

BASIS = Assumptions(mortality=MortalityTable.flat(0.0), interest=0.03)

BASE = {
    "id": "G1", "written_premium": 1_000_000.0, "policy_term_years": 5,
    "expected_loss_ratio": 0.62, "cat_load_ratio": 0.05,
    "expense_ratio": 0.28, "init_pols": 1.0, "earning_pattern": "uniform",
}

#: The three templates RFC-068's last section named as one shape bug:
#: two that disagreed between the executors, and the pooled one whose
#: single-model-point bridge reported False for the same reason.
SLAB_READERS = ("GeneralInsurance", "LongTermCare")
POOLED_SLAB_READER = "LongevitySwap"


def gi_point(**overrides):
    return ModelPoint(**{**BASE, **overrides})


def specimen(name):
    """One worked example, built the way the evidence pack builds it."""
    built = build_run(EXAMPLES[name]["request"], catalogue())
    built.pop("executor", None)
    return built


# --------------------------------------------------------------------------
# The grant: same digest, and the values always were the same
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", SLAB_READERS)
def test_a_slab_reading_template_digests_identically_under_both_executors(name):
    """The failure mode, guarded at the level it surfaced: the evidence
    pack compares ``results_digest`` values, and the digest covers the shape
    as well as the numbers — rightly, which is why the fix had to make the
    shapes agree rather than teach the digest to forgive them."""
    built = specimen(name)
    _, interpreted = record_run(**built, executor="interpreted")
    _, vectorized = record_run(**built, executor="vectorized")
    assert interpreted.results_digest == vectorized.results_digest


@pytest.mark.parametrize("name", SLAB_READERS)
def test_the_invariant_was_never_breached_only_unprovable(name):
    """Shape agreement AND elementwise value agreement, asserted separately.

    The point of the second assertion is historical: before RFC-069 the
    stacked interpreted result was ``(1, n_t, n_mp)`` against the vectorized
    ``(n_t, n_mp)`` with every number identical. Values equal and shapes
    equal together are the statement that §1.2 was never broken here — the
    pack just could not say so."""
    built = specimen(name)
    interpreted, _ = record_run(**built, executor="interpreted")
    vectorized, _ = record_run(**built, executor="vectorized")
    for var in built["outputs"]:
        stacked = np.array([mp[var] for mp in interpreted.per_mp]).T
        expected = np.asarray(vectorized.array(var))
        assert stacked.shape == expected.shape, var
        assert np.array_equal(stacked, expected), var


def test_the_pooled_slab_readers_single_point_bridge_holds():
    """RFC-061's bridge, restored for the pooled slab-reader: on a block of
    one, the reduction is the same either way, so the two executors must
    agree bitwise — and before RFC-069 they agreed on every number and
    differed in shape, which reported the bridge as broken."""
    built = specimen(POOLED_SLAB_READER)
    one = {**built, "modelpoints": list(built["modelpoints"])[:1]}
    digests = {
        executor: record_run(**one, executor=executor)[1].results_digest
        for executor in ("interpreted", "vectorized")
    }
    assert len(set(digests.values())) == 1


def test_bound_to_one_model_point_a_slab_column_is_a_scalar():
    """The mechanism itself, at the member that carries it. Bound to a
    single model point every model-point field is a scalar, so a variable
    reading a ``setup()`` slab must be one too — otherwise it and its
    dependants are the only array-valued variables in a per-policy
    projection, and everything downstream that sums them (``aggregate``,
    ``combined_ratio``) quietly returns arrays."""
    model = GeneralInsurance(gi_point(), BASIS, proj_len=6)
    assert np.ndim(model.earned_fraction(1)) == 0
    assert np.ndim(model.unearned_premium(3)) == 0
    assert np.ndim(model.combined_ratio()) == 0


# --------------------------------------------------------------------------
# The trap not taken: a block of one is not a single binding
# --------------------------------------------------------------------------

def test_a_vectorized_block_of_one_keeps_its_axis():
    """The obvious fix — squeeze when the leading axis is length one — is
    wrong, because a vectorized block of one produces the same ``(1,)``
    slice and there it is correct: the axis is the block. The discriminator
    is the binding, and a ``ModelPointBatch`` of one is still a batch."""
    batch = to_batch([gi_point()])
    model = GeneralInsurance(batch, BASIS, proj_len=6)
    value = model.earned_fraction(1)
    assert value.shape == (1,)


def test_a_chunk_of_one_still_digests_like_the_unchunked_run():
    """``chunk_size=1`` hands the model a sequence of one-policy batches —
    each producing the ``(1,)`` slice a naive squeeze would eat. Bitwise
    equality with the unchunked run is the standing guarantee (§1.2, and
    the registry's reason for leaving chunk_size out of ``run_id``)."""
    points = [gi_point(id="G1"),
              gi_point(id="G2", policy_term_years=3, earning_pattern="front")]
    outputs = ["premium_earned", "unearned_premium", "underwriting_result"]
    whole, one_by_one = (
        record_run(GeneralInsurance, points, BASIS, 6, outputs=outputs,
                   executor="vectorized", chunk_size=size)[1]
        for size in (None, 1)
    )
    assert whole.results_digest == one_by_one.results_digest


# --------------------------------------------------------------------------
# The refusals
# --------------------------------------------------------------------------

def test_a_per_policy_model_refuses_a_blocks_slab():
    """Bound to a single model point, a slab carrying more than one policy
    is a slab built for a block this model is not bound to. Reading its
    first row would be the RFC-061 shape of failure — plausible numbers,
    wrong population — so it is refused with the population named."""
    model = GeneralInsurance(gi_point(), BASIS, proj_len=6)
    block_slab = np.ones((3, 7))
    with pytest.raises(ValueError, match="3 policies"):
        model.at(block_slab, 2)


def test_the_pooled_slab_reader_still_refuses_the_interpreted_block():
    """The fix must not widen what the interpreted executor accepts: a
    pooled template over a block of many still reduces across the block,
    and one policy at a time still cannot do that (RFC-061)."""
    built = specimen(POOLED_SLAB_READER)
    points = list(built["modelpoints"])
    assert len(points) > 1
    with pytest.raises(PooledBlockError):
        run(LongevitySwap, points, built["assumptions"], built["proj_len"])


# --------------------------------------------------------------------------
# What the evidence pack now says
# --------------------------------------------------------------------------

def test_the_pack_attests_the_three_templates_it_could_not():
    """RFC-068's last section: three of the five unattested templates were
    one shape bug. This runs the pack's own attestation over exactly those
    three specimens and holds it to the verdicts the fix makes true —
    including the executors each row actually ran, so the claim cannot
    quietly narrow."""
    from engine.report.evidence import default_specimens, executor_equivalence

    wanted = set(SLAB_READERS) | {POOLED_SLAB_READER}
    specimens = [s for s in default_specimens() if s["name"] in wanted]
    assert {s["name"] for s in specimens} == wanted
    section = executor_equivalence(specimens)
    rows = {row["template"]: row for row in section.content["templates"]}
    for name in SLAB_READERS:
        assert rows[name]["error"] is None
        assert rows[name]["in_equivalence_class"] is True
        assert rows[name]["bitwise_identical"] is True
        assert set(rows[name]["digests"]) == {"interpreted", "vectorized"}
    swap = rows[POOLED_SLAB_READER]
    assert swap["error"] is None
    assert swap["in_equivalence_class"] is False       # pooled, and rightly
    assert swap["repeats_deterministically"] is True
    assert swap["bitwise_on_one_modelpoint"] is True   # the restored bridge
