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
  bit for bit, and — the other direction — that the operations it declines
  to trust are refused *however the measurement comes out here*.

The asymmetry between those two is the whole point, and it was learned the
hard way. §5's guarantee is a specification: if ``multiply`` ever disagrees,
the first test fails and B1 is off, which is a thing to find out from a test
rather than from a reserve. §9.2's *absence* of a guarantee is not
symmetrical with it — an operation it declines to require correct rounding
for may still happen to agree, and whether it does is a property of the CPU.

This module originally asserted that ``exp``, ``log`` and ``power`` really do
differ. That held here and failed on a GitHub runner the first time CI ever
ran the job: NumPy dispatches AVX-512 kernels for the transcendentals while
the compiler calls libm, so on a CPU with AVX-512 they are a last bit apart
and on a CPU without it they agree exactly. The category therefore cannot be
justified by measurement on one machine, and is not. It is justified by the
standard, and the measurement is recorded beside it.
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


#: The operations §9.2 declines to require correct rounding for, measured
#: here against the compiler. Named once because three tests share them.
DECLINED = ("exp", "log", "log1p", "expm1", "power", "log2", "log10")


def _measure_declined():
    """Which of :data:`DECLINED` differ between NumPy and the compiler *here*.

    A list, deliberately, rather than a verdict: which way this comes out is a
    property of the machine, and the tests below are written so that neither
    answer changes what the boundary permits.
    """
    a, b = sample("ordinary")
    differing = []
    for op in DECLINED:
        ufunc = getattr(np, op)
        binary = ufunc.nin == 2
        src = (f"lambda p, q: np.{op}(p, q)" if binary
               else f"lambda p, q: np.{op}(p)")
        with np.errstate(all="ignore"):
            reference = ufunc(a, b) if binary else ufunc(a)
            compiled = _numba.njit(eval(src, {"np": np}))(a, b)
        if not np.array_equal(_bits(reference), _bits(compiled)):
            differing.append(op)
    return differing


@needs_compiler
def test_the_ops_it_declines_to_trust_are_refused_however_the_measurement_comes_out():
    """**The classification follows the standard, not this machine's answer.**

    This test used to assert that ``exp``, ``log`` and ``power`` *do* differ
    under the compiler, on the reasoning that a category which never caught
    anything would be paranoia. That assertion was true here and false on a
    GitHub runner, and CI failed on it the first time it ever ran.

    The cause is not a library version. NumPy dispatches hand-written AVX-512
    kernels for the transcendentals; the compiler calls libm. On a CPU with
    AVX-512 the two are a last bit apart, and on a CPU without it NumPy falls
    back to the same scalar path the compiler takes and **all seven agree
    exactly**. Disabling AVX-512 dispatch on one machine reproduces both
    answers — see the test below, which pins that.

    So agreement is a property of the silicon, and a measurement taken on any
    one machine cannot classify an operation. The classification rests on
    IEEE-754 §9.2 declining to *require* correct rounding, which is a
    specification and does not vary. What is asserted here is the mechanism:
    whichever way the measurement comes out, these operations are still
    refused. The measurement is recorded rather than believed.

    This makes the case for hoisting stronger, not weaker. An operation that
    agrees on one CPU and disagrees on another is precisely one that cannot go
    in a kernel claiming bitwise equivalence — and a machine where it happens
    to agree is the dangerous place to be standing, because that is where
    someone is tempted to widen CORRECTLY_ROUNDED on the strength of a green
    run.
    """
    differing = _measure_declined()

    for op in DECLINED:
        assert op in IMPLEMENTATION_DEFINED, (
            f"{op} is measured here but not classified; a measurement with no "
            f"category behind it decides nothing"
        )

    # The mechanism, which does not consult the measurement at all.
    ok, reasons = compilable(list(DECLINED))
    assert not ok, (
        f"compilable() admitted {DECLINED} into a kernel. On this machine "
        f"{differing or 'none of them'} differed from the compiler — but the "
        f"refusal must not depend on that, because it comes out the other way "
        f"on a CPU without AVX-512."
    )
    for op in DECLINED:
        assert any(op in reason for reason in reasons), (
            f"the refusal does not name {op}, so a reader cannot tell which "
            f"operation cost them the kernel"
        )

    # Where they do differ, it is one ulp — a rounding difference, not a bug.
    # Guarded on the measurement rather than assumed, because on a machine
    # where they agree the difference is zero and this would be asserting
    # that a correct answer is wrong.
    if "exp" in differing:
        a, _ = sample("ordinary")
        with np.errstate(all="ignore"):
            ref, got = np.exp(a), _numba.njit(lambda p: np.exp(p))(a)
        assert np.abs(_bits(ref) - _bits(got)).max() == 1


def _numpy_cpu_features():
    """What NumPy reports *finding*, which is not what it reports using."""
    try:
        return np._core._multiarray_umath.__cpu_features__
    except AttributeError:  # pragma: no cover - older layouts
        from numpy.core._multiarray_umath import __cpu_features__
        return __cpu_features__


