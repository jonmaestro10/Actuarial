# The number the actuary wrote, and the number the machine kept

**Claim.** `0.035` is stored as `0.0350000000000000033306690738754696…`, so
a basis carries an error **before any arithmetic runs**. Read as written it
is exactly 3.5%, and the two readings diverge from the 17th digit onward.

**Demonstrate it:** `python scripts/findings/representation_error.py`
**Recorded in:** [`docs/rfc-051-exact-decimal.md`](../rfc-051-exact-decimal.md)

## Two readings of one assumption

| reading | value |
|---|---|
| as written | `0.035` |
| as stored | `0.0350000000000000033306690738754696212708950042724609375` |

Compounded over forty years the two differ by about **1.3 × 10⁻¹⁶**
relative. Small — and it is not the size that matters but the fact that it is
there before the model does anything, and that no amount of care in the
projection can remove it.

## What the audit mode does with that

`engine/core/exact.py` offers both readings deliberately, because the gap
between two otherwise identical runs is precisely the **representation**
error with arithmetic error held constant. Without both, a discrepancy has
two candidate causes and no way to separate them.

Running the float engine against 34-digit decimal on the same basis gives the
bound the repository did not previously have: the interpreted float executor
agrees to within **8.6 × 10⁻¹⁶** on `FixedAnnuity`, and to within about
**1 × 10⁻¹³** across the nine templates the mode can audit. Roughly thirteen
trustworthy significant digits.

## The trap inside the trap

Converting via `Decimal(x)` instead of `Decimal(repr(x))` produces a run that
**still completes, still uses decimal arithmetic, and still reports 34
digits** — while carrying the very representation error the mode was built to
remove. The output looks like an audit and is not one.

That is the general shape worth remembering: an audit mode's failure is
rarely an error message. It is a result that looks exactly like success.
