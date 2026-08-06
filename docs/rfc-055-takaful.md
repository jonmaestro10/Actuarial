# RFC-055: Takaful — a fee that is an option, and a loan that moves money between generations

Status: **implemented** — `engine/library/takaful.py`, `engine/api/examples.py`,
`tests/test_takaful.py`

## Summary

C6, the last of §10's C-items. The execution plan asks for:

> `engine/library/takaful.py` on the with-profits chassis. Model the
> participants' risk fund vs the shareholder fund, wakala fee and/or
> mudarabah share as declared `@var`s, surplus distribution to
> participants, and the qard hasan facility (shareholder loan to a deficit
> fund, repaid from future surplus). Golden tests from hand-computed
> miniature funds; the sharp-edge finding to look for: how the qard
> repayment ordering changes the split of surplus between generations of
> participants.

`FamilyTakaful` is a regular-contribution plan on the hybrid wakala–mudarabah
model. Every contribution splits three ways exactly — agency fee, savings,
donation — the risk fund is pooled and free to go negative, and the operator
lends it the shortfall when it does.

The with-profits chassis is the right structural analogue and it is worth
saying why precisely: the participant's investment account is a
retrospective per-policy accumulation like an asset share, the risk fund is
a pooled quantity nobody can attribute like an estate, and the distribution
rule is a management action like a bonus declaration. What is different is
*whose money each fund is*, and that difference is the entire product.

## Two fees, and the second one is not a fee

A takaful operator does not underwrite. It manages a fund the participants
collectively own, and it is paid two ways that are close enough to be
confused and are kept apart here for that reason:

- `wakala_fee` — an agency fee, a stated proportion of the **gross**
  contribution, taken up front. Ordinary, and genuinely a fee for work.
- `mudarabah_share` — a share of the fund's **investment profit**, as the
  managing partner of a capital somebody else supplied.

Both are carried as plain numbers, so pure wakala is the hybrid with the
profit shares at zero, pure mudarabah is the hybrid with the agency fee at
zero, and no template branches on which model an operator runs. A third
number, `operator_surplus_share`, is a performance fee on the *underwriting*
surplus at distribution — a different thing again from the investment share,
and named so it cannot be mistaken for it. RFC-054's estimation-versus-
prediction error is the worked example of that discipline; this is the same
move.

### The mudarabah share is a call option, and that is not a metaphor

A mudarib shares profit and does not share loss — the capital provider bears
a loss alone. So the operator's take is

    mudarabah_share × max(earned, 0)

and not `mudarabah_share × earned`. The consequence is one nobody quotes:
**hold the expected return fixed and raise the volatility, and the
operator's expected fee rises while the participants' expected return falls
by exactly as much.** The operator is long a call on the fund's return
struck at zero, and the participants are short it.

`tests/test_takaful.py` measures it on a two-point scenario set whose mean
return is exactly zero, so every number can be written down: at ±40% the
operator takes eight times what it takes at ±5%, on a contract whose terms
have not changed at all. That is the same shape as `MonthlySum`'s cap in
`engine/data/index_credit.py` — an asymmetry that is the economics of the
design and is invisible in any deterministic projection.

## The qard, and who pays for last year's claims

When the risk fund cannot meet its claims the operator lends it the
shortfall: **qard hasan**, interest-free by construction, repaid only out of
the fund's future surplus and ahead of any distribution to participants. If
the fund never generates one, the operator writes it off. There is no other
repayment route, which is why `qard_outstanding` at the end of a run-off is
the operator's realised loss and is a variable rather than a term buried in
the fund arithmetic.

The plan asked for the effect of the repayment *ordering*, and it is real: a
deficit in year three is funded by the operator and repaid out of year
seven's surplus, so the participants present in year seven pay for claims
incurred before some of them joined, and the participants who were present
in year three and have since left take none of it.

The ordering itself is not a choice — AAOIFI and the IFSB both require the
loan to be served before distribution — but the **size** of what it moves is
never measured. So the counterfactual is computed on purpose, in the habit
`vm22.floor_outside_reserve` and `MackResult.quadrature_total` set:

    surplus_if_qard_ignored − distributable_surplus

and `qard_transfer_to_participants` is the participants' share of it.
Neither feeds any cashflow.

