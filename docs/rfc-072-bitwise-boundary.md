# RFC-072: What a compiled kernel may contain, and why the standard decides it

Status: **implemented** — `engine/core/bitwise.py`,
`tests/test_bitwise_boundary.py`, `pyproject.toml`,
`.github/workflows/ci.yml`

**This RFC does not build the compiled executor.** B1 remains not started.
What it does is remove the unknown that has kept B1 unstarted through six
RFCs, by answering the one question its design turns on — and the answer
changes the design.

## Summary

B1's own assessment says the item "is larger than its effort marker" and was
"left unstarted rather than begun badly". The reason given is that Numba
cannot compile a `@var` body as written, and that translating the DSL into a
compilable form is a project in itself. That is true. It is also not the
first question.

The first question is narrower and had never been asked:

> Given the same inputs and the same expression, does compiled code return
> the same bits NumPy does?

§1.2 asks for **bitwise** equality, not closeness. If the answer were "no",
no amount of DSL translation would produce a third executor that could join
the class, and B1 as specified would be impossible. So it was measured.

The answer is **yes for some operations and no for others**, and the split is
not a property of Numba. It is IEEE-754's, which means it will not move when
the compiler is upgraded and it would be the same for any other backend.

## What the standard guarantees, and what it declines to

IEEE-754 §5 requires addition, subtraction, multiplication, division and
square root — along with comparison, the sign and rounding manipulations, and
the remainder — to be **correctly rounded**: the exact mathematical result,
rounded once. Two conforming implementations cannot disagree. That is a
specification, not an observation about a build.

§9.2 *recommends* correct rounding for `exp`, `log`, `pow` and the
trigonometric functions, and **no library does it**, because a correctly
rounded `exp` costs far more than one accurate to within an ulp. NumPy ships
hand-written SIMD kernels for many of them; a compiler lowers them to its own
runtime or to libm. Both are within an ulp of the truth and of each other,
and an ulp is a different bit pattern.

Measured here, NumPy 2.4.6 against Numba 0.66.0 on one machine, on ordinary
finite positive data — not at the extremes, not on NaN, but on the numbers a
projection is made of:

| operation | bitwise | note |
|---|---|---|
| `+` `−` `×` `÷` `sqrt` | ✅ | IEEE-754 §5, correctly rounded |
| `floor` `ceil` `trunc` `rint` `copysign` `fmod` | ✅ | exactly specified |
| `maximum` `minimum` `where`, comparisons | ✅ | selection, not arithmetic |
| `exp` `log` `log2` `log10` `log1p` `expm1` | ❌ | 1 ulp |
| `power` (including `x ** 3`) | ❌ | 1 ulp |
| `tan` | ❌ | 1 ulp |
| `sin` `cos` `arctan` `hypot` | ⚠️ agreed | but §9.2 does not require it |
| `sign` | ❌ | only at signed zero: `sign(-0.0)` is `+0.0` in NumPy, `-0.0` compiled |

The ⚠️ row is the one worth dwelling on. `sin` and `cos` happened to agree.
They are classified as **must-hoist anyway**, because agreement the standard
does not require is a coincidence of two versions rather than a contract, and
the next NumPy release can withdraw it without breaking any promise — and it
would withdraw it silently, in a reserve.

`x ** 3` is the quiet one. NumPy special-cases small integer exponents into
repeated multiplication; the compiler calls `pow`. An integer power looks
exact and is not.

### Reductions are a third case, and the tempting mitigation has no threshold

`np.sum` is not one operation but an association order over many, and
floating-point addition is not associative. NumPy sums in pairwise blocks;
anything else sums differently.

The obvious mitigation is "reduce only short blocks in the kernel". There is
no length at which that is safe. Measured against a compiled `sum`, the first
disagreement is at **twelve** elements — and past that, which lengths agree
depends on the *values*, not on the length. 63 disagrees, 64 agrees, 127
agrees, 128 disagrees. A kernel that reduced small blocks would be right most
of the time, which is the worst available outcome.

This is what puts every `@pool` body outside a kernel, `pool_sum` being the
library's one reduction.

## The repo already knew half of this, from the other side

`np.exp` and `**` were found not to be bit-portable **across CPUs** — same
NumPy, same Python, different microarchitecture, different last bit. That is
why `engine.report.evidence.REPRODUCIBILITY_SCOPE` limits a pack digest to
one machine, and why the worked examples carry literal scenario values rather
than a seeded generator.

