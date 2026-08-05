# RFC-027: The adjustment, and the minus sign in front of Article 206

Status: **implemented** — `engine/report/scr.py`, `tests/test_scr.py`

## Summary

RFC-014 built the life underwriting stresses and RFC-026 the market risk
module. Both produce a *module* capital. This is the layer above, and it
closes the item RFC-014 named as out of scope in as many words:

> **The loss-absorbing capacity of technical provisions and deferred tax**
> (the "adjustment"), which needs the with-profits and tax structure of a
> specific fund.

RFC-019 built the with-profits structure and `engine/data/tax.py` has had a
tax basis since PLAN §5.1, so Article 103 of Directive 2009/138/EC can now
be written down in full:

    SCR = BSCR + SCR_operational + Adj

Three pieces: Annex IV's aggregation into a Basic SCR, Article 204's
operational charge, and Articles 205 to 207's adjustment. Commission
Delegated Regulation (EU) 2026/269 does not amend Articles 205 to 207, so
unlike RFC-026's market risk parameters this layer has **one** regime.

## The minus sign

Article 103 says the SCR is the **sum** of three items, one of which is the
adjustment — so the adjustment has to be negative for it to adjust anything.
It is:

    Adj_TP = − max(min(BSCR − nBSCR, FDB), 0)

That leading minus does not survive machine reading. In the consolidated
PDF's text layer the character comes through as a mojibake `Ä`, in exactly
the same position as the `Ä` standing for the minus inside `BSCR − nBSCR` —
so an extraction that resolves one and not the other produces a formula
that looks complete and is wrong. It had to be confirmed against the
original OJ L 12 typesetting, where both render as proper minus signs at
`x=116.49` and `x=184.68` on the same line.

Drop it and nothing raises. The SCR is simply wrong by twice the
adjustment, in the direction that looks prudent — which is the worst
possible direction for an error to be wrong in, because nobody
investigates a capital requirement that came out too big.

So `reconciles()` asserts the sign rather than the arithmetic: both
adjustments must be ≤ 0, and the total must be the sum of its parts. An
implementation that lost the minus fails it, which is asserted as a failure
rather than assumed impossible.

## The finding: absorption does not survive re-aggregation

Article 206(2) does not scale the Basic SCR. It recomputes every
scenario-based module allowing the shock to reduce future discretionary
benefits, and then **re-aggregates** — and a correlation matrix is not
linear in its inputs.

On a life fund whose modules are market 400, default 60, life 300, health
40:

| | gross | net | absorbed |
|---|---|---|---|
| market | 400.00 | 280.00 | 120.00 |
| life | 300.00 | 210.00 | 90.00 |
| default, health | 100.00 | 100.00 | 0.00 |
| **Basic SCR** | **592.79** | **427.55** | **165.24** |

The modules gave up **210**. The Basic SCR fell by **165.24**. The missing
**44.76 — 21.3% of the absorption — is eaten by the aggregation**, because
diversification had already discounted those modules and taking risk out of
them takes less than its face value out of the total.

A reviewer handed the module-level absorptions cannot reproduce the
adjustment from them. This is RFC-026's Article 164(3) finding one level
up: the aggregate is not a function of the parts in the way a reader
expects.

The control: with a single non-zero module there is no diversification to
discount and the absorption passes through **exactly**, gap 0.0.

## The finding: the two halves of the adjustment compete

Article 207(1) makes the deferred tax loss `BSCR + Adj_TP + SCR_op`, and
`Adj_TP` is negative. So relief already taken in the with-profits fund
shrinks the loss the tax line is allowed to absorb.

Measured across the range where neither clamp binds, at a 25% tax rate,
every unit of technical-provision absorption reduces the SCR by exactly
**0.75** — and at rate `t` by exactly `1 − t`, checked at 0%, 10%, 19%, 25%
and 40%:

| future discretionary benefits | Adj_TP | Adj_DT | SCR |
|---|---|---|---|
| 0 | 0.00 | −154.64 | 463.91 |
| 50 | −50.00 | −142.14 | 426.41 |
| 120 | −120.00 | −124.64 | 373.91 |
| 1,000 (uncapped) | −165.24 | −113.33 | 339.98 |

Article 205 says the adjustment is the sum of the two. It is — but they are
not independent, and a firm optimising one is spending the other. The
headline number a with-profits fund reports for loss absorption is worth
three quarters of itself.

## The finding: two things sit outside the aggregation, and it shows

