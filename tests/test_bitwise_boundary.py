"""The boundary a compiled kernel must respect, measured rather than assumed.

§1.2 asks for bitwise equality across executors. B1 proposes a compiled
third executor, and whether that is even possible turns on one question:
does compiled code return the same bits NumPy does?

This module answers it by running both. Two halves, and the second is the
one that matters:

- The **structural** tests need no compiler and always run. They assert that
  ``engine/core/bitwise.py``'s three categories are disjoint, that an
  operation nobody has classified is refused rather than assumed safe, and
  that the refusal names the operation responsible.
- The **measurement** needs the ``[compile]`` extra. It asserts that every
  operation IEEE-754 requires to be correctly rounded really is reproduced
  bit for bit, and — the other direction, which is what keeps the module
  from being paranoia — that the operations it declines to trust really do
  differ.

Both directions are asserted because both can rot. If a future NumPy or
compiler release made ``exp`` agree, ``test_the_ops_it_declines_to_trust_really_do_differ``
fails, and the right response is to look again rather than to keep hoisting
an operation that no longer needs it. If one made ``multiply`` disagree, the
first test fails and B1 is off, which is a thing to find out from a test
rather than from a reserve.
"""

from __future__ import annotations

import os

import numpy as np
import pytest

from engine.core.bitwise import (
    CORRECTLY_ROUNDED,
    IMPLEMENTATION_DEFINED,
    MEASURED_AGAINST,
    ORDER_DEPENDENT,
    UNSAFE_FLAGS,
    classify,
    compilable,
)

try:
    import numba as _numba
except ImportError:  # pragma: no cover - exercised without the extra
    _numba = None

needs_compiler = pytest.mark.skipif(
    _numba is None,
    reason="the [compile] extra is not installed, so the bitwise boundary "
           "is unmeasured here; pip install -e '.[compile]' to run it",
)


def test_the_measurement_is_not_allowed_to_skip_where_it_is_required():
    """**A skipped measurement reads exactly like a passing one.**

    39 of this module's cases need a compiler, and a developer without the
    ``[compile]`` extra should not be made to install it. CI is the opposite
    case: the job in ``.github/workflows/ci.yml`` exists *only* to run them,
    so an install step that half-succeeded would leave every case skipped and
    the summary line green — the claim unmeasured and nothing saying so.

    ``REQUIRE_COMPILE_EXTRA`` is what tells the two apart. It is set by that
    job and by nothing else, and it turns the skip into a failure.
    """
    required = os.environ.get("REQUIRE_COMPILE_EXTRA", "") not in ("", "0")
    if required and _numba is None:
        pytest.fail(
            "REQUIRE_COMPILE_EXTRA is set but numba is not importable, so "
            "the bitwise boundary would be reported as passing without "
            "having been measured. Check that the [compile] extra installed."
        )
    if not required:
        pytest.skip("not the CI job that requires the compiler")


@needs_compiler
def test_the_measurement_says_which_libraries_it_measured():
    """**The evidence has a date, and a green run must not hide it.**

    Numba pins ``numpy<2.5``. The main test matrix installs no such ceiling,
    so once NumPy moves past it the two CI jobs resolve *different* NumPys —
    and this job would go on passing while measuring a library the suite no
    longer runs on. Measured, green, and about the wrong thing, which is the
    failure this repo keeps finding in provenance strings.

    The classification itself is version-independent by construction, so a
    mismatch is not a defect: :data:`CORRECTLY_ROUNDED` is guaranteed by
    IEEE-754 and :data:`IMPLEMENTATION_DEFINED` is the set already declined.
    What goes stale is the *evidence*, and the right response to this failing
    is to re-run the measurement and update
    :data:`~engine.core.bitwise.MEASURED_AGAINST` — not to widen either set.
    """
    def minor(version: str) -> str:
        return ".".join(version.split(".")[:2])

    assert minor(np.__version__) == MEASURED_AGAINST["numpy"], (
        f"the boundary was measured against NumPy {MEASURED_AGAINST['numpy']} "
        f"and this is {np.__version__}. Re-run the measurement and update "
        f"MEASURED_AGAINST; if the transcendentals now agree, that is a "
        f"coincidence of two versions and still not a guarantee."
    )
    assert minor(_numba.__version__) == MEASURED_AGAINST["numba"], (
        f"the boundary was measured against Numba "
        f"{MEASURED_AGAINST['numba']} and this is {_numba.__version__}"
    )


def _bits(x):
    return np.asarray(x, dtype=np.float64).view(np.int64)


def sample(kind: str = "ordinary"):
    """Inputs to measure over.

    ``ordinary`` is the range an actuarial projection actually holds:
    finite, positive, no signed zero, no subnormals. ``adversarial`` adds
    the values where implementations are most likely to part company. Both
    are measured, because "agrees on ordinary data" and "agrees" are
    different claims and the module makes the second one.
    """
    rng = np.random.default_rng(20260806)
    if kind == "ordinary":
        return rng.uniform(1e-6, 1e6, 50_000), rng.uniform(0.5, 2.5, 50_000)
    a = np.concatenate([
        rng.uniform(-1e3, 1e3, 20_000),
        np.ldexp(rng.uniform(1, 2, 20_000), rng.integers(-1020, 1020, 20_000)),
        np.array([0.0, -0.0, 1.0, 0.5, 2.0 ** -1074, np.pi] * 1_000),
    ])
    return a, rng.uniform(0.5, 2.5, a.size)


