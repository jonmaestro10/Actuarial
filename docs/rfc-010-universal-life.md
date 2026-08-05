# RFC-010: Universal life and the account-value family

Status: **implemented** — `engine/data/account.py`,
`engine/library/universal_life.py`

## Summary

PLAN.md §5.2 asks for **universal life / interest-sensitive** products with
"account-value mechanics, secondary guarantees", and the Phase 3 roadmap
pairs it with fixed-indexed annuities. This RFC covers the account side:
the charges that come out, the rate that goes in, the tax-law floor under
the death benefit, and the shadow account that keeps a contract alive after
its own arithmetic has killed it.

Every template before this one projects a benefit the policy document fixes
and asks what it costs. A universal-life account value is the other way
round: the benefit is whatever the account has become, and the account is
the running result of charges the insurer sets against a rate the insurer
credits. Two things follow that no earlier template needed.

## The contract can lapse from arithmetic

When the account cannot meet its own deductions, the policy leaves the book
on a date that is an **output** of the projection rather than an input to
it. This is the first template where a projected number decides whether a
policy still exists.

`in_force_av` is therefore **absorbing** — a running product, not a
per-period test. A contract that could not pay does not come back when
markets recover, and writing the indicator as a product makes that
structural instead of incidental.

It is also kept strictly apart from voluntary lapse. One surrenders an
account and is paid its cash value; the other walks away from an account
that is already empty. Merging them would pay out a cash value that does
not exist.

## The crediting floor is a written option, and it is a strip

A minimum guaranteed crediting rate is worth **exactly zero** in a
deterministic projection at any rate above it. `pv_guarantee_cost()`
returns `0.0`, not a small number.

Across a distribution it is worth a great deal, and the reason is that it is
not one option over the life of the contract but **one option per period**,
resetting every period whatever the account has already earned:

| portfolio volatility | floor bites in | uplift to the credited rate | cost, as % of PV(account) |
|---|---|---|---|
| 5% | 36% of periods | +123 bp a year | 1.2% |
| 10% | 44% | +323 bp | 3.1% |
| 15% | 48% | +527 bp | 5.0% |
| 20% | 50% | +733 bp | 6.9% |

(2% guaranteed rate, 1% spread, 5% expected return, 2,000 scenarios.)

At 10% volatility the floor roughly **doubles the account** over thirty
years relative to the unfloored path. A deterministic valuation of the same
contract prices that at nothing. This is the reason the family belongs in
the stochastic executor rather than beside it.

`CreditingBasis` therefore ships two modes rather than one. A **declared**
rate credits what the insurer announces and prices no option at all — its
stochastic and deterministic values differ only through the benefits. A
**portfolio** rate credits what the assets earned less a spread, floored,
and is where the option lives. The two coincide exactly when returns are
flat at `current + spread`, which is the check on the pair.

## The corridor is not optional, and leaving it out is not conservative

US tax law will not let a contract be all savings: under IRC §7702 the
death benefit must stay a stated multiple of the account value, falling
from 250% under age 41 to 100% at 95.

Without it, a contract funded hard enough for the account to pass the face
amount has a **net amount at risk of zero** — and therefore pays no cost of
insurance at all — for the rest of its life. Measured on a 40-year contract
issued at 35: the net amount at risk hits zero in year 10 and stays there
for thirty years. The model shows three decades of free cover.

With §7702 the same contract carries around £150,000 of net amount at risk
throughout, and the present value of death claims is **4.2 times** the
figure without it. A corridor omitted "for simplicity" understates both the
benefit and the charge that pays for it.

The table is reproduced in full in `SECTION_7702`, because a corridor that
is *nearly* the statutory one fails the test it exists to pass.

## Order of operations, and the one alternative that is a mistake

Within a period: premium in, load off, policy fee out, **death benefit
struck**, net amount at risk and COI charged on it, deductions capped at the
account, interest credited on what is left.

Striking the death benefit *before* the policy fee is a defensible
alternative that changes real numbers, and the order above — the textbook
monthly deduction sequence — is a stated convention rather than an
inevitability.

Striking it *after* the COI is not an alternative, it is circular. The
engine's cycle detector from RFC-001 says so by name:

```
av_after_charges depends on itself within one period:
  av_after_charges(0) -> charges_due(0) -> coi_due(0) -> nar(0)
  -> death_benefit(0) -> av_after_charges(0)
```

