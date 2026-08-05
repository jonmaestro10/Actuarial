# RFC-007: LSMC proxies, and the error estimate that licenses them

Status: **implemented** — `engine/core/lsmc.py`, `scripts/benchmark_lsmc.py`

## Summary

PLAN.md §4.4 lists proxy models among the nested-stochastic tactics, with a
condition attached: *"as an optional, clearly-labeled acceleration with error
estimates"*. That condition is the design. A proxy is an approximation, and
an approximation whose error nobody has measured is indistinguishable from a
mistake.

So this arrives **after** RFC-006 rather than instead of it. The exact
nested valuation is the thing a proxy has to be checked against, and
`proxy_error` does exactly that.

## The trick

A full nested run values every outer node with enough inner scenarios to
make each value accurate on its own — hundreds. LSMC does the opposite: it
values every node with *very few* inner scenarios, accepts that each value
is nearly worthless individually, and recovers the answer by regressing
those noisy values on the state at the node.

The regression is what averages the noise. Each fitted coefficient is
informed by every node, so the surface can be far more accurate than any of
the values it was fitted to.

It regresses on state the **template itself declares**, through
`restart_fields` — so a proxy cannot be fitted on a quantity the model does
not carry, and asking for one raises with the list of what it does. For a
GMxB that is the fund value and the benefit base.

Values are fitted **per policy in force** and scaled back. A guarantee on
two policies is worth exactly twice a guarantee on one; making a polynomial
rediscover that would waste degrees of freedom on something already known.

## What it costs and what it is worth

Measured against a 1,000-inner-scenario reference on a GMxB contract, 400
outer paths, four valuation dates (`scripts/benchmark_lsmc.py`):

| inner | degree | error | worst date | speedup |
|---|---|---|---|---|
| 1 | 3 | 17.55% | 33.64% | 1000× |
| 2 | 3 | 10.00% | 20.58% | 500× |
| **5** | **3** | **2.08%** | **2.72%** | **200×** |
| 20 | 3 | 2.35% | 5.06% | 50× |
| 100 | 3 | 1.58% | 2.02% | 10× |

The useful setting is around five inner scenarios per node: **2% for 200×
less inner work**. Below that the surface degrades fast — one path per node
is not enough for a payoff with a kink in it, and the error estimate says so
rather than letting it pass.

Degree matters as much as sampling. Holding inner scenarios generous, so
that what is left is the basis rather than the noise: degree 1 gives 9.43%,
degree 2 gives 2.88%, degree 3 gives 1.84%, and further degrees buy little.
A linear surface cannot represent a guarantee payoff; a cubic nearly can.

## No in-sample statistic can tell you whether it worked

This is the finding worth carrying away, and it changed what this module
reports.

`residual_std` and `r_squared` describe how far the **noisy node values**
sit from the surface. How far the surface sits from the **truth** is a
different quantity, and the two bear no reliable relationship. Measured
across settings on this block the ratio ran from **0.11 to 1.84**, with no
pattern — the residual over-states the error at some settings and
under-states it at others.

The dangerous direction is the flattering one, and it is pinned by a test.
At degree 3, two inner scenarios per node produces a *lower* residual than
five — so an in-sample reading picks it as the better fit. Its surface is
**five times further out**.

I had originally documented the opposite: that the residual reliably
over-states the error because it is dominated by sampling noise. My own test
disproved it at the first settings I tried, which is why the numbers above
exist.

## A proxy cannot be measured better than its reference

The reference is itself a Monte Carlo estimate, so `proxy_error` reports its
error too:

- **`reference_noise_floor`** — from the reference's per-node standard
  errors. A lower bound, and knowingly a loose one: the nested driver values
  every node at a date against the *same* inner scenarios (RFC-006), so node
  errors are correlated and the whole surface shifts together rather than
  averaging out. On this block it reads 0.46% where the truth is 1.00% —
  understating by about half.
- **`reference_noise`** — the real thing, from a second nested run of the
  same shape on a different seed. Two independent 1,000-inner references
  differ by **1.00%** of the mean value. That costs a second reference to
  know, which is why it is optional and why the floor exists at all.

Quoting a 2% proxy error against a reference that is itself 1% uncertain is
the honest way to state it. Quoting it against nothing would not be.

## Not in scope

- **Neural surrogates.** PLAN §4.4 mentions them alongside LSMC. The
  measurement harness here is what they would have to clear, and it is
  indifferent to what produced the surface — `proxy_error` takes values, not
  a model.
- **Choosing the basis automatically.** Degree and state variables are
  arguments. Cross-validating them is a sensible next step and would need a
  held-out set of outer nodes, which changes the fit rather than the
  measurement.
- **Regressing on anything but declared state.** Deliberate: it is what
  keeps a proxy tied to the model rather than to whatever happened to be in
  scope when it was fitted.