# --------------------------------------------------------------------------
# Structural — no compiler needed, so these run everywhere
# --------------------------------------------------------------------------

def test_the_three_categories_do_not_overlap():
    """An operation is exactly one of: guaranteed by the standard,
    implementation-defined, or a reduction. An op in two categories would
    make :func:`classify` order-dependent, and the caller would get whichever
    answer the ``if`` chain reached first."""
    assert not CORRECTLY_ROUNDED & IMPLEMENTATION_DEFINED
    assert not CORRECTLY_ROUNDED & ORDER_DEPENDENT
    assert not IMPLEMENTATION_DEFINED & ORDER_DEPENDENT


def test_an_unclassified_op_is_refused_rather_than_assumed_safe():
    """**The refusal that keeps this honest.** An operation nobody has
    classified is a question. Routing it to the safe side would silence the
    question — the kernel would quietly stop fusing and nothing would say
    why — and routing it to the fast side would be a wrong number. So it is
    neither: it is refused, by name, with what to do about it."""
    assert classify("multiply") == "exact"
    assert classify("exp") == "hoist"
    assert classify("sum") == "reduce"
    assert classify("erf") == "unknown"

    ok, reasons = compilable(["multiply", "erf"])
    assert not ok
    assert len(reasons) == 1 and reasons[0].startswith("erf: not classified")
    assert "engine/core/bitwise.py" in reasons[0]


def test_the_refusal_names_the_operation_and_the_reason():
    """A verdict of "not compilable" without the operation would leave the
    caller to guess which of forty variables was responsible."""
    ok, reasons = compilable(["add", "multiply", "exp", "sum", "subtract"])
    assert not ok
    joined = " ".join(reasons)
    assert "exp:" in joined and "§9.2" in joined
    assert "sum:" in joined and "association order" in joined
    # The operations that are fine are not mentioned at all.
    assert "add:" not in joined and "multiply:" not in joined

    ok, reasons = compilable(["add", "multiply", "subtract", "where"])
    assert ok and reasons == []


def test_a_kernel_may_not_be_built_with_a_flag_that_licenses_reassociation():
    """``fastmath`` is precisely the permission to turn ``a * b + c`` into a
    fused multiply-add — which is *more* accurate, and a different number.
    Recorded as a named list so B1's kernel builder has something to assert
    against rather than a sentence in a docstring to remember."""
    assert "fastmath" in UNSAFE_FLAGS
    assert "no_signed_zeros" in UNSAFE_FLAGS


def test_every_op_the_pooled_templates_reduce_with_is_a_reduction():
    """``pool_sum`` is the library's one reduction, and it is what puts every
    ``@pool`` body outside a kernel. Asserted here rather than assumed,
    because "the pooled templates are the ones that reduce" is the sort of
    claim that stays in a docstring after it stops being true."""
    assert classify("sum") == "reduce"
    for op in ("mean", "cumsum", "prod", "dot"):
        assert classify(op) == "reduce", op


# --------------------------------------------------------------------------
# The measurement — needs the [compile] extra
# --------------------------------------------------------------------------

@needs_compiler
@pytest.mark.parametrize("kind", ["ordinary", "adversarial"])
@pytest.mark.parametrize("op", sorted(
    {"add", "subtract", "multiply", "divide", "sqrt", "negative", "absolute",
     "floor", "ceil", "trunc", "rint", "copysign", "fmod", "remainder",
     "maximum", "minimum", "fmax", "fmin"}))
def test_what_the_standard_guarantees_is_reproduced_bit_for_bit(op, kind):
    """**The load-bearing claim.** IEEE-754 §5 requires these to be
    correctly rounded — the exact result, rounded once — so two conforming
    implementations cannot disagree, and B1 is possible exactly to the
    extent that this holds.

    Measured on ordinary projection data *and* on the adversarial set
    (signed zero, subnormals, the full exponent range), because the module
    claims the guarantee unconditionally and a claim that only holds on
    well-behaved inputs is a different claim.
    """
    assert op in CORRECTLY_ROUNDED
    a, b = sample(kind)
    ufunc = getattr(np, op)
    binary = ufunc.nin == 2
    if op in ("sqrt",):
        a = np.abs(a)

    src = (f"lambda p, q: np.{op}(p, q)" if binary else f"lambda p, q: np.{op}(p)")
    with np.errstate(all="ignore"):
        reference = ufunc(a, b) if binary else ufunc(a)
        compiled = _numba.njit(eval(src, {"np": np}))(a, b)
    assert np.array_equal(_bits(reference), _bits(compiled), equal_nan=True), (
        f"{op} on {kind} inputs is not bitwise-reproducible under the "
        f"compiler — IEEE-754 §5 says it must be, so either the compiler is "
        f"non-conforming or this op does not belong in CORRECTLY_ROUNDED"
    )


