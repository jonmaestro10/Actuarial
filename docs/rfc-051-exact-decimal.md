# RFC-051: The number the actuary wrote, and the number the machine kept

Status: **implemented** — `engine/core/exact.py`, `tests/test_exact.py`

## Summary

PLAN §3.4's unbuilt promise, and the one line of it that had never been
examined:

> Document float behavior; offer a slow exact-decimal audit mode for
> regulatory sign-off runs.

The mode is built. Its purpose is **not to produce better numbers** — it is
to produce a *second answer under different arithmetic*, so the float
answer's error can be measured rather than assumed. That is the
dual-executor move one level down: RFC-033's invariant compares two
executors that share an arithmetic, and this compares two arithmetics that
share an executor.

```python
approx = runner.run(FixedAnnuity, points, basis, 40)
exact  = run_exact(FixedAnnuity, points, basis, 40)
agreement(approx, exact)["worst_relative"]     # → 8.7e-16
```

**The result.** Across the nine templates the mode can audit, the
interpreted float executor agrees with 34-digit decimal to between
**1.1 × 10⁻¹⁵ and 1.0 × 10⁻¹³** relative, worst case over every variable and
every period. That is the bound the plan asked for and the repo did not
have. It is a *measurement*, and the number worth carrying is the worst one:
about **1 × 10⁻¹³**, or roughly thirteen trustworthy significant digits.

## The decision the whole mode rests on

A float assumption is not the number the actuary wrote. `0.035` parses to

```
0.0350000000000000033306690738754696212708950042724609375
```

because binary floating point has no exact representation for it. That error
exists *before any arithmetic happens*, and it compounds through a forty-year
discount chain.

So conversion goes through `Decimal(repr(x))`, which recovers `0.035`
exactly — **not** `Decimal(x)`, which faithfully preserves the binary value
and would defeat the entire point. This is the difference between an audit
mode and an expensive re-run: convert the wrong way and the run still
completes, still uses decimal arithmetic, still reports 34 digits, and still
carries the representation error it was built to remove.

Both readings are meaningful, so both are offered, and they answer different
questions:

| reader | question it answers |
|---|---|
| `as_written` | what does the basis *the actuary stated* produce? — the sign-off question |
| `as_stored` | what does the basis *the machine holds* produce? |

The gap between two such runs is the **representation** error with arithmetic
error held constant. Without both, a discrepancy has two candidate causes and
no way to separate them.

## "Exact" is a qualified claim, and the qualification is measured

The name overclaims if left alone, so the module states the limit and a test
asserts it. Under `EXACT_CONTEXT` the mode is:

- **exact** for `+`, `−` and `×` on decimal inputs — no representation error,
  no rounding;
- **correctly rounded to 34 significant digits** for `÷` and `**`, because
  `1/3` has no finite decimal expansion and no arithmetic gives it one.

34 is not a preference: it is the significand of IEEE 754 **decimal128**, so
the mode is a named format. Double precision carries about 15.95 decimal
digits, so an audit run holds roughly eighteen more than the run it checks —
which is what lets the difference be read as the float run's error rather
than as two approximations disagreeing.

**And exactness has a horizon that depends on the inputs, which was the
finding.** `pols_if(t)` is `(1 − q)^t`. With `q = 0.015` the survival factor
`0.985` has three decimal places, so `t` multiplications need `3t` of them —
and the recursion is *genuinely exact*, equal to an independently computed
closed form, only while `3t ≤ 34`. That is **t ≤ 11**. At `t = 13` the chain
and the closed form part company: the chain has rounded twelve times, the
closed form once.

Neither is wrong, and what survives past the boundary is still the point of
the mode — 34 digits against double's 16. But "exact-decimal mode" would be a
false description of period 13, and a sign-off pack is exactly the document
where a reader takes a label at face value.

## Why a `Decimal` subclass rather than a rewritten executor

`float + Decimal` is a `TypeError` in Python, deliberately: silently mixing
them is how a decimal calculation becomes a binary one halfway through. But
every `@var` body in the library contains float literals — `1.0 - q`,
`0.5 * x`.

`Exact` is a `Decimal` subclass that coerces float operands through
`as_written`. That makes **the whole template library polymorphic without
editing a single template**, which matters far beyond convenience: an audit
mode that needed its own copies of the formulas would be auditing the copies.
The same argument applies to the assumptions — `_DecimalView` is a proxy over
the *real* basis, so the audit run uses the same mortality table and the same
treaty as the run it is checking.