@needs_compiler
@pytest.mark.skipif(
    not _numpy_cpu_features().get("AVX512F"),
    reason="no AVX-512 to disable, so the dispatch difference cannot be shown "
           "here; this is the CPU on which the transcendentals already agree",
)
def test_whether_the_transcendentals_agree_is_decided_by_simd_dispatch():
    """**The finding CI produced that no local run could.**

    Turning AVX-512 dispatch off makes every operation in :data:`DECLINED`
    agree bitwise with the compiler; turning it on makes every one of them
    differ. Same NumPy, same numba, same Python, same data — only the kernel
    NumPy selects changes.

    That is the same fact as ``REPRODUCIBILITY_SCOPE``'s, met a third time: a
    pack digest is an identity on a machine, `np.exp` is not bit-portable
    across microarchitectures, and now — the operational consequence — a
    *measurement* of bitwise agreement is not portable either. A green
    boundary run says what this CPU does, not what the arithmetic guarantees.

    Run in a subprocess because ``NPY_DISABLE_CPU_FEATURES`` is read when
    NumPy initialises, so it cannot be set from inside a running test. Naming
    a feature NumPy does not report finding is a silent no-op that looks
    exactly like no difference existing, which is why the skip above keys off
    what NumPy actually reports.
    """
    import subprocess
    import sys as _sys
    import textwrap

    program = textwrap.dedent(
        """
        import numpy as np, numba
        rng = np.random.default_rng(20260807)
        a = rng.uniform(0.1, 10.0, 50_000)
        b = rng.uniform(0.5, 3.0, 50_000)
        differing = []
        for op in ("exp", "log", "log1p", "expm1", "power", "log2", "log10"):
            u = getattr(np, op)
            binary = u.nin == 2
            src = ("lambda p, q: np.%s(p, q)" % op if binary
                   else "lambda p, q: np.%s(p)" % op)
            with np.errstate(all="ignore"):
                ref = u(a, b) if binary else u(a)
                got = numba.njit(eval(src, {"np": np}))(a, b)
            if not np.array_equal(ref.view(np.int64), got.view(np.int64)):
                differing.append(op)
        print(",".join(differing))
        """
    )

    def run(disabled: str | None) -> list[str]:
        environment = dict(os.environ)
        if disabled:
            environment["NPY_DISABLE_CPU_FEATURES"] = disabled
        else:
            environment.pop("NPY_DISABLE_CPU_FEATURES", None)
        done = subprocess.run([_sys.executable, "-c", program],
                              capture_output=True, text=True, env=environment)
        assert done.returncode == 0, done.stderr[-2000:]
        return [op for op in done.stdout.strip().split(",") if op]

    with_simd = run(None)
    # Every AVX-512 tier NumPy might dispatch through, because disabling only
    # the top one leaves it selecting the next.
    without_simd = run("AVX512_SPR,AVX512_ICL,AVX512_SKX,AVX512_CLX,"
                       "AVX512_CNL,AVX512F,X86_V4")

    assert with_simd, (
        "AVX-512 is present and reported, yet nothing differed with SIMD "
        "dispatch enabled — the premise of the boundary's second category "
        "has changed and wants reading, not patching"
    )
    assert not without_simd, (
        f"{without_simd} still differed with AVX-512 dispatch disabled, so "
        f"SIMD selection is not the whole explanation and the remaining "
        f"difference is unaccounted for"
    )
    assert set(with_simd) > set(without_simd)


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


def test_a_structural_op_is_allowed_for_a_different_reason_than_arithmetic():
    """**Selection and reshaping perform no arithmetic**, so a kernel may
    contain them — but not because IEEE-754 pins their rounding. It pins
    nothing about them; they simply do not round, because a value that is
    copied is the value.

    Kept as its own category rather than folded into ``CORRECTLY_ROUNDED``
    because the emitter treats them differently: a reshape is resolved when
    the loop is laid out and never appears in the loop body. ``where`` used
    to sit in the arithmetic set with a comment saying it was not arithmetic,
    which is the observation that produced this set — and ``atleast_1d``,
    which `GeneralInsurance` actually uses, was refused as unclassified until
    it existed.
    """
    from engine.core.bitwise import STRUCTURAL

    assert classify("where") == "structural"
    assert classify("atleast_1d") == "structural"
    assert classify("take") == "structural"
    assert classify("multiply") == "exact"

    # A kernel may contain both kinds, so neither is a refusal.
    ok, reasons = compilable(["add", "where", "atleast_1d", "take"])
    assert ok and reasons == []

    # Four categories, still disjoint — an op in two would make classify
    # order-dependent and the caller would get whichever branch came first.
    for other in (CORRECTLY_ROUNDED, IMPLEMENTATION_DEFINED, ORDER_DEPENDENT):
        assert not STRUCTURAL & other

    # And the extrema *reductions* stay reductions: `argmax` moves an index
    # and is structural, `amax` picks a value out of an axis and is not.
    assert classify("argmax") == "structural"
    assert classify("amax") == "reduce"