@needs_compiler
def test_the_ops_it_declines_to_trust_really_do_differ():
    """The other direction, so the module is a measurement and not a
    superstition. If nothing in :data:`IMPLEMENTATION_DEFINED` actually
    differed, hoisting them all would be cost with no benefit and the
    category would want revisiting.

    They differ. ``exp``, ``log``, ``log1p``, ``expm1`` and ``power`` are
    each a last-bit apart on ordinary, finite, positive data — not at the
    extremes, not on NaN, but on the numbers a projection is made of.
    """
    a, b = sample("ordinary")
    differing = []
    for op in ("exp", "log", "log1p", "expm1", "power", "log2", "log10"):
        ufunc = getattr(np, op)
        binary = ufunc.nin == 2
        src = (f"lambda p, q: np.{op}(p, q)" if binary
               else f"lambda p, q: np.{op}(p)")
        with np.errstate(all="ignore"):
            reference = ufunc(a, b) if binary else ufunc(a)
            compiled = _numba.njit(eval(src, {"np": np}))(a, b)
        if not np.array_equal(_bits(reference), _bits(compiled)):
            differing.append(op)
        assert op in IMPLEMENTATION_DEFINED

    assert set(differing) >= {"exp", "log", "power"}, (
        f"only {differing} differed. If the transcendentals now agree, that "
        f"is worth knowing and worth acting on — but it is a coincidence of "
        f"two library versions rather than a guarantee, so widening "
        f"CORRECTLY_ROUNDED needs an argument, not just this measurement"
    )
    # And the difference is exactly what an ulp looks like, not a bug: one
    # step in the integer representation, never more.
    with np.errstate(all="ignore"):
        ref, got = np.exp(a), _numba.njit(lambda p: np.exp(p))(a)
    assert np.abs(_bits(ref) - _bits(got)).max() == 1


@needs_compiler
def test_a_reduction_has_no_safe_length():
    """The finding that puts every ``@pool`` body outside a kernel.

    Floating-point addition is not associative, NumPy sums in pairwise
    blocks, and a compiled ``sum`` sums its own way. The tempting mitigation
    — "reduce only short blocks in the kernel" — has no threshold to use:
    the first disagreement is at **twelve** elements, and past that which
    lengths agree depends on the values rather than on the length.
    """
    rng = np.random.default_rng(7)
    kernel_sum = _numba.njit(lambda z: np.sum(z))
    disagreeing = [n for n in range(1, 400)
                   if np.sum(v := rng.uniform(0.0, 1e6, n)) != kernel_sum(v)]

    assert disagreeing, "a compiled sum agreed at every length up to 400"
    assert min(disagreeing) < 20, (
        f"the first disagreement is at {min(disagreeing)} elements; if it "
        f"has moved far out, re-read whether a short-block reduction is now "
        f"worth having"
    )
    # Not a threshold: lengths above the first disagreement still agree
    # sometimes, which is what makes "only reduce small blocks" untestable.
    assert set(range(min(disagreeing), 400)) - set(disagreeing)


@needs_compiler
def test_the_forward_recursion_the_kernel_exists_for_is_bitwise():
    """The positive result the whole boundary is in service of.

    A survival chain — the shape every template's ``pols_if`` has — written
    as a compiled scalar loop over a slab reproduces the vectorized NumPy
    version bit for bit, because it is multiplication and subtraction and
    nothing else. That is what B1 can fuse, and this is the evidence that
    fusing it costs no accuracy.
    """
    rng = np.random.default_rng(11)
    n_mp, n_t = 2_000, 240
    q = rng.uniform(0.0001, 0.05, (n_t, n_mp))
    lapse = rng.uniform(0.0, 0.2, (n_t, n_mp))
    init = rng.uniform(1.0, 1000.0, n_mp)

    def vectorized(q, lapse, init):
        out = np.empty((q.shape[0] + 1, init.size))
        out[0] = init
        for t in range(q.shape[0]):
            out[t + 1] = out[t] * (1.0 - q[t]) * (1.0 - lapse[t])
        return out

    @_numba.njit
    def kernel(q, lapse, init):
        out = np.empty((q.shape[0] + 1, init.size))
        for j in range(init.size):
            out[0, j] = init[j]
        for t in range(q.shape[0]):
            for j in range(init.size):
                out[t + 1, j] = out[t, j] * (1.0 - q[t, j]) * (1.0 - lapse[t, j])
        return out

    reference = vectorized(q, lapse, init)
    compiled = kernel(q, lapse, init)
    # Shape, dtype and value separately — the rule RFC-069 and RFC-070
    # earned, where three bugs produced equal numbers with an unequal
    # contract and every one would have passed a value-only comparison.
    assert compiled.shape == reference.shape
    assert compiled.dtype == reference.dtype == np.float64
    assert np.array_equal(_bits(compiled), _bits(reference))