The operators are generated rather than written out, because a missing
`__rsub__` would surface as a `TypeError` deep inside one template's
recursion and nowhere else.

## The bug this design nearly shipped with

The proxy converts arguments on their way *into* the float assumption layer.
The first version converted every argument down to float, on the reasoning
that the bases behind `periodic_q` are table lookups and calendar arithmetic.

That was wrong, and wrong in the worst available way. `Decrements.split`
takes the **in-force count** — an accumulated quantity, the whole state of
the recursion — and converting it down put the entire survival chain back
into float arithmetic while the answer still arrived wearing `Decimal`. The
run completed. The types were right. The numbers were the float numbers.

It was caught because the closed-form test asserted *equality* rather than a
tolerance, and equality failed at `t = 6` where the digits should still have
fitted. A tolerance would have passed.

The rule now distinguishes by value, not by parameter name: **integral
arguments are lookup keys and go down to `int`; non-integral arguments are
quantities and pass through untouched.** It works because the arithmetic on
the other side is written in operators — `Decrements.split` is "deliberately
uncoerced: the operands stay exactly what the caller passed", a property
RFC-004 chose for an unrelated reason and which turns out to be what makes
this mode possible at all. Where a basis is *not* written that way, it fails
on the `Decimal` rather than silently downgrading it, and that becomes a
stated refusal.

## Coverage, stated rather than implied

| verdict | count | which |
|---|---|---|
| **audited** | 9 | CreditLife, Endowment, FixedAnnuity, GeneralInsurance, IncomeProtection, LongTermCare, TermLife, UniversalLife, WholeLife |
| **outside** | 8 | 5 bind a scenario set; 3 are pooled or coupled |
| **refused** | 2 | PayoutAnnuity, PensionBuyout — the assumption layer returns arrays |

The "outside" bucket is not this mode's limitation: the interpreted executor
cannot run those either, for reasons §1.2 already states. The "refused"
bucket is the interesting one — a yield curve hands back a vector, and a
one-policy-at-a-time decimal run has nothing to apply it to. Falling back to
float there would produce a "sign-off run" that was a float run wearing a
label, which is the one outcome worse than refusing, so it raises with the
shape it was given.

The partition is asserted in CI, including that **none of the three buckets
may empty out** — a bucket that has quietly emptied makes every assertion
over it vacuous while the test keeps passing, which is the trap RFC-071 hit
from the other direction.

## Two smaller decisions

**The context traps.** Float returns `inf` for a division by zero and `nan`
for an invalid operation, and both propagate silently into a reported
reserve. `EXACT_CONTEXT` raises on `InvalidOperation`, `DivisionByZero` and
`Overflow`. A sign-off run that *stopped* is strictly better than one that
produced `NaN` and a number beside it.

**`agreement` reports relative and absolute separately, with locations.** A
relative difference against zero is either zero or undefined, and a cashflow
that should be exactly nothing is the one case where the absolute figure is
what matters. And a single aggregate cannot say whether a discrepancy is
spread thinly or concentrated in one variable at one period — which is
exactly what decides whether it is rounding or a bug.

## Acceptance

`tests/test_exact.py` — 15 tests. The conversion decision is asserted in both
directions and shown to compound. The qualified claim is asserted on both
sides: a thousand additions of `0.1` come to exactly `100`, where float does
not; and `1/3` fills the precision and fails to round-trip.

The exactness horizon is asserted at the boundary — exact through `t = 11`,
diverging from the closed form at `t = 13`, with the float run leaving the
chain strictly earlier, so the comparison is not vacuous.

The refusals: a pooled model (reusing `check_per_policy` rather than
restating the rule, so the two executors cannot drift on where the class
ends), an array-valued assumption, an empty block, a `bool` offered as a
quantity, and a division by zero that raises instead of returning infinity.

The mode is asserted to be **slower** than the float executor, so the docs
cannot drift into implying it is free — with a deliberately loose bound,
because that is a statement about order of magnitude and a tight one would
fail on a shared runner for reasons that have nothing to do with the engine.
Measured here: about **3× the interpreted float executor**, which is itself
the slow one.
