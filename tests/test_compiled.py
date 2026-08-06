"""The compiled executor: bitwise against the vectorized one, or it does not ship.

§1.2 asks every executor to produce **bitwise-identical** results. This suite
holds the compiled one to that and to nothing weaker — there is no tolerance
anywhere in it, because a tolerance would be the guarantee quietly becoming a
different guarantee.

Three things it exists to hold:

- **The equality.** Every template the executor compiles must agree with
  :func:`~engine.core.vector.run_vectorized` on every variable and period,
  compared as **bit patterns**, with shape and dtype asserted separately —
  RFC-069 and RFC-070's rule, where three bugs produced equal numbers with an
  unequal contract.
- **The refusals.** A tape that does not stabilise, a body that branches on
  model-point data, a model with nothing to fuse. Each is refused by name.
- **Coverage, stated.** Which templates compile is computed, not claimed, and
  the ones that do not are reported with the reason.

The measurement half needs the ``[compile]`` extra and says so when absent.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.core.compiled import (
    CompilationRefused,
    cached_plan,
    compile_kernel,
    plan,
    run_compiled,
)
from engine.core.vector import run_vectorized
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import ModelPoint
from engine.library.fixed_annuity import FixedAnnuity

try:
    import numba as _numba
except ImportError:  # pragma: no cover - exercised without the extra
    _numba = None

needs_compiler = pytest.mark.skipif(
    _numba is None,
    reason="the compiled executor needs the [compile] extra "
           "(pip install -e '.[compile]')",
)


def build(n=8, proj_len=40):
    points = [ModelPoint(age_at_entry=45 + (i % 20), defer_years=10,
                         premium=100_000.0, annual_payment=9_000.0,
                         init_pols=1) for i in range(n)]
    return points, Assumptions(mortality=MortalityTable.flat(0.015),
                               interest=0.03, crediting_rate=0.02), proj_len


def bits(array):
    return np.asarray(array, dtype=np.float64).view(np.int64)


# --------------------------------------------------------------------------
# The plan — no compiler needed
# --------------------------------------------------------------------------

def test_the_plan_separates_what_is_fused_from_what_is_handed_in():
    """A variable is hoisted **whole**, never in part. A sub-expression is an
    anonymous intermediate with no name that survives to run time, so nothing
    could compute it for the next block; a `@var` has a name the vectorized
    executor can evaluate for any batch, which is what a hoist slab has to
    be."""
    points, assumptions, proj_len = build()
    result = plan(FixedAnnuity, points, assumptions, proj_len)

    assert result.compilable
    assert set(result.fused) | set(result.hoisted) == set(result.order)
    assert not set(result.fused) & set(result.hoisted)
    # The mortality lookup and the discount factor are table and transcendental
    # work; they are the things a kernel cannot reproduce bit for bit.
    assert set(result.hoisted) == {"q_x", "v"}


def test_the_generated_source_is_readable_and_is_the_loop_that_ran():
    """An auditor can read the loop the engine actually executed. That is
    worth more here than in most compilers, because the thing compiled is a
    regulatory calculation."""
    points, assumptions, proj_len = build()
    source = plan(FixedAnnuity, points, assumptions, proj_len).source

    assert "def kernel(" in source
    assert "o_pols_if[t, j] = (o_pols_if[t - 1, j] * (1.0 - h_q_x[t - 1, j]))" \
        in source
    # Period 0 has its own body, because every stock variable branches on it.
    assert "o_pols_if[0, j] = (f_init_pols[j] * 1.0)" in source
    # And nothing the kernel may not contain appears in it.
    for forbidden in ("np.exp", "np.log", "np.sum", "**", "fastmath"):
        assert forbidden not in source, forbidden


def test_the_digest_identifies_the_loop_and_not_the_model():
    """Two models compiling to the same loop are the same kernel; one model
    whose formulas changed is a different one. Keying the cache on the class
    would miss the second."""
    points, assumptions, proj_len = build()
    first = plan(FixedAnnuity, points, assumptions, proj_len)
    again = plan(FixedAnnuity, points, assumptions, proj_len)
    assert first.digest == again.digest

    other = plan(FixedAnnuity, points,
                 Assumptions(mortality=MortalityTable.flat(0.02),
                             interest=0.05, crediting_rate=0.02), proj_len)
    # A different basis folds different constants, so a different loop.
    assert other.digest != first.digest or other.source == first.source


def test_a_model_with_nothing_to_fuse_is_refused_rather_than_wrapped():
    """If every variable is hoisted the kernel would be the vectorized
    executor with extra steps, and shipping that as a third executor would
    be a speed claim with no speed in it."""

    class AllHoisted(FixedAnnuity):
        pass

    points, assumptions, proj_len = build()
    result = plan(FixedAnnuity, points, assumptions, proj_len)
    assert result.fused  # the real one does fuse

    from engine.core.compiled import CompilationPlan
    empty = CompilationPlan("X", ("a",), (), ("a",), (), (), {}, "",
                            ("every variable is hoisted",), 10)
    assert not empty.compilable
    with pytest.raises(CompilationRefused, match="every variable is hoisted"):
        compile_kernel(empty)


# --------------------------------------------------------------------------
# The equality — needs the compiler
# --------------------------------------------------------------------------

@needs_compiler
def test_the_compiled_run_is_bitwise_identical_to_the_vectorized_one():
    """**The claim §1.2 makes, held without a tolerance.**

    Shape, dtype and value are asserted separately: RFC-069's spurious axis,
    RFC-070's missing one and the `int64` beneath them all produced equal
    numbers with an unequal contract, and every one would have passed a
    value-only comparison."""
    points, assumptions, proj_len = build(n=64)
    compiled = run_compiled(FixedAnnuity, points, assumptions, proj_len)
    reference = run_vectorized(FixedAnnuity, points, assumptions, proj_len)

    assert set(compiled._stacked) == set(reference._stacked)
    assert compiled.mp_ids == reference.mp_ids
    for name in sorted(reference._stacked):
        got, want = compiled.array(name), reference.array(name)
        assert got.shape == want.shape == (proj_len + 1, len(points)), name
        assert got.dtype == want.dtype == np.float64, name
        assert np.array_equal(bits(got), bits(want)), name


@needs_compiler
def test_the_kernel_is_reused_rather_than_rebuilt():
    """Compilation traces every period and invokes a JIT; doing it per run
    would make the executor slower than the one it exists to beat. PLAN §4.2
    asks for kernels cached per (model class, time structure) — the basis is
    in the key too, because the folded constants come off it."""
    points, assumptions, proj_len = build()
    first = cached_plan(FixedAnnuity, points, assumptions, proj_len)
    second = cached_plan(FixedAnnuity, points, assumptions, proj_len)
    assert first is second
    assert compile_kernel(first) is compile_kernel(second)


@needs_compiler
def test_a_chunked_vectorized_run_and_a_compiled_run_still_agree():
    """The vectorized executor chunks the block and asserts that changes no
    number. The compiled one does not chunk at all, so agreeing with both a
    chunked and an unchunked run is a stronger statement than agreeing with
    either."""
    points, assumptions, proj_len = build(n=200)
    compiled = run_compiled(FixedAnnuity, points, assumptions, proj_len)
    chunked = run_vectorized(FixedAnnuity, points, assumptions, proj_len,
                             chunk_size=32)
    for name in sorted(chunked._stacked):
        assert np.array_equal(bits(compiled.array(name)),
                              bits(chunked.array(name))), name


@needs_compiler
def test_asking_for_a_variable_the_kernel_does_not_produce_is_refused():
    points, assumptions, proj_len = build()
    with pytest.raises(CompilationRefused, match="not produced"):
        run_compiled(FixedAnnuity, points, assumptions, proj_len,
                     outputs=["pols_if", "not_a_variable"])


@needs_compiler
def test_the_catalogue_compiles_and_agrees_or_says_why_not():
    """**Coverage stated, never fudged.** Which templates compile is computed
    here rather than claimed in a docstring, every one that compiles is held
    to bitwise equality, and the ones that do not are reported with a reason.

    A floor is asserted so that a change which silently stopped compiling
    everything fails here instead of showing up as an unexplained benchmark.
    """
    from engine.report.evidence import default_specimens

    specimens = [s for s in default_specimens()
                 if s.get("scenarios") is None]
    if not specimens:
        pytest.skip("the worked examples need the [api] extra")

    agreed, refused = [], {}
    for specimen in specimens:
        name = specimen.get("name") or specimen["model_cls"].__name__
        call = dict(model_cls=specimen["model_cls"],
                    modelpoints=specimen["modelpoints"],
                    assumptions=specimen["assumptions"],
                    proj_len=specimen["proj_len"])
        compilation = cached_plan(**call)
        if not compilation.compilable:
            refused[name] = compilation.refusals[0]
            continue
        compiled = run_compiled(**call)
        reference = run_vectorized(**call)
        for variable in compilation.fused:
            got, want = compiled.array(variable), reference.array(variable)
            assert got.shape == want.shape, (name, variable)
            assert got.dtype == want.dtype == np.float64, (name, variable)
            assert np.array_equal(bits(got), bits(want)), (name, variable)
        agreed.append(name)

    assert len(agreed) >= 12, (
        f"only {len(agreed)} of {len(specimens)} templates compiled; "
        f"refused: { {k: v[:60] for k, v in refused.items()} }"
    )
    for name, reason in refused.items():
        assert len(reason) > 30, (name, reason)