**And the gap collapses to `distribution_rate × qard_repaid`, identically,
in surplus and in deficit alike.** That is asserted rather than derived on
paper, and it is the point rather than a simplification: the transfer does
not look like a payment from one generation to another *in any account an
operator keeps*. It looks like a slightly smaller distribution. A number
that is exactly a fraction of a line already on the face of the fund is
precisely the kind that never gets noticed, and naming it is the whole value
of computing it.

## The third instance of a limit, and a fourth

RFC-041 (a spouse pension escalating from the date of death) and RFC-042
(the LTC benefit pool) both ran into the same wall: a quantity that depends
on **when** a life entered a state, not merely that it is there, is not
expressible over states. The execution plan asked the next agent to watch
for a third.

Here it is, and it is not in a state chain at all. **The qard cannot be
attributed to the cohorts whose claims drew it.** The loan is a property of
the fund rather than of a participant; a repayment made at `t` cannot be
traced to the deficit at `s` that caused it, because the fund has no memory
of which period's shortfall each unit of qard covered, and the participants
who caused it may have left. The aggregate is reportable and the attribution
is not.

That it turned up in a pooled fund rather than in a Markov chain is worth
recording: the limit is a property of the **question** — anything that asks
"when did this arise" of a state that only records "what is true now" — and
not of the multi-state engine, which is where both earlier instances made it
look like it lived.

A fourth showed up while writing the tests, at the other end of the run.
**The risk fund outlives the participants.** Every contract runs off, the
fund still holds a balance, the distribution rule still releases a share of
it, and there is nobody with a claim on it. The contract does not say who
gets that and practice does not agree — some jurisdictions require the
residual to go to charity, some carry it to the next generation's fund, some
let the operator retain it. `unallocated_surplus` reports it and nothing
allocates it, which is the same choice `regdiff.py` makes about its named
residual and `vm22.Allocation` about the guarantees §13's own arithmetic
does not keep.

The allocation identity is therefore stated in two parts, and both are
asserted: what the participants are paid *plus* what could not be allocated
is what was declared for them, and nothing is stranded while a participant
remains.

## The executor equivalence class, stated and asserted

C3 established the practice: name the class in the RFC and assert it, rather
than assume it from the chassis. `FamilyTakaful` declares `@pool` variables
— the risk fund, the loan, the distribution — so it is in RFC-061's **block
class**:

- `run` over a block of more than one raises `PooledBlockError`, and the
  test says so;
- a block of **one** bridges into the per-policy class, and both executors
  agree bitwise on every variable;
- a pooled block is never chunked, and `chunk_size=1` gives bit-identical
  answers to the whole block.

It is also in RFC-068's **scenario class** whenever a scenario set is bound,
which the worked example is — a fund whose earned rate never moves is either
always in surplus or always in deficit, so a deterministic specimen would
demonstrate a with-profits fund with different words on it and would never
draw a qard at all. It is the second template in both classes, after
`VariablePayoutAnnuity`, and the single-scenario bridge is what says the
pooled reduction sweeps the block and not the slab.

## Why the fixtures are calibrated the way they are

Two of the golden fixtures exist to reach mechanisms a plausible fund does
not reach, and both say so in their docstrings rather than looking like
ordinary parameters:

- the **deficit block** is priced so far below its claims that it is in
  deficit from the first period and never recovers. It exercises the draw
  and the write-off, and it is the only way to see a qard at all on a flat
  mortality basis.
- the **bad-then-good scenario** is a −50% investment year on a well-priced
  fund, followed by recovery. It is the only way to reach a *repayment*: a
  fund in deficit because its tabarru' is too thin is in deficit for good.

That is itself worth stating, because it is the finding restated from the
other side. **A qard is repaid when the deficit was a market event and not
when it was a pricing error**, and an operator whose fund is structurally
under-priced is not making a loan at all — it is subsidising, permanently,
through a facility whose name says it expects to be repaid.

## What the worked example shows

48 lognormal paths at 22% volatility against a fund priced to run off at
about zero on the mean path. Roughly a third of the paths draw a loan and
two thirds of those repay it; a third finish with a balance outstanding.
Pooled and scenario-bound, so it runs under the stochastic executor alone —
which is exactly what RFC-068's third class was named for, and this is the
template that made the pair of classes overlap for the second time.
