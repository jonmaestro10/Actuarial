# RFC-042: Two claim states, and the pool a Markov chain cannot hold

Status: **implemented** — `engine/library/long_term_care.py`,
`tests/test_long_term_care.py`

## Summary

Execution plan §10, item C4:

> `engine/library/long_term_care.py` on the multi-state engine
> (`engine/data/multistate.py`, `engine/library/income_protection.py` is the
> pattern): active → claim (home/facility) → dead, benefit-utilization and
> inflation-protection mechanics.

```python
run_vectorized(LongTermCare, book, basis, 40,
               outputs=["home_care", "facility_care", "benefits",
                        "benefit_maximum", "progression"])
```

Four states — `active`, `home_care`, `facility_care`, `dead` — and the two
mechanics the item names. It arrived with a worked example on its first
commit, because RFC-066's `assumptions.transitions` was built first; the
sequencing was the point.

## Utilization is per claim state, because the asymmetry is the structure

A claimant does not automatically draw the policy maximum, and the reason
differs by where they are. Home-care claimants typically use fewer hours than
the cap allows, so utilization runs well below one. Facility costs generally
*exceed* the cap, so utilization sits at one and the maximum binds.

One utilization rate for both would price a product nobody sells. So there
are two, `facility_utilization` defaults to 1.0, and home care is written as
a **percentage of the facility maximum** rather than as a second independent
maximum — which is how policies are actually written and one fewer input to
get inconsistent.

Utilization above one is **refused**. It would pay more than the policy
maximum, which no policy does, and it is exactly what a cost-inflation factor
mistaken for a utilization rate looks like — a plausible number in the wrong
field, which is the class of error a range check catches and a reviewer does
not.

## Simple and compound are not a formatting choice

The inflation-protection rider grows the maximum, and *how* is the whole
question. Simple adds the rate to the **original** maximum each year;
compound applies it to the grown one.

Over the thirty years between a policy issued at 55 and a claim at 85, at 5%:

| | factor at 30 years |
|---|---|
| simple | 2.50× |
| compound | 4.32× |

**Nearly double the benefit for the same stated rate** — and they are 2%
apart after five years, which is why the choice looks cheap at the point of
sale and is worth 73% of the benefit at the point of claim. A module offering
one of these and calling it inflation protection would be pricing a different
product, so both are here and the divergence is asserted rather than
described.

Increases land on **anniversaries**, the same rule as
`engine/library/pension_buyout.py` and for the same reason: a rider grants
its rise on a policy anniversary, and a fractional exponent pays part of one.
The test asserts the flat stretch as well as the step, because a smooth curve
passes through the same anniversary values.

A negative rate is refused — an inflation-protection rider does not reduce
the maximum it protects — and a misspelt mode raises rather than falling
through to no rider, which would silently price a policy without the benefit
its holder paid for.

## The benefit pool is not here, and *why* is the finding

Most LTC policies cap the lifetime benefit: a pool of money, often a number
of years of the daily maximum. When it exhausts, the benefit stops though the
claimant is still on claim.

**A Markov chain over states cannot express that.** The pool depends on how
long *this* claimant has been claiming, not on the state they occupy.
Occupancy is a headcount: two lives in `facility_care` are indistinguishable
to it, one of whom entered last month and one four years ago.

That is the **second time this shape has come up** — RFC-041 hit it with a
spouse's pension escalating from the date of death, a quantity depending on
*when* a life entered a state rather than that it is there. Twice is a
pattern worth naming rather than rediscovering, and it is the multi-state
counterpart of the reduce-then-aggregate error VM-22 produced four times: a
structural limitation that looks like a modelling detail until it is stated.

The two honest workarounds, and what each costs:

- **Add a state.** An `exhausted` state with a transition out of each claim
  state gives the right *aggregate* run-off if the exit rate is calibrated —
  and it is memoryless where the real rule is a deterministic countdown.
  Nothing stops a caller declaring one, since the state names come from the
  transition matrix. This module will not calibrate it for them: a rate it
  invented would be a pool nobody bought.
- **Add a duration dimension**, splitting each claim state by time since
  entry. Exact, and it multiplies the state space by the pool length.

Neither is chosen. What is chosen is to say so — and **elimination periods**,
LTC's waiting period before benefits begin, are out for exactly the same
reason: a countdown from the claim date is not a property of the claim state.

## Premiums stop; benefits do not

`premium_years` is the **premium-paying** term, not the cover term. An LTC
policy is guaranteed renewable and pays for as long as the claim lasts, so a
limited-pay policy stops collecting long before it stops paying. That is the
opposite arrangement from `IncomeProtection`, where one `term_years` masks
both — and giving them one field would have made a limited-pay policy
inexpressible.

As there, the chain outlives the contract: states are never masked, because
the person does not cease to exist when the premium does.

## Acceptance

`tests/test_long_term_care.py` — 17 tests. Occupancy is conserved across all
four states exactly, for the whole projection, including after premiums stop.
`dead` is monotone and the claim states are not, which is the cheapest check
that this is a chain rather than a decrement model in a chain's clothing.
Recovery happens from *both* claim states, asserted on the flow rather than
on the arrows, since a matrix with arrows never traversed still conserves
occupancy.

`progression` — the `home_care → facility_care` flow — is asserted against
its own transition probability, because it is the variable that justifies the
second claim state: it turns a claimant drawing a fraction of a fraction into
one drawing the whole maximum.

The refusals: utilization outside `[0, 1]` on either claim state, a home-care
percentage above the facility maximum, a negative inflation rate, a misspelt
inflation mode, and a transition matrix over the wrong states.

Two existing guards fired on this commit, both correctly, and both are worth
recording because the fix was not the obvious one:

- `test_a_name_the_library_has_nothing_for_is_listed_not_matched` used
  `HOUSE_CODE_XQ` as a name the library had nothing resembling. `home_care`
  arrived, the stub scored **0.526** against a `WEAK` threshold of 0.5, and
  the "matches nothing" case quietly stopped being one. The machinery was
  right; the sentinel had expired. It is now a name that scores 0.25, and the
  **margin is asserted**, so a future template dragging the score toward the
  threshold fails there rather than silently turning the test into one about
  weak matches.
- `test_every_template_that_traces_from_a_common_point_settles` skipped
  templates needing a product-specific model point. A multi-state template
  needing a transition matrix in the *assumptions* is a second legitimate
  reason, so it is caught by message and named separately — any other
  `ValueError` is still a real failure and re-raises. The two multi-state
  templates are skipped for **different** reasons, which the test now pins:
  `IncomeProtection` fails first on its model point, `LongTermCare` gets as
  far as the assumptions because its setup needs nothing product-specific.
