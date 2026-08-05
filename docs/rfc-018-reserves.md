# RFC-018: Policy reserves, whole life and endowment

Status: **implemented** — `engine/library/reserves.py`,
`engine/library/endowment.py`

## Summary

PLAN.md §5.2's **first** bullet asks for "Term / whole life / endowment
(net & gross premium reserves)". `TermLife` covered term assurance; the
other two products and the whole of the reserves half were the gap.

It closes a structural one too. Every template in this library projects
*cashflows*, and every overlay in `engine/report` then builds its own
liability out of them — which is right for products whose statutory
liability *is* a projection. A traditional assurance is valued on a
**reserve**: a closed-form prospective value on a stated basis, a property
of the contract rather than of a reporting framework. It belongs beside the
cashflows, and now is.

## Almost everything here is an exact identity

Which is unusual in this codebase and worth saying: most of these tests
assert equality rather than closeness.

**Prospective equals retrospective.** Looking forward — what the office
still owes less what it will still collect — and looking back — what it has
collected and earned less what it has paid — give the same number, whenever
the reserve is valued on the basis the premium was solved on. That is not a
coincidence to verify once; it is what makes a reserve well defined, and it
stops being true the moment the bases differ. Both are implemented so the
identity can be asserted.

**An endowment is a term assurance plus a pure endowment**, `==` on floats.
The identity is the definition of the product, so it is written that way
rather than given a formula of its own — a second implementation would be a
second chance to disagree with it.

**The reserve is self-financing**: `(V_t + P)(1 + i) = q·S + p·V_{t+1}`.
What the office holds after the year's premium and a year's interest is
exactly what it needs for the claims it expects and the reserve it must
still carry. Residual under 1e-8 across products and payment terms.

**Nil at issue, and the benefit at maturity** — 0.0 opening, and the sum
assured closing for an endowment, nothing for a term.

## The two layers check each other

`reserves.net_premium` solves a closed form off the mortality table. The
`Endowment` template projects policies forward year by year through the
decrement machinery in `engine/data/decrements.py`. They share no code.

Priced on the closed form, the **projected** present value of premiums
equals the projected present value of benefits to nine significant figures
— the equivalence principle, arrived at from the other end. That is a
stronger check on both layers than either could give alone.

## The bug: a maturity is not a benefit already paid

The retrospective reserve subtracted the maturity benefit at duration
`term`. But a retrospective accumulation counts only what has *been* paid,
and an endowment's maturity falls due **at** that duration rather than
before it.

The closing retrospective reserve came out at zero where the prospective
one correctly held the sum assured. **The two definitions disagreed by
exactly the sum assured** — as wrong as a reserve can be, and invisible on
a term assurance, which is what the first smoke test used.

## What the net premium reserve leaves out

Expenses. The net premium is solved so the premiums exactly fund the
benefits, which means it funds nothing else — and a policy sold with
acquisition costs has spent real money by the end of its first year that no
part of that premium was ever going to recover.

So on the same contract, at issue:

| basis | reserve at issue |
|---|---|
| net premium | **0.00**, exactly |
| gross premium, 10% loading | **−3,178** |

And the sign of the gross figure runs the other way from the obvious guess,
which is worth stating because the first version of the test asserted the
opposite. A gross premium reserve is benefits and expenses *less* the office
premium, so charging **more** makes it **more negative** — the valuation
recognises the extra profit as an asset immediately:

| loading | gross reserve at issue |
|---|---|
| 5% | −737 |
| 10% | −3,178 |
| 20% | −8,061 |
| 40% | −17,828 |

That is precisely what a net premium basis refuses to do, and why it holds
nil at issue while the office is already out of pocket. **The strain is the
gap between the two bases, not the sign of either.**

## The modified bases, and the order they rank in

Both reduce the reserve without touching a cashflow. What they change is
when the office must find capital.

- **Zillmerising** takes an acquisition allowance off the reserve in
  proportion to the premiums still to come — the whole allowance at issue
  (exactly −900 on a 900 allowance) and exactly nothing at maturity.
- **Full preliminary term** is the limiting case: treat the first year as a
  one-year term assurance and start the accumulation a year late. The
  first-year reserve is **exactly zero** — by construction, not by being
  small — which permits the whole first year's acquisition cost.

Asserted at every duration: `FPT ≤ Zillmer ≤ net premium`, and all three
converge on the same maturity value. That ordering is the reason the
modified bases exist and the thing a regulator is choosing between.

## Whole life against the end of the table

The whole life reserve climbs monotonically to **94% of the sum assured at
age 116** and then turns back down.

The turn is a property of the *horizon*, not of the product. Running a whole
life against a finite table makes its last few years a shortening term
assurance, and a shortening term is worth less. A genuine whole life has no
such tail; the projection does. `WholeLife.term_years` is documented as the
run-off horizon rather than a cover period for exactly this reason.

A limited-payment contract charges 1.65 times the full-term premium for the
same benefit — not the 3× that dropping two thirds of the payments might
suggest, because the payments dropped are the late, heavily discounted,
heavily decremented ones.

## The reserve basis is a required argument

An office projects on its best estimate and reserves on something more
prudent. `reserve_series(basis, rate)` takes both explicitly and defaults to
neither: taking the projection's own basis would make the reserve a
restatement of the projection rather than a check on it.

## Not in scope

- **Surrender values** and paid-up values, which are contractual scales
  rather than valuations and vary by office.
- **With-profits**: asset shares, reversionary and terminal bonus. PLAN
  lists it as "later" and it needs the `@pool` machinery rather than this.
- **Sub-annual reserves.** Everything here is at whole durations; a
  reserve between anniversaries interpolates, and which interpolation is a
  regulatory choice.
- **Negative reserve floors.** A regime that forbids a negative reserve
  says so separately, and `zillmerised_reserve` deliberately does not floor
  — the floor is the regime's, not the arithmetic's.
- **Mortality improvement in the closed form.** The factors read annual `q`
  by attained age; a generational basis needs the calendar year threading
  through, which the projection layer already does and this one does not.
