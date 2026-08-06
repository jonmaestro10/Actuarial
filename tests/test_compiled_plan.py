"""B1's first brick: what a kernel would contain, worked out per model.

`engine/core/graph.py` traces the `@var` graph and sorts it, and says
plainly that it "does **not** fuse anything". `engine/core/compiled.py` is
the step between that and a kernel: it classifies every operation a model
actually performs into the ones a kernel may contain, the ones NumPy must
precompute, and the ones that make the model uncompilable — with the
operation named.

It emits a **plan and not a kernel**, and that boundary is the point. A plan
can be checked against the model it describes; a kernel can only be checked
against its own output. Getting the plan right first gives the kernel
something to be wrong against.
"""

from __future__ import annotations

import pytest

from engine.core.bitwise import classify
from engine.core.compiled import CompilationPlan, plan
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import ModelPoint
from engine.library.fixed_annuity import FixedAnnuity


def build():
    points = [ModelPoint(age_at_entry=50 + i, defer_years=10,
                         premium=100_000.0, annual_payment=9_000.0,
                         init_pols=1) for i in range(4)]
    return points, Assumptions(mortality=MortalityTable.flat(0.015),
                               interest=0.03, crediting_rate=0.02)


def test_a_plan_says_what_the_kernel_would_fuse_and_what_it_would_be_handed():
    """The shape of the answer. A model is not simply compilable or not: it
    has a body of exact arithmetic that fuses, and a set of values that have
    to arrive precomputed. Reporting only the verdict would leave the emitter
    with nothing to build from."""
    points, assumptions = build()
    result = plan(FixedAnnuity, points, assumptions, 40)

    assert isinstance(result, CompilationPlan)
    assert result.model == "FixedAnnuity"
    assert result.compilable
    assert result.refusals == ()

    # Every variable the model declares appears in the topological order.
    assert set(result.order) == set(FixedAnnuity.var_names())
    # And the order is a real one: a variable never precedes what it reads.
    assert result.order.index("q_x") < result.order.index("death_benefits")

    assert result.exact_op_count > 0
    for name, ops in result.exact_ops.items():
        for op in ops:
            assert classify(op) in ("exact", "structural"), (name, op)

    # The hoists are the table gathers — a mortality lookup leaves the traced
    # world and comes back as ordinary data.
    assert result.hoists
    assert {h.reason for h in result.hoists} <= {"operation", "gather"}


def test_the_transcendentals_land_outside_the_kernel_not_inside_it():
    """RFC-072's rule, applied. Anything IEEE-754 declines to pin is a value
    NumPy computes and the kernel reads — never an instruction in the loop
    body. Asserted by classification rather than by inspecting a kernel,
    because there is no kernel yet and this is the contract one would have
    to meet."""
    points, assumptions = build()
    result = plan(FixedAnnuity, points, assumptions, 40)

    fused = {op for ops in result.exact_ops.values() for op in ops}
    assert not any(classify(op) == "hoist" for op in fused)
    assert not any(classify(op) == "reduce" for op in fused)


def test_a_reduction_makes_a_model_uncompilable_and_says_which_variable():
    """A `@pool` body reduces across the model-point axis, and a reduction's
    answer depends on association order with no safe length (RFC-072). So a
    pooled model is refused — by arithmetic rather than by policy — and the
    refusal names the variable, because "not compilable" without it leaves
    the caller to guess."""
    from engine.core.compiled import _Tape

    tape = _Tape(variable="adjustment")
    tape.record("sum")
    assert tape.refusals
    assert "adjustment" in tape.refusals[0]
    assert "association order" in tape.refusals[0]
    assert "no length" in tape.refusals[0]


def test_an_unclassified_operation_is_refused_with_what_to_do_about_it():
    """Routing an unknown op to the safe side would silence the question:
    the kernel would quietly stop fusing, the benchmark would quietly
    regress, and nobody would learn a new operation had appeared."""
    from engine.core.compiled import _Tape

    tape = _Tape(variable="claims")
    tape.record("erf")
    assert tape.refusals and "erf" in tape.refusals[0]
    assert "engine/core/bitwise.py" in tape.refusals[0]


def test_a_branch_on_traced_data_is_refused_rather_than_specialised():
    """**RFC-070's bug, made unrepeatable.** A `@var` body must not branch on
    model-point data. If one does, the recorded tape is specialised to *this
    batch's values*, and a kernel built from it is right for the trace and
    wrong for the next block — which is exactly how a conditional branch that
    one batch never entered survived three RFCs."""
    from engine.core.compiled import _Tape, _Traced
    import numpy as np

    tape = _Tape(variable="joint_benefit")
    traced = _Traced(np.array([1.0]), tape)
    assert bool(traced) is True              # the value is real, and taken
    assert tape.refusals

    # A multi-element array raises on the way through, as NumPy's does — the
    # refusal is still recorded, because it is recorded before the attempt.
    wider = _Tape(variable="joint_benefit")
    with pytest.raises(ValueError, match="truth value"):
        bool(_Traced(np.array([1.0, 0.0]), wider))
    assert wider.refusals
    assert "specialised to this batch" in tape.refusals[0]
    assert "RFC-070" in tape.refusals[0]


def test_the_tracer_does_not_change_what_the_model_computes():
    """The tracer carries real values precisely so the model takes the
    branches it normally would. If tracing changed a number, the plan would
    describe a model nobody runs."""
    from engine.core.vector import run_vectorized

    points, assumptions = build()
    reference = run_vectorized(FixedAnnuity, points, assumptions, 10,
                               outputs=["pols_if"])
    plan(FixedAnnuity, points, assumptions, 10)
    again = run_vectorized(FixedAnnuity, points, assumptions, 10,
                           outputs=["pols_if"])
    import numpy as np
    assert np.array_equal(reference.array("pols_if").view(np.int64),
                          again.array("pols_if").view(np.int64))


def test_the_catalogue_is_mostly_compilable_and_the_exceptions_are_named():
    """**Coverage, stated rather than implied.** The point of the plan is to
    know before writing an emitter how much of the library it could serve.

    Every deterministic specimen is planned. The verdict is asserted as a
    partition with a floor, so a change that silently made everything
    uncompilable — or that emptied the set being iterated — fails here rather
    than showing up as an unexplained benchmark."""
    from engine.report.evidence import default_specimens

    specimens = [s for s in default_specimens() if s.get("scenarios") is None]
    if not specimens:
        pytest.skip("the worked examples need the [api] extra")

    verdicts = {}
    for specimen in specimens:
        name = specimen.get("name") or specimen["model_cls"].__name__
        verdicts[name] = plan(
            specimen["model_cls"], specimen["modelpoints"],
            specimen["assumptions"], specimen["proj_len"])

    compilable = {n for n, p in verdicts.items() if p.compilable}
    refused = {n: p.refusals for n, p in verdicts.items() if not p.compilable}

    assert len(verdicts) >= 10, "the specimen set has shrunk unexpectedly"
    assert len(compilable) >= 12, (
        f"only {len(compilable)} of {len(verdicts)} templates plan cleanly; "
        f"refusals: { {n: r[0][:70] for n, r in refused.items()} }"
    )
    # Every refusal names an operation and a variable, so it is actionable.
    for name, reasons in refused.items():
        assert reasons and any(len(r) > 40 for r in reasons), name

    # And the plans are not empty: a template that fused nothing would be
    # "compilable" in the vacuous sense.
    for name, result in verdicts.items():
        assert result.exact_op_count > 0, name
        assert result.describe().startswith(result.model)