The same gap in the same standard bites again one layer in: the
transcendentals are not portable across *implementations* on one machine
either. It is one fact met twice, and naming it that way is worth more than
two separate cautions would be — the operations are the same operations.

## What this forces on B1, which is the useful part

A compiled kernel may contain **only** correctly-rounded operations.
Everything else has to be evaluated by NumPy and passed in as a precomputed
slab.

That reads like a concession and is the opposite. It is the only arrangement
under which §1.2 survives at all — and it is the arrangement the plan already
reaches for on unrelated grounds, where it proposes "assumption lookups
hoisted into precomputed slabs". Two independent arguments landing on the
same architecture is a good sign about the architecture.

It also lands where the performance is. The transcendentals in the library
are overwhelmingly **loop-invariant along the model-point axis** — a discount
factor, a period conversion, a crediting accumulation — or table gathers, and
both want computing once per period rather than once per policy. `FixedAnnuity`
is the clean illustration: its only power, `(1 + g) ** elapsed`, is a scalar
in `t` and never touches the model-point axis at all. What is left inside the
recursion, and what a kernel exists to fuse, is multiplication and
subtraction: survival chains, fund roll-forwards, cashflow accumulation.

And that part is provably safe. A survival chain — the shape every template's
`pols_if` has — written as a compiled scalar loop over a slab reproduces the
vectorized NumPy version bit for bit, because it is multiplication and
subtraction and nothing else. On a 2,000 × 240 slab it was also 2.7× faster,
which is not the ≥5× B1 targets but is a floor measured on the least
favourable case: one expression, so nothing to fuse.

So B1's shape is now determined rather than open:

1. Split each `@var` at its first non-exact operation. Above the split,
   NumPy, once per period, into a slab. Below it, the kernel.
2. The kernel takes the hoisted slabs as inputs and contains exact
   arithmetic only.
3. `fastmath` stays off — it is precisely the permission to contract
   `a * b + c` into a fused multiply-add, which is *more* accurate and a
   different number. `UNSAFE_FLAGS` names the family so the kernel builder
   has something to assert against.
4. A template whose recursion carries a transcendental **through** the
   model-point axis cannot be split this way, and is refused by name rather
   than compiled approximately.

## Two decisions inside the module

**An unclassified operation is refused, not routed to the safe side.**
`classify` returns `"unknown"` as its own answer, and `compilable` refuses on
it with the operation named and what to do about it. Sending it to the hoist
side would be safe and would silence the question: the kernel would quietly
stop fusing, the benchmark would quietly regress, and nobody would learn that
a new operation had appeared in a template. This repo has had that failure
in the other direction — a refusal whose condition emptied out and went on
passing while asserting nothing (RFC-071) — and the lesson is the same one.
A question is worth more than a default.

**A skipped measurement reads exactly like a passing one.** The 39
measurement cases need the `[compile]` extra, and a developer without it
should not be made to install a 60 MB compiler to run the suite. CI is the
opposite case. So the extra is not added to the main test matrix — it would
put an llvmlite download in front of every run, and a failure to build it
would block a suite that does not depend on it — but a separate
`bitwise-boundary` job installs it and sets `REQUIRE_COMPILE_EXTRA`, which
turns the skip into a **failure**. Without that, a job whose install step
half-succeeded would skip all 39 cases and report green, and the claim would
be unmeasured with nothing saying so.

## Acceptance

`tests/test_bitwise_boundary.py` — 45 tests, of which 39 are the measurement
and run only where a compiler is present.

Both directions are asserted, because both can rot. Every operation the
module claims is guaranteed is measured to be bitwise-reproducible, on
ordinary projection data **and** on an adversarial set (signed zero,
subnormals, the full exponent range) — because the module makes the claim
unconditionally and a claim that only holds on well-behaved inputs is a
different claim. And the operations it declines to trust are measured to
actually differ, so the category is a finding rather than a superstition: if
a future release made `exp` agree, the test fails and the right response is
to look again, not to keep hoisting an operation that no longer needs it.

The refusals: an unclassified operation; a verdict that does not name the
operation responsible; a reduction at any length; and — the one that guards
the guard — a required measurement that skipped.
