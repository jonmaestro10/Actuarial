# RFC-024: Actual against expected

Status: **implemented** — `engine/report/experience.py`

## Summary

Every reporting overlay in this library names the same thing as its own
open edge. RFC-012:

> **Experience variance.** Everything here is expected against expected, so
> revenue and expenses cancel on claims and the service result is exactly
> the two margins unwinding. Splitting actual from expected is what turns
> this into a reporting run rather than a projection of one.

RFC-015, RFC-017 and RFC-023 repeat it. This is that split, and it has two
halves that are usually run together and should not be: an **arithmetic**
half that has no single right answer, and a **classification** half that
decides in which year the profit appears.

## The finding: the ordering decides the sign of the interest result

A term assurance book was expected to make a present value of profits of
**3.11m** and made **0.26m** — a variance of **−2.86m**. Attributing it to
mortality, lapses, interest and expenses is where the trouble starts,
because the effects interact: heavier mortality on a book that also lapsed
less is not the sum of the two effects measured separately.

Peeling the drivers off one at a time — the commonest analysis of surplus —
in three different orders:

| order | mortality | lapse | interest | expenses |
|---|---|---|---|---|
| mort → lapse → int → exp | −2,633,708 | +93,227 | −85,348 | −231,611 |
| exp → int → lapse → mort | −3,276,223 | +496,962 | **+108,048** | −186,228 |
| int → exp → mort → lapse | −2,830,254 | +50,993 | +121,294 | −199,473 |

Every one of those adds to −2.86m exactly. They are all "correct". And the
full range across every ordering:

| driver | lowest | highest | width | width ÷ its own value |
|---|---|---|---|---|
| mortality | −3,278,426 | −2,632,162 | 646,264 | 0.22 |
| lapse | +50,993 | +531,304 | 480,311 | 1.72 |
| **interest** | **−101,996** | **+162,068** | 264,064 | **14.35** |
| expenses | −233,814 | −184,682 | 49,133 | 0.24 |

**The interest line changes sign.** Same book, same year, same experience:
an analysis of surplus reports an interest profit of 162k or an interest
loss of 102k depending only on where interest was peeled off. Its range is
fourteen times its own value, so the number reported against it is decided
more by the ordering than by anything that happened.

The lapse line moves by a factor of ten. Only mortality and expenses — the
two largest and the two smallest — are robust to the choice.

## The interaction is a fifth of the variance, not a rounding

Measure each driver alone against the base instead, and the contributions
no longer add up: they explain 77% of the movement and leave **22.7%**
unattributed. That residual *is* the interaction, and a sequential analysis
does not avoid it — it silently distributes it among the lines according to
their order.

The interaction is second order in the size of the variances. So it is
negligible in a quiet year, and it is not negligible in the year anybody
actually wants an analysis of surplus for.

## Three methods, and what each one gives up

| | adds up | order-independent |
|---|---|---|
| `sequential` | **yes** | no |
| `isolated` | no | **yes** |
| `shapley` | **yes** | **yes** |

The Shapley value is the average of a driver's marginal contribution over
*every* order. It is the unique allocation that is efficient (adds up),
symmetric (two drivers that always contribute the same get the same) and
null (a driver that changes nothing gets nothing) — a theorem, not a
preference, and all three properties are asserted rather than cited.

It costs `2**n` evaluations against `n`, which on four drivers is sixteen
projections instead of four. `contribution_range` gets the exact range over
all `n!` orderings out of the same sixteen, because a driver's contribution
depends only on the **set** peeled off before it and every subset is some
ordering's prefix.

The engine does not choose between the three. `sequential` reports the order
it used, `isolated` reports its residual, and `order_sensitivity` says what
the ordering was worth — because an analysis of surplus quoted without that
is an opinion presented as a measurement.

## The bug this found: a lapse rate stored twice

`Assumptions` holds the lapse rate as both `lapse` and `dynamic_lapse.base`,
and different templates read different copies — `TermLife` takes
`periodic_lapse()` off the scalar, `UnitLinked` takes
`dynamic_lapse.rate(...)`. A driver swap that set one and not the other
would produce a run on the **actual** basis for some products and the
**expected** basis for others, with nothing in any output to show for it.

`COUPLED_FIELDS` names the groups that have to move together, in the one
place a swap can consult it.
`engine.report.solvency2.Stress.apply` already handles the same coupling by
hand; this is the same list, written down.

## The second half: the same variance lands in two places

Under IFRS 17 the destination of a variance is decided by which service it
relates to, not by what it is:

- current or past service — claims incurred, expenses paid — goes **straight
  to profit or loss**;
- future service — §B96(a), and §B97(a) for premiums relating to future
  coverage — adjusts the **CSM** and never appears in the result at all.

So an adverse 260 is either 260 off this year's profit or 260 off a margin
that unwinds over decades. The standard says where each *category* goes. It
does not say how to tell an **experience variance** from a **change in
estimate** on the same number — and that judgement moves profit between
years without moving a single cashflow.

`Attribution.reclassified` exists so the judgement can be measured rather
than argued about, and `allocate` refuses to place a variance whose service
period is not stated: a default there is a decision about profit dressed up
as a convenience.

## Not in scope

- **Deciding the split.** This module makes the classification explicit and
  will not make it for you, for the reason above.
- **Wiring the variances into RFC-012's CSM roll-forward.** `Attribution`
  produces the two totals; feeding the CSM adjustment back into
  `measure` and re-running the roll is the next step and is a change to
  that module rather than to this one.
- **Attributing at a finer grain than a driver.** Splitting a mortality
  variance into volume, mix and rate is the same machinery on a longer
  driver list, and `MAX_DRIVERS` is where the exhaustive methods stop being
  reasonable — beyond it, group the drivers or accept a sequential analysis
  and quote the order.
- **Stochastic experience.** Everything here compares two deterministic
  bases. A distribution of outcomes is RFC-016's machinery.
- **Analysis of change in the balance sheet**, as against the result.
  RFC-020's `analysis_of_change` is the embedded-value shape of the same
  question and has the same residual discipline.