**Intangible risk gets no diversification at all.** Annex IV point 1 adds
Article 203's charge — 80% of the intangible assets — *outside* the square
root. A hundred of intangible charge adds exactly **100.00** to the Basic
SCR. A hundred added to the health module adds **45.49**. The same capital
costs **2.2 times** as much when it is intangible risk, and that is a
structural choice in the formula rather than a calibration.

**The unit-linked expense term escapes the operational cap.** Article
204(1) is `min(0.3·BSCR, Op) + 0.25·Exp_ul`, and the second term is added
*after* the cap. On a small balance sheet that is not a detail: a Basic SCR
of 20 caps the charge at 6, and 400 of unit-linked expenses take it to
**106** — more than five times the Basic SCR the cap was measured against.
It is the one part of the standard formula's operational charge that
nothing limits.

**And the cap makes operational risk a function of the other risks.** Same
volumes, same processes, and the charge falls with the Basic SCR:

| Basic SCR | basic Op | charge |
|---|---|---|
| 400 | 60 | 60 |
| 200 | 60 | 60 |
| 100 | 60 | 30 |
| 50 | 60 | 15 |

A firm that de-risks its investments cuts an operational charge that has
not changed.

## The finding: a composite gets a fifth of its capital from two zeros

Annex IV point 1 puts 0.25 in every off-diagonal cell except three: default
against non-life is 0.5, and **life against non-life and health against
non-life are zero**. The standard formula asserts that a life book and a
non-life book at the same insurer share no risk whatever.

Written down as a number: the life fund above needs 592.79 and a non-life
fund needs 300.00. Together they need **720.69** rather than 892.79 — a
**19.3% saving** for putting them under one roof. Set those two cells to
0.25, the value every other off-diagonal cell takes, and the composite
would need **34.55 more**.

That is the single largest diversification benefit in the standard formula
and it rests on two cells that are an assertion rather than a calibration
anybody can inspect.

## Article 206's two clamps

`− max(min(BSCR − nBSCR, FDB), 0)` is a clamped difference and both kinks
are real.

The **floor at zero** means absorption cannot make the requirement worse —
which matters because the re-aggregation above can, in principle, produce a
net Basic SCR above the gross one.

The **cap at FDB** — technical provisions without risk margin in respect of
future discretionary benefits — limits relief to what the fund could
actually take away from policyholders. A fund with no discretionary
benefits gets **no relief at all** however far its liabilities would move,
and a fund whose absorption already exceeds its discretionary benefits gets
**nothing further** for absorbing more.

## Where the standard is judgement, the module takes the answer

Article 207(2a) lets an undertaking use an *increase* in deferred tax
assets only where it can demonstrate probable future taxable profit against
which it can be utilised, and 207(2c) sets conditions on the projection
that produces the demonstration — no new business beyond the planning
horizon and five years, forward rates unless better evidence exists,
haircuts increasing with distance. There is no arithmetic that settles it.

On the same fund the demonstration is worth a quarter of the requirement:

| Article 207(2a) demonstration | Adj_DT | SCR |
|---|---|---|
| not made | 0.00 | 608.54 |
| made in part (75) | −75.00 | 533.54 |
| made in full | −152.14 | 456.41 |
| deferred tax liability of 40 only | −40.00 | 568.54 |

So `DeferredTaxes` takes the recognisable amount as an **input**, in the
same way RFC-014 takes the risk margin's run-off driver. The module does
the clamping the article requires and does not pretend to do the
demonstration. Note the last row: a deferred tax liability already on the
balance sheet absorbs without anybody's opinion about future profit —
releasing it is arithmetic. Only the excess creates an asset, and only the
excess is clamped.

## Not in scope

- **Counterparty default risk** (Articles 189 to 202). It is a Basic SCR
  module like market or life, and this layer takes it as one.
- **Ring-fenced funds and matching adjustment portfolios** (Article 81 and
  Article 217), where the notional SCR of each fund is computed separately
  and diversification between them is not recognised. That is a real and
  material restriction on everything above, and it is its own piece.
- **Article 206(2)'s net modules are an input here.** Producing them means
  re-running every scenario with management actions under Article 23
  switched on, which is a projection question for the with-profits template
  rather than an aggregation question for this module.
- **Partial internal models** (Annex XVIII), whose integration techniques
  change how the Basic SCR is assembled and therefore how Articles 206 and
  207 apply.
- **The minimum capital requirement** (Articles 248 to 253), which is a
  separate calculation with its own floor and cap on the SCR.
