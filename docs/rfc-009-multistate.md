# RFC-009: Multi-state Markov models

Status: **implemented** — `engine/data/multistate.py`,
`engine/library/income_protection.py`

## Summary

PLAN.md §5.2 asks for a multi-state Markov engine for the health and
protection family — critical illness, disability income, waiver of premium.
It is the step past RFC-004's multiple decrements, and the difference is one
word: **recovery**.

A decrement model is a one-way star. Everyone starts in one live state and
leaves it, and RFC-004 answers only which exit they take. A multi-state
model is a general graph: a healthy life can fall sick, a sick life can
recover, and both can die. Once a transition can run backwards, the
population in a state is no longer a decreasing sequence and the
survivorship framing stops working at all.

What replaces it is one matrix multiply per period — the Chapman-Kolmogorov
forward equation:

    occupancy(t + 1) = occupancy(t) @ P

## The DSL needed nothing new

Which was not obvious in advance. A template writes one `@var` per state and
the forward equation falls out as ordinary formulas:

```python
@var
def healthy(self, t):
    if t == 0:
        return self.mp.init_pols * 1.0
    return (self.healthy(t - 1) * self._p(t - 1, HEALTHY, HEALTHY)
            + self.sick(t - 1) * self._p(t - 1, SICK, HEALTHY))
```

Each state reads the previous period of every state that can reach it. The
dependency graph from RFC-001 handles it; no new primitive, no new executor.

## The invariant

**Rows sum to one.** Everybody in a state at the start of a period is
somewhere at the end of it, including still there. Checked on construction
rather than assumed, because a matrix whose rows sum to 0.999 loses a tenth
of a percent of the population every period and looks like mortality while
doing it — the error message names the offending state.

The consequence is what the templates are held to: **total occupancy is
conserved across all states for the whole projection**, measured at 8.9e-16.

`from_rates` fills the diagonal from what the transitions leave behind, and
**refuses** a stated diagonal. Quoting the stay-put probability separately
invites it to disagree with the row beside it.

## The bridge back to decrements

A generalisation has to contain what it generalises. A two-state chain with
an absorbing exit reproduces `(1 - q) ** t` survivorship to the last bit, and
its live state is monotone — so the multi-state engine contains the
decrement engine rather than sitting beside it.

With recovery switched on, the sick population rises, peaks and falls. That
is the sequence a running product cannot produce, and it is the whole
reason this module exists.

## Running it monthly, and the trap in that

An annual transition matrix does **not** become a monthly one by dividing.
The monthly matrix is the twelfth **matrix root**: the `M` with `M**12 == P`.

Element-wise division is not an approximation, it is wrong: on the sickness
matrix here, compounding the naive "monthly" matrix twelve times misses the
annual one by **5.6 percentage points** on a probability. It ignores every
path that leaves a state and returns inside the year, which is exactly what
a multi-state model exists to capture.

`Assumptions.periodic_transitions()` takes the root, caches it, and is the
annual matrix itself at `freq = 1`. A monthly projection then lands on the
annual one at every anniversary, because twelve monthly steps compose to
exactly one annual step — asserted directly.

## The embedding problem

Worse than the arithmetic: **a valid annual matrix need not have a valid
monthly one at all.**

The root from the eigendecomposition can come back with negative entries — a
probability below zero — or complex. When it does, no Markov chain on that
time step reproduces the annual matrix. This is the *embedding problem*, and
it is a property of the data rather than a numerical artefact.

It bites on entirely plausible sickness data, which is why it is worth a
section rather than a footnote:

| annual assumption | monthly root |
|---|---|
| 30% of the sick recover within a year | fine |
| 85% recover within a year | **negative probability**, refused |
| 98% recover within a year | **complex**, refused |

`TransitionMatrix.root` raises in both cases, with a message saying that the
annual matrix cannot be run at that frequency. Returning a clipped matrix
would produce a projection that looks fine and answers a question nobody
asked.

## `IncomeProtection`

The seed template: healthy, sick, dead, with premiums paid by the healthy
and a benefit paid to the sick.

**Waiver of premium is not a rider here, it is the model.** Premiums are a
cashflow of the healthy state, so a life that falls sick stops paying by
construction rather than by a separate benefit switched on beside the
sickness one.

**The chain outlives the contract.** States are not masked at the end of the
term — a policy's term is a property of its cashflows, not of the life, and
pretending the person ceases to exist would break the conservation invariant
that makes the model checkable. `in_term` masks the premiums and benefits
instead.

`recoveries` is an output, which no decrement template could produce.

## Not in scope

- **Deferred periods and benefit limitation**, which most real income
  protection carries. Both are expressible — a deferred period splits the
  sick state by duration since claim — and both belong in a second template
  rather than in the engine.
- **Semi-Markov models**, where a transition depends on how long the life
  has been in its current state. The state space grows a duration axis;
  nothing here forbids it, and nothing here does it.
- **Estimating a transition matrix from experience.** This layer runs a
  matrix; deriving one is the assumption-setting problem, upstream of the
  engine.
- **Sub-annual matrices supplied directly.** If the annual matrix does not
  embed, the honest fix is a monthly table rather than a root of an annual
  one, and the reader should be told that rather than handed a clip.
