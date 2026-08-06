r"""Which arithmetic a compiled kernel may use, if it is to agree bit for bit.

§1.2 asks every executor to produce **bitwise-identical** results, not close
ones. B1 proposes to add a third executor that compiles the ``@var`` graph
into a native forward loop, and the question that has to be answered before
any of it is designed is narrow and empirical: *given the same inputs and the
same expression, does compiled code return the same bits NumPy does?*

The answer is **yes for some operations and no for others**, and the split is
not a property of the compiler. It is IEEE-754's.

What the standard requires, and what it leaves open
---------------------------------------------------
IEEE-754 §5 requires the five basic operations — addition, subtraction,
multiplication, division and square root — together with comparison, the
sign and rounding manipulations, and the remainder, to be **correctly
rounded**: the returned value is the exact mathematical result rounded once,
under the current rounding mode. Two conforming implementations therefore
cannot disagree, and the guarantee is a *specification*, not an observation
about a particular build.

The standard says nothing of the kind about the transcendental library.
``exp``, ``log``, ``pow`` and the trigonometric functions are
*recommended* to be correctly rounded (§9.2) and no implementation does it,
because a correctly rounded ``exp`` costs far more than one within an ulp.
NumPy ships hand-written SIMD kernels for many of them; a compiler lowers
them to its own runtime or to libm. Both are within an ulp of the true
result and of each other, and an ulp is a different bit pattern.

The repo already knew half of this. ``np.exp`` and ``**`` were found not to
be bit-portable *across CPUs* — same NumPy, same Python, different
microarchitecture, different last bit, which is why
:data:`engine.report.evidence.REPRODUCIBILITY_SCOPE` limits a pack digest to
one machine. The same fact bites again one layer in: the transcendentals are
not portable across *implementations* on one machine either. It is the same
gap in the same standard, met from the other side.

**Reductions are a third case.** ``np.sum`` is not a single operation but an
association order over many, and floating-point addition is not associative.
NumPy sums in pairwise blocks; anything else sums differently. There is no
safe length — measured, the first disagreement is at **twelve** elements, and
which lengths agree past that depends on the values, not on the length. So a
reduction is never compiled, and that is what makes ``pool_sum`` — every
``@pool`` body in the library — a hoist rather than a kernel operation.

What this forces on B1's design
-------------------------------
A compiled kernel may contain **only** :data:`CORRECTLY_ROUNDED` operations.
Everything else has to be evaluated by NumPy and passed in as a precomputed
slab. That is not a concession extracted from the guarantee; it is the only
arrangement under which the guarantee survives, and it happens to be the
arrangement the plan already reaches for on other grounds — assumption
lookups "hoisted into precomputed slabs".

It also lands well. The transcendentals in the library are overwhelmingly
*loop-invariant along the model-point axis* — a discount factor, a
period-conversion, a crediting accumulation — or table gathers, and both are
naturally computed once per period rather than once per policy. What is left
in the recursion, and what a kernel would exist to fuse, is multiplication
and subtraction: survival chains, fund roll-forwards, cashflow accumulation.

Nothing here imports the compiler. This module states a contract; the
measurement that the contract holds is ``tests/test_bitwise_boundary.py``,
which needs the ``[compile]`` extra and says so when it is absent.
"""

from __future__ import annotations

#: Operations IEEE-754 §5 requires to be correctly rounded, and which two
#: conforming implementations therefore cannot disagree about. A compiled
#: kernel may use these and nothing else.
#:
#: Named by NumPy ufunc, because that is what a tracer recording a ``@var``
#: body sees. ``fma`` is absent because NumPy has no ufunc for it — and a
#: compiler that contracts ``a * b + c`` into one is a real hazard here,
#: which is why ``fastmath`` must stay off (see :data:`UNSAFE_FLAGS`).
CORRECTLY_ROUNDED = frozenset({
    # §5.4.1 arithmetic
    "add", "subtract", "multiply", "divide", "true_divide", "sqrt",
    # §5.5.1 sign-bit operations — exact by construction, they move bits
    "negative", "positive", "absolute", "copysign",
    # §5.9 / §5.3.1 comparison and rounding to integral, exactly specified
    "equal", "not_equal", "less", "less_equal", "greater", "greater_equal",
    "floor", "ceil", "trunc", "rint",
    # §5.3.1 remainder, correctly rounded
    "fmod", "remainder",
    # §5.3.1 minimum/maximum. Exact in value; the standard's NaN and signed
    # zero handling is the part that has historically varied between
    # libraries, so a kernel using these on data that can hold either is
    # relying on more than the standard gives it.
    "maximum", "minimum", "fmax", "fmin",
    # Not an arithmetic operation: a data-movement select.
    "where",
    # Boolean algebra over the masks indicator-style formulas are built on.
    "logical_and", "logical_or", "logical_not", "logical_xor",
    "isnan", "isinf", "isfinite",
})