rather than iterating to a fixed point nobody asked for. That is what makes
"fee, then benefit, then COI" a checkable statement about the template
instead of a comment in it — and it is the first time the cycle detector has
caught something an actuary might plausibly write.

## The secondary guarantee, and why it is worth so much

A no-lapse guarantee runs as a **shadow account**: a second, notional
account rolled forward on its own terms, usually a better crediting rate
against cheaper charges. Nothing in it is ever paid to anybody. It produces
one number, a yes or no on whether the policy still exists, which is why it
carries no corridor and no surrender charge — neither concept applies to an
amount that cannot be received.

The measured effect is larger than "the contract lasts longer". On a level
premium against rising mortality, the account exhausts at age 80:

| | without the guarantee | with it |
|---|---|---|
| contract lapses | year 25 (age 80) | runs the full 40 years |
| PV of death claims | 48.0m | 69.2m (**+44%**) |
| of which paid on an empty account | — | 23.0m |

The lapse the guarantee prevents does not happen at a random time. Rising
mortality against a level premium exhausts the account **precisely at the
ages the death benefit is most likely to be claimed**. So the guarantee is
not a marginal extension of a contract; it restores the half of the cover
that the product's own arithmetic was about to destroy.

It can also fail on its own terms, and does so correctly. Charge the shadow
account more than the stated premium supports and it drains in year 4 while
the real account lasts to year 25 — so by the time the guarantee is needed
there is nothing left of it. That is the mechanism by which a no-lapse
guarantee lapses, not a modelling failure.

## Running sub-annually

Three separate effects, measured separately because they point different
ways and only one of them is plumbing:

- **The crediting conversion is not a bitwise identity.** `(1 + g) ** (1/12)`
  compounded twelve times misses `1 + g` by about **five ulps**, which
  reaches a relative 3e-14 over 25 years. `freq = 1` *is* exact — it
  short-circuits without evaluating a power — and the difference between
  "exact at freq = 1" and "exact across freq" is worth stating rather than
  glossing.
- **An annual step invests the whole year's premium on day one**, and earns
  a year's interest on all of it. Monthly, most of it arrives later: the
  account ends **2.0% lower**. Neither is an approximation of the other —
  they are different contracts, and the annual one is the one that does not
  exist.
- **A finer step collects less from policies that leave mid-year.** At an 8%
  lapse rate, charge income falls **5.4%** going from annual to monthly,
  because an annual step charges a full year to a policy that surrendered in
  March. This is the same finding the unit-linked template made at −3.3%,
  reproduced on a different charge structure.

## The one default that is not neutral

`AccountBasis()` takes no load, charges no fee, applies no corridor and no
surrender charge, credits nothing and carries no secondary guarantee.

**The cost of insurance is the deliberate exception** — it defaults to one
times the mortality basis, expected mortality with no margin. The
neutral-looking alternative, a zero COI, is not a neutral default but a
nonsense product: free life cover, an account growing without paying for the
risk it carries, and a projection that would look plausible while answering
nothing. Nothing predates this module, so there is no earlier result for a
zero default to protect, and a default that must be overridden before the
model *means* anything is worse than one that must be overridden before it
*earns* anything.

Every existing template is unaffected: 50 output series across term life,
unit-linked and the fixed annuity are bitwise identical before and after.

## Not in scope

- **Fixed-indexed crediting** — caps, participation rates, point-to-point
  versus monthly-sum, annual reset. It is the next crediting rule to slot
  into `CreditingBasis`, and the account mechanics here are already the ones
  an FIA needs.
- **Dynamic policyholder behaviour.** Premium payment on a universal-life
  contract is flexible by design, and a policyholder who stops paying when
  the account is healthy is a real and modellable phenomenon. This template
  projects the premium the model point states; a behavioural rule belongs
  beside `DynamicLapse`, not inside the roll-forward.
- **Guaranteed maturity premium tests and the §7702 premium limits.** The
  corridor is the death-benefit side of §7702; the premium side (guideline
  level, guideline single, seven-pay/MEC testing) is a compliance
  calculation on the policy, not a projection variable.
- **Surrender charges expressed per 1000 of face amount.** A different
  quantity from a percentage of the account, and one that belongs in a
  template reading the face amount rather than in a factor applied to the
  account.
- **Reserves.** `account_value` is the account, which is the liability this
  contract carries before any statutory strengthening. CRVM, VM-20 and
  IFRS 17 measurement of it are the §5.3 overlays.
