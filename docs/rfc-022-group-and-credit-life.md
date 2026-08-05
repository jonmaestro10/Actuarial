# RFC-022: Group and credit life

Status: **implemented** — `engine/data/loan.py`, `engine/library/credit_life.py`,
`engine/library/group_life.py`

## Summary

PLAN.md §5.2's last unbuilt line: "**Group & credit life**". Two products
that look like simple term assurance and are not, for opposite reasons.

**Credit life** is a term assurance whose sum assured is written down by
somebody else's schedule and whose premium is paid once, at the start. So
its liability is a *premium* measure rather than a benefit measure, and its
largest question is not what the cover costs but what happens to the
premium when the borrower settles early.

**Group life** is one contract over many lives, where the interesting
cashflow is not a claim at all but the experience refund — and that refund
turns out to be an option, of exactly the shape RFC-010 found under a
crediting floor and RFC-020 put a line in the accounts for.

## The finding: the Rule of 78 is exactly right, on a product nobody sells

Three ways to work out how much of a single premium is unearned at
duration `k` of `n`:

| basis | formula | unearned per unit of |
|---|---|---|
| pro rata | `(n−k)/n` | time |
| Rule of 78 | `(n−k)(n−k+1)/(n(n+1))` | remaining instalment count |
| sum at risk | share of total outstanding balance still to run | **cover** |

At a **zero** interest rate the balance amortises in a straight line, and
then the sum-at-risk answer *is* the Rule of 78 — the same expression, not
an approximation of it. They agree to one ulp (1.1e-16 across a sixty-period
loan, bit for bit on short ones), and both endpoints are exact.

That is what the rule was built for: flat, interest-free, add-on credit.
It stops being correct the moment the balance stops running off in a
straight line, and every real loan does. At a positive rate the balance
falls more slowly than linearly, so more risk is left than the rule admits
and the borrower is short-changed by the difference:

| loan rate | worst shortfall | at period |
|---|---|---|
| 0% | 0 | — |
| 12% nominal | **2.82%** of the whole premium | 21 |
| 24% nominal | **5.31%** | 21 |

Not the mid-point — asserted and wrong, then measured. The two curves are
not symmetric about it.

### Pro rata is not the fair alternative

The comparison usually made against the Rule of 78 is against pro rata, and
its maximum is exactly `n / (4(n+1))` — **24.59%** of the whole premium on a
sixty-period loan, at the midpoint, approaching a quarter of everything the
borrower paid. It is also the wrong comparison, because pro rata over-refunds:
a decreasing term assurance is not half used up half way through its term.

The order is fixed and provable for any declining balance:

    rule of 78  ≤  sum at risk  ≤  pro rata

On a thousand-policy book at 15% settlement rates, the three bases refund
**123,141**, **130,333** and **177,477** of a 700,000 gross premium.
Moving from the Rule of 78 to the basis the cover actually justifies hands
borrowers 1.03% of gross premium and costs the book 12% of its net cash.
Moving to pro rata removes **91%** of it. One of those is a correction; the
other is a different and larger error pointing the other way.

`refund_basis` is a class attribute, not an assumption, because a refund
basis is a **contract term** — the policy document says how it is worked out
and two otherwise identical books are sold on different ones. Same typing as
RFC-019's bonus basis, for the same reason.

## Credit life: what has to be a template rather than a parameter

**The premium is single, so the reserve is the unearned part of it.** The
invariant this template is checked against is that every unit of premium is
either earned or given back — nothing else may consume one — and it holds to
1e-12 on all three bases.

The unearned premium reserve is *not* the benefit reserve, and it is worth
seeing how far apart they are on the same contract. Against the present
value of future claims and refunds:

| duration | 0 | 1 | 2 | 3 | 4 |
|---|---|---|---|---|---|
| UPR / prospective value | 1.149 | 1.142 | 1.134 | 1.126 | 1.118 |

A premium-based liability carries the loading; a benefit-based one does not.
Which one a regulator wants is the question, and answering it with the other
one is a 12–15% error in a single direction.

**A death earns the rest of the premium.** The claim is paid in full and
there is no refund on top, so the whole unearned balance on a dying life
falls into income at once. A settlement does the reverse. The two decrements
therefore cannot be netted, which is why they are separate outputs.