#: Operations whose result is **implementation-defined to within an ulp**.
#: IEEE-754 §9.2 recommends correct rounding for these and no library does
#: it. They must be evaluated by NumPy and passed into a kernel as data.
#:
#: Measured to differ between NumPy 2.4 and Numba 0.66 on one machine:
#: ``exp``, ``exp2``, ``expm1``, ``log``, ``log2``, ``log10``, ``log1p``,
#: ``power`` and ``tan``. Others in this set — ``sin``, ``cos``, ``arctan``,
#: ``hypot`` — happened to agree when measured, and are listed here anyway.
#: Agreement that the standard does not require is a coincidence of two
#: versions, and a coincidence is not a contract: the next NumPy release can
#: withdraw it without breaking any promise, and it would withdraw it
#: silently.
IMPLEMENTATION_DEFINED = frozenset({
    "exp", "exp2", "expm1", "log", "log2", "log10", "log1p",
    "power", "float_power",
    "sin", "cos", "tan", "arcsin", "arccos", "arctan", "arctan2",
    "sinh", "cosh", "tanh", "arcsinh", "arccosh", "arctanh",
    "hypot", "cbrt", "logaddexp", "logaddexp2",
    # `sign` is not IEEE-mandated and was measured to differ at **signed
    # zero**: NumPy returns +0.0 for `sign(-0.0)`, the compiler -0.0. Equal
    # as numbers, different bits, and therefore a different results digest.
    "sign",
})

#: Reductions along an axis. Not one operation but an association order over
#: many, and floating-point addition is not associative. Never compiled.
#:
#: There is no safe length. NumPy sums in pairwise blocks; measured against a
#: compiled ``np.sum`` the first disagreement is at **12** elements, and past
#: that which lengths agree depends on the values rather than on the length.
#: A kernel that reduced "only small blocks" would be right most of the time.
ORDER_DEPENDENT = frozenset({
    "sum", "prod", "mean", "std", "var", "cumsum", "cumprod",
    "dot", "matmul", "einsum", "average",
    # The extrema reductions are exact in value, but they are listed here
    # because they still pick *an* element and NumPy's NaN and signed-zero
    # tie-breaking is not something the standard pins either.
    "amax", "amin", "max", "min", "nanmax", "nanmin",
})

#: The library versions the *measurement* was taken against, as
#: ``major.minor``. Provenance for the evidence, not a constraint on the
#: contract — and the difference matters.
#:
#: :data:`CORRECTLY_ROUNDED` is guaranteed by the standard, so no release of
#: anything can withdraw it; :data:`IMPLEMENTATION_DEFINED` is the set this
#: module declines to trust, so no observation can weaken it either. The
#: classification is therefore **version-independent by construction**, and
#: an upgrade cannot make a kernel built to it wrong.
#:
#: What an upgrade *can* do is change which of the untrusted operations
#: happen to agree — NumPy has rewritten its SIMD transcendental kernels
#: before and will again — which changes what is worth hoisting rather than
#: what is safe. So this is recorded, and the measurement re-checks it, to
#: make the evidence's age visible instead of letting a green run assert
#: something about a library the repo stopped using two releases ago.
MEASURED_AGAINST = {"numpy": "2.4", "numba": "0.66"}

#: Compiler flags that void the guarantee whatever operations are used.
#: ``fastmath`` licenses reassociation and contraction — it is exactly the
#: permission to turn ``a * b + c`` into a fused multiply-add, which is more
#: accurate and a different number.
UNSAFE_FLAGS = ("fastmath", "ffast-math", "associative_math",
                "reciprocal_math", "no_signed_zeros")


class NotCompilable(ValueError):
    """An expression a compiled kernel cannot reproduce bit for bit."""


def classify(op: str) -> str:
    """``"exact"``, ``"hoist"``, ``"reduce"`` or ``"unknown"`` for a ufunc name.

    ``"unknown"`` is deliberately **not** merged into ``"hoist"``. An
    operation nobody has classified is a question, and answering it by
    routing it to the safe side would silence the question — the kernel would
    quietly get slower and no one would learn that a new op had appeared.
    Callers refuse on it; see :func:`compilable`.
    """
    if op in CORRECTLY_ROUNDED:
        return "exact"
    if op in IMPLEMENTATION_DEFINED:
        return "hoist"
    if op in ORDER_DEPENDENT:
        return "reduce"
    return "unknown"


def compilable(ops) -> tuple[bool, list[str]]:
    """``(verdict, reasons)`` for a sequence of ufunc names.

    The reasons are the point. A kernel that cannot be built is a fact about
    a specific operation in a specific formula, and reporting "not
    compilable" without it would leave the caller to guess which of forty
    variables was responsible.
    """
    reasons = []
    for op in dict.fromkeys(ops):
        kind = classify(op)
        if kind == "hoist":
            reasons.append(
                f"{op}: implementation-defined to within an ulp (IEEE-754 "
                f"§9.2 recommends correct rounding and no library provides "
                f"it), so NumPy and a compiler need not agree — evaluate it "
                f"in NumPy and pass the result in"
            )
        elif kind == "reduce":
            reasons.append(
                f"{op}: a reduction, whose answer depends on association "
                f"order; there is no length at which this is safe"
            )
        elif kind == "unknown":
            reasons.append(
                f"{op}: not classified. Decide whether IEEE-754 requires it "
                f"to be correctly rounded and add it to engine/core/bitwise.py"
            )
    return not reasons, reasons
