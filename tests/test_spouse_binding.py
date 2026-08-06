"""RFC-070 — the spouse fields, and the dtype underneath them.

Three templates on the valuation-basis chassis carry a survivor benefit, and
each built its spouse curves by zipping model-point fields **directly**:

    zip(self.mp.spouse_dob, self.mp.dob, joint)

Over a :class:`~engine.data.modelpoints.ModelPointBatch` those are object
arrays and it works. Bound to a lone :class:`~engine.data.modelpoints.ModelPoint`
they are a bare ``date`` and a bare ``str``, and the zip raises. Dates were
incidental — ``self.mp.sex`` breaks on the same line for the same reason.

It hid because it is **conditional**: the branch is only entered when
``np.any(joint > 0)``, so a model point with no survivor benefit never
reaches it. `PayoutAnnuity`'s A1 and A2 ran under the interpreted executor
for as long as the template has existed; only A3, with a 60% reversion,
failed. That is the reason this is tested per model point below rather than
per template — a loop over templates that happened to take the first one
would still be green today and would still be wrong.

Removing it exposed a second failure underneath, which is why this module
covers two things rather than one. ``engine/core/vector.py`` coerces every
value to ``float64`` on the way into its slab; the interpreted executor kept
whatever the formula returned, so ``PayoutAnnuity.age`` came back ``int64``.
Equal numbers, different dtype, different ``results_digest`` — RFC-069's
failure mode one layer down, and invisible until the exception above was
removed.

Both are held to the same standard: the values were always equal, and it is
the *contract* that was not. So shape, dtype and value are asserted
separately, because a test that only compares values would have passed
throughout and told nobody anything.
"""

import datetime as dt

import numpy as np
import pytest

from engine.core.registry import record_run
from engine.core.runner import run as run_interpreted
from engine.core.vector import run_vectorized
from engine.data.modelpoints import ModelPoint, per_policy_field

pytest.importorskip("fastapi", reason="the worked examples need the [api] extra")

from engine.api.catalogue import build_run, catalogue  # noqa: E402
from engine.api.examples import EXAMPLES  # noqa: E402

#: The templates that carry a survivor benefit, and therefore the branch.
#: `LongevitySwap` is pooled, so only its block-of-one bridge is reachable
#: under the interpreted executor — which is exactly the case that was
#: broken, and exactly the case RFC-061 says must hold.
SPOUSE_TEMPLATES = ("PayoutAnnuity", "PensionBuyout", "LongevitySwap")


def _specimen(name):
    built = build_run(EXAMPLES[name]["request"], catalogue())
    return (built["model_cls"], list(built["modelpoints"]),
            built["assumptions"], built["proj_len"])


def _reversion(point):
    """The survivor percentage, whatever this chassis calls it."""
    for field in ("joint_percent", "spouse_percent"):
        if hasattr(point, field):
            return float(getattr(point, field))
    return 0.0


# --- the helper ------------------------------------------------------------


def test_a_field_read_off_one_model_point_gains_a_policy_axis():
    point = ModelPoint(id="A", dob=dt.date(1956, 1, 1), sex="M",
                       joint_percent=0.6)
    dob = per_policy_field(point, "dob", dtype=object)
    assert dob.shape == (1,) and dob[0] == dt.date(1956, 1, 1)
    sex = per_policy_field(point, "sex", dtype=object)
    assert sex.shape == (1,) and sex[0] == "M"
    assert per_policy_field(point, "joint_percent").tolist() == [0.6]


def test_a_field_read_off_a_batch_is_returned_untouched():
    """The property the whole fix rests on. If lifting the single-point case
    could move a batch, widening the equivalence class would mean revaluing
    every block in the library instead of proving something about it."""
    from engine.data.modelpoints import to_batch

    batch = to_batch([
        ModelPoint(id="A", dob=dt.date(1956, 1, 1), sex="M", pct=0.6),
        ModelPoint(id="B", dob=dt.date(1948, 3, 2), sex="F", pct=0.0),
    ])
    for name, dtype in (("dob", object), ("sex", object), ("pct", np.float64)):
        raw = getattr(batch, name)
        lifted = per_policy_field(batch, name, dtype=dtype)
        assert lifted.shape == raw.shape
        assert np.array_equal(lifted, raw)


def test_a_missing_required_field_is_refused_by_name():
    """Rather than an `AttributeError` from four frames inside `setup()`, or
    — worse — an object array of `None` that survives into a survival curve.
    `None` cannot be the sentinel: a model point may legitimately carry it,
    and omitting a default says something different from supplying one."""
    point = ModelPoint(id="A", dob=dt.date(1956, 1, 1))
    with pytest.raises(ValueError, match="spouse_dob"):
        per_policy_field(point, "spouse_dob", dtype=object)
    # A supplied default is honoured, including a falsy or None one.
    assert per_policy_field(point, "spouse_dob", None, dtype=object)[0] is None
    assert per_policy_field(point, "certain_years", 0.0).tolist() == [0.0]


def test_the_numeric_default_still_refuses_a_date():
    """The reason `dtype` is keyword-only with no safe default. A date sent
    down the numeric path raises from inside NumPy about a conversion,
    several frames from the template that asked — which is the failure this
    signature exists to make impossible to reach by habit."""
    point = ModelPoint(id="A", dob=dt.date(1956, 1, 1))
    with pytest.raises(TypeError):
        per_policy_field(point, "dob")


# --- the templates ---------------------------------------------------------