**The sum at risk is a closed form in `t`.** `P · a_{n−t|} / a_{n|}`, so the
batch executor evaluates one expression across model points that each have
their own principal, rate and term — no schedule object per policy. The
zero-rate limit is taken rather than approached, because 0% finance is a
real product and the naive expression is `0/0` on it. It is a `@var` so an
interest-only or balloon loan is an override rather than a rewrite.

## The finding: an experience refund is an option, and it is priced at zero

A profit-sharing group scheme returns a share of any surplus at the end of
each rating period and charges nothing back when the experience is bad. That
floor is the whole economics:

    deterministic cost  =  share × max( E[surplus], 0 )
    actual cost         =  share × E[ max(surplus, 0) ]

Jensen's inequality on a convex payoff, so the second is strictly larger
whenever the death count is uncertain — which is always.

The gap has a **closed form** here rather than a simulation. Claims on `n`
lives of equal cover are `S × Binomial(n, q)`, so the expectation is a finite
sum over the death count: no scenarios, no seed, no Monte Carlo error.

Priced at exactly expected claims, on 100 lives at 200,000 of cover and 0.4%
mortality with a 50% share:

| | |
|---|---|
| deterministic refund | **0** |
| actual expected refund | **26,791** |

The best-estimate projection reports that the profit share costs nothing.
It never costs nothing. Even priced *below* expected claims — a 90% loading,
a scheme the insurer expects to lose money on — the deterministic refund is
still exactly zero and the real one is 24,112.

### And the small schemes are the expensive ones

Option value rises with volatility, and a small scheme's claims ratio is the
volatile one. At a 25% loading:

| lives | option value per life | uplift over the deterministic cost |
|---|---|---|
| 25 | **352** | **352%** |
| 50 | 309 | 309% |
| 100 | 235 | 235% |
| 250 | 120 | 120% |
| 1,000 | 41 | 41% |
| 5,000 | **7** | **6.6%** |

Fifty times more per life on a twenty-five-life scheme than a five-thousand
one. And a twenty-five-life scheme is precisely the one whose own experience
carries no credibility — the schemes with the weakest case for experience
rating are the ones where granting it costs the most. Nothing in the code
knows that; it falls out of the binomial.

## Group life: the pool is the point

`scheme_margin`, `surplus_carried`, `experience_refund` and `insurer_result`
are `@pool` variables. A refund is struck on the **scheme**: one member's
claim is met out of everybody's premium, which is a transfer between model
points and the thing a per-policy formula cannot see. This is the second
template to need `@pool` for what RFC-001 introduced it for, after RFC-019's
asset share, and pooled models go through the batch executor for the reason
RFC-019 records — the interpreted runner builds one instance per model point,
so a reduction there would pool a block of one.

The surplus accumulator resets on the strike rather than on the calendar:
the balance is multiplied by `1 − strike` at the *previous* period, so the
period that pays a refund starts from nothing. A deficit is not carried into
the next rating period either — that is the same floor, seen from the other
side.

**Cover follows salary, not a sum assured.** `sum_assured` is a `@var` in
`t`, so a scheme's exposure grows every year without a new member and without
anybody underwriting anything.

The **retained margin** comes out of the premium before the pot is struck,
which is why a scheme running at precisely its expected claims produces no
refund at all — and is the reason an insurer is willing to write a profit
share in the first place.

## Not in scope

- **Free cover limits.** Cover below the limit is accepted without evidence
  and above it is underwritten, so the mortality basis is a property of a
  *slice of cover* rather than of a person. Expressible as a blended rate;
  it is a pricing convention rather than machinery, and asserting a direction
  for the anti-selection loading is not something this RFC can measure.
- **Stochastic claims inside the projection.** :func:`refund_option_value`
  measures the option exactly on a single rating period of equal covers. A
  scheme of unequal covers over several periods is RFC-006's nested machinery,
  and the answer would be sampled rather than summed.
- **Credibility weighting.** The tension is measured here and not resolved:
  blending a scheme's own experience with the book's is a standard formula
  this module does not apply.
- **Rate reviews as a management action.** The unit rate is a model point
  field, so a review is a new projection rather than a decision the model
  takes. RFC-019's `declared_bonus` is the shape a rate rule would take.
- **Terminal illness, spouse and dependant benefits**, all of which are
  further benefits on the same decrements.
- **The lender as the policyholder.** Credit life is often written with the
  lender as beneficiary and the borrower as the life assured, which changes
  who the refund is owed to and nothing in the arithmetic.