def _points_with_a_reversion():
    for name in SPOUSE_TEMPLATES:
        _, points, _, _ = _specimen(name)
        for i, point in enumerate(points):
            if _reversion(point) > 0.0:
                yield pytest.param(name, i, id=f"{name}-mp{i}")


@pytest.mark.parametrize("name,index", list(_points_with_a_reversion()))
def test_a_model_point_with_a_reversion_runs_one_policy_at_a_time(name, index):
    """The failure itself. Before RFC-070 every one of these raised
    ``TypeError: 'datetime.date' object is not iterable`` from inside
    ``setup()``.

    Parametrised over the model points that actually carry a reversion, and
    the list is built by *reading* the specimens rather than hard-coded — a
    specimen edited to drop its joint-life policy would collect nothing here,
    which is why the count is asserted separately below."""
    cls, points, assumptions, proj_len = _specimen(name)
    names = sorted(cls.var_names())
    interpreted = run_interpreted(cls, [points[index]], assumptions, proj_len,
                                  outputs=names)
    vectorized = run_vectorized(cls, [points[index]], assumptions, proj_len,
                                outputs=names)
    for var in names:
        got = np.array([mp[var] for mp in interpreted.per_mp]).T
        want = np.asarray(vectorized.array(var))
        assert got.shape == want.shape, var
        assert got.dtype == want.dtype, var
        assert np.array_equal(got, want), var


def test_the_specimens_still_carry_a_reversion_to_exercise():
    """Guards the parametrisation above against collecting nothing. Three
    templates, one joint-life model point each — and a specimen that lost
    its reversion would silently turn the test above into no test at all."""
    cases = list(_points_with_a_reversion())
    assert len(cases) == len(SPOUSE_TEMPLATES)


def test_the_branch_is_conditional_which_is_why_this_hid():
    """A model point with no reversion never enters the zip, so it ran
    correctly throughout. Asserted because it is the reason the bug survived
    every previous pass, and because a future refactor that made the branch
    unconditional would be a real change of behaviour rather than a
    tidy-up."""
    cls, points, assumptions, proj_len = _specimen("PayoutAnnuity")
    without = [p for p in points if _reversion(p) == 0.0]
    assert without, "the specimen must keep a single-life policy too"
    result = run_interpreted(cls, without[:1], assumptions, proj_len,
                             outputs=["payments"])
    assert np.isfinite(np.asarray(result.per_mp[0]["payments"])).all()


@pytest.mark.parametrize("name", ["PayoutAnnuity", "PensionBuyout"])
def test_the_whole_block_now_meets_the_per_policy_invariant(name):
    """What the fix is *for*: these two are in §1.2's per-policy class and
    can now be shown to be, over the whole specimen rather than a policy of
    it. `LongevitySwap` is excluded because it is pooled — RFC-061's block
    class — and owes the block-of-one bridge instead, which is the case
    parametrised above."""
    built = build_run(EXAMPLES[name]["request"], catalogue())
    built.pop("executor")
    interpreted = record_run(**built, executor="interpreted")[1]
    vectorized = record_run(**built, executor="vectorized")[1]
    assert interpreted.results_digest == vectorized.results_digest


# --- the dtype underneath --------------------------------------------------


def test_an_integer_valued_variable_is_a_float_in_both_executors():
    """`PayoutAnnuity.age` is an attained age — an integer by formula. The
    array executors store into a `float64` slab and coerce on the way in;
    the interpreted executor used to keep the `int64`. Equal values,
    different dtype, different digest.

    Asserted on dtype *and* value, separately: the values were never wrong,
    and a test comparing only values would have been green throughout the
    period the digests disagreed."""
    cls, points, assumptions, proj_len = _specimen("PayoutAnnuity")
    interpreted = run_interpreted(cls, points, assumptions, proj_len,
                                  outputs=["age"])
    vectorized = run_vectorized(cls, points, assumptions, proj_len,
                                outputs=["age"])
    got = np.array([mp["age"] for mp in interpreted.per_mp]).T
    want = np.asarray(vectorized.array("age"))
    assert got.dtype == np.float64 == want.dtype
    assert np.array_equal(got, want)
    # And the numbers really are whole ages, so the coercion is lossless
    # rather than a rounding that happens to agree.
    assert np.array_equal(got, np.round(got))


def test_every_interpreted_series_is_plain_floats():
    """The contract, stated over a template rather than a variable. A
    `numpy.int64` would compare equal to a float and fingerprint
    differently, which is the whole failure mode."""
    cls, points, assumptions, proj_len = _specimen("PayoutAnnuity")
    result = run_interpreted(cls, points, assumptions, proj_len)
    for per_mp in result.per_mp:
        for name, values in per_mp.items():
            assert all(type(v) is float for v in values), name


def test_the_evidence_pack_now_attests_the_whole_per_policy_class():
    """The claim this RFC and RFC-069 exist to make true. Every template the
    pack places *in* the per-policy class is bitwise-identical across both
    deterministic executors — no unexplained reds, and no template reported
    as failing an invariant it was actually keeping."""
    from engine.report.evidence import executor_equivalence

    section = executor_equivalence()
    rows = section.content["templates"]
    unattested = [r["template"] for r in rows
                  if r["in_equivalence_class"] and not r["bitwise_identical"]]
    assert unattested == []
    assert section.content["n_bitwise"] == section.content["n_in_class"]
    for name in ("PayoutAnnuity", "PensionBuyout"):
        row = next(r for r in rows if r["template"] == name)
        assert row["error"] is None
        assert row["in_equivalence_class"] is True
