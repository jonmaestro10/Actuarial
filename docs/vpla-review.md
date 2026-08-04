# VPLA repository review

Structural review of `jonmaestro10/VPLA` (HEAD `fe8b47f`, last pushed
2022-07-21) — the prior-art variable payout life annuity system named in
[PLAN.md §10.1](../PLAN.md) as reference model #1 for the VA/VPLA library.

Reviewed at commit `fe8b47f` against engine `main` (Phases 0–2).

---

## 1. What it is

VPLA is a **working production system for one product**: a variable payment
life annuity (VPLA) pool, of the kind Canadian registered plans use — members
contribute to a pooled fund, each member's pension is their account value
divided by an annuity factor, and every valuation the whole pool's pensions
are scaled by the ratio of realized to assumed experience.

It is not a projection engine. There is no time axis, no scenario dimension,
and no cashflow projection anywhere in the repository. It computes **one
valuation at a time** and persists the result: a monthly ETL over MySQL,
driven from a Dash UI, with a thin AWS wrapper.

That distinction matters for how it should be reused. VPLA's *actuarial
primitives* — discount factors, survival curves, annuity factors, the joint
and certain variants — are directly reusable as reference implementations.
Its *architecture* is the thing the engine exists to replace.

## 2. Repository map

| Path | Lines | Role |
|---|---:|---|
| `application/rate_table.py` | 114 | `RateTable`: payment frequency, per-period rate vector, discount and accumulation factors |
| `application/mortality_table.py` | 360 | `MortalityTable`: table + improvement-scale lookup, fractional-age (UDD/linear) mortality over a period, cumulative survival curves |
| `application/person.py` | 276 | `Person`: single-life, joint-life, reversionary and certain-period annuity factors |
| `application/calculation_engine.py` | 252 | `CalcEngine`: monthly account-value roll-forward and the valuation-date pension adjustment. **The product logic.** |
| `application/fund.py` | 247 | `Fund`: DB-backed monthly pooled balance/cashflow, derived monthly return; `FundValidation` reconciles fund vs member totals |
| `application/datahandler_mysql.py` | 313 | `DataInput` / `DBRetrieve`: DDL, CSV load, current-vs-previous valuation joins |
| `application/data_connector.py`, `aws_connection.py` | 180 | MySQL/RDS credentials and connection context managers; boto3 session factory |
| `application/user_tracking.py` | 193 | Cognito auth |
| `application/account.py`, `plan.py`, `reconcile.py` | 71 | Stubs and a historical-returns loader; `Plan` and `ReconcileFunds` are unimplemented |
| `pages/*.py`, `app.py` | ~1200 | Dash multi-page UI (calculator, DB load/view/validate, fund entry, valuation run) |
| `cdk/` | 5 files | CDK stacks (API Gateway, Cognito, RDS) + two annuity-factor Lambdas |
| `tests/` | 999 | 3 modules of golden annuity-factor values with fixtures |
| `data/` | 18 files | CPM2014 + UP94 tables and improvement scales (CSV/JSON/XLS), sample member and fund extracts |

Roughly 3,200 lines of Python, of which the actuarial core is about
**750 lines** (`rate_table` + `mortality_table` + `person`) and the product
logic about **170 lines** (`CalcEngine`). Everything else is persistence,
UI, auth, and infrastructure.

## 3. The actuarial core

### 3.1 Discount factors — `RateTable`

`rates` is supplied as a list of annual effective rates, one per payment
period; the validator expands it to a dense array covering `120 * freq`
periods, repeating each supplied rate `freq` times and holding the last rate
flat to the end. Discount factors are a forward recursion:

```
df[0] = 1
df[i] = df[i-1] * (1 + rates[i-1]) ** (-1 / freq)
```

So `rates` are annual effective and discounting is per period. There is no
term structure interpolation: `convert_freq` re-samples by repeating the
annual rate rather than reconstructing from the discount curve, which its
own docstring flags as a known limitation.

### 3.2 Mortality — `MortalityTable`

Three layers, all keyed by integer age:

1. **Table lookup** (`mortality_lookup`) — `q_x` by age and sex, with the
   last tabulated age held flat beyond the end of the table. Optional
   blending across sexes by a fixed male proportion.
2. **Improvement** — supports both a **1-D constant scale**
   (`q * (1 - imp[age][sex]) ** (year - year_start)`) and a **2-D
   generational scale** (`q * Π_{y} (1 - imp[sex][y][age])` over calendar
   years, holding the last tabulated year flat). Dimensionality is detected
   at runtime from the nesting depth of the loaded dict.
3. **Fractional age** (`mortality_period`) — probability of death over one
   payment period starting at an arbitrary date. Splits the period across
   the two ages it straddles by day count (actual, or 30/360 when
   `actual_daycount` is false) and combines them either linearly or under a
   **UDD** conditioning:

   ```
   udd:    q_period = pct_within_first / (1 - q_first * pct_before_first) * q_first
                    + pct_second * q_second
   linear: q_period = pct_first * q_first + pct_second * q_second
   ```

   The UDD form is the correct conditional statement — it re-bases the
   first age's rate on survival to the start of the period. Attained age
   ≥ 120 returns `q = 1`.

`survival_factors` then builds a cumulative survival vector of length
`120 * freq`, one `mortality_period` call per period.

### 3.3 Annuity factors — `Person`

| Method | Formula | Notes |
|---|---|---|
| `annuity_factor` | `Σ_k v_k · ₖp_x / freq` | Optional certain period: survival is overwritten with 1 for the first `freq · certain_years` periods, which is the correct life-and-certain factor |
| `annuity_factors` | `Σ_{j≥k} v_j · ⱼp_x` for each `k` | Present values at time 0 of payments from period `k` onward (used by the joint calculation) |
| `annuity_factors_future_value` | previous, divided by `v_k` and `ₖp_x` | Value per surviving life at `k` |
| `joint_annuity_factor` | see below | Reversionary (contingent survivor) annuity |
| `joint_life_factor` | `Σ_k v_k · ₖp_x · ₖp_y` | Joint-life status; docstring correctly notes it differs from SOA's blended-`q` convention |

The reversionary calculation is worth stating precisely, because it is the
piece the VA library most wants. It accumulates, over each period `i`, the
probability that the primary annuitant survives to `i-1` and dies in the
period, times the joint percentage, times the time-0 value of the survivor's
payments from `i` onward:

```
total[i] = ₍ᵢ₋₁₎p_x · q_x(i-1) · j · Σ_{k≥i} v_k · ₖp_y
factor   = ( Σ_k v_k · ₖp_x + Σ_i total[i] ) / freq
```

**This is algebraically the textbook reversionary annuity.** Exchanging the
order of summation gives `Σ_k v_k · ₖp_y · (1 - ₖp_x)`, so

```
joint_annuity_factor = ä_x + j · (ä_y - ä_xy)
```

under independent lives. Verified numerically against the CPM2014 table at
3%: the loop and the closed form agree to **0.0 — bitwise identical**. The
implementation is O(n²) where the closed form is O(n), but it is correct.

### 3.4 Verification performed

The four published golden values in `tests/test_single_zero_rate.py` were
reproduced from `data/CPM2014.json` with an independent 12-line
reimplementation of §3.1–3.3 (annual frequency, valuation date on the
member's birthday, where the UDD split collapses to the tabular `q_x`):

| Case | VPLA golden | Reproduced |
|---|---|---|
| male 65, i = 0% | 21.3327 | 21.3327 |
| male 75, i = 0% | 13.4418 | 13.4418 |
| male 85, i = 0% | 7.1221 | 7.1221 |
| male 65, i = 3% | 15.4794 | 15.4794 |

The core is understood and is correct on its own terms. That reading is what
`tests/vpla_reference.py` in this repository now encodes, and what
`tests/test_vpla_reconciliation.py` reconciles the engine against.

## 4. The product logic — `CalcEngine`

Two paths, both operating on a wide DataFrame of members joined to their
previous month's row.

**Between valuations** (`update_values_no_valuation`) — a carry-forward:

```
account_value = (account_value_old - monthly_pay_old) * (1 + fund_return)
                + additional_contribution
monthly_pay   = monthly_pay_old + additional_contribution / annuity_factor / freq
```

Pensions in payment are unchanged; new money converts to pension at the
member's own annuity factor. Deceased members drop to zero pay.

**At a valuation** (`update_values_valuation`) — the variable-payment step:

```
retrospective  = rolled-forward account value          (what the pool earned)
prospective    = monthly_pay · annuity_factor · freq    (what the pool owes)
adjustment     = Σ retrospective / Σ prospective - 1    (pool-wide, one number)
monthly_pay   := (1 + adjustment) · monthly_pay_old     (existing members)
                 + additional_contribution / annuity_factor / freq
account_value := monthly_pay · annuity_factor · freq
```

Three properties follow, and they are the heart of the product:

1. **The adjustment is a pool-level reduction.** It is a ratio of two sums
   taken *across all members* and then applied *back to every member*. No
   member's pension can be computed from that member's own data alone.
2. **The pool is self-balancing by construction.** After the adjustment,
   `Σ prospective = (1 + adjustment) · Σ prospective_old = Σ retrospective` —
   assets equal liabilities exactly at every valuation, which is what makes
   the product a pure pass-through of investment and mortality experience.
3. **Account value changes meaning across the step.** It is retrospective
   (rolled-forward assets) between valuations and prospective (a reserve)
   immediately after one.

`return_adjustment` and `mortality_adjustment` decompose the change into
investment and mortality sources. They are attribution outputs only — nothing
downstream consumes them.

## 5. Mapping to the engine

| VPLA | Engine equivalent today | Status |
|---|---|---|
| `RateTable.discount_factors` | `v(t)` as a `@var`, flat `assumptions.interest` | **Narrower** — engine has no term structure |
| `MortalityTable.mortality_lookup` | `MortalityTable.q_at` / `clip_age` | Comparable; engine has no improvement scales |
| `MortalityTable.mortality_period` | — | **Missing** — engine is integer-age annual only |
| `MortalityTable.survival_factors` | `pols_if(t)` recursion | Equivalent for annual steps |
| `Person.annuity_factor` | `FixedAnnuity.pv_payments()` in the payout phase | Equivalent (reconciled in the new test module) |
| `Person.joint_annuity_factor` | — | **Missing** — needs a second life on the model point |
| `Fund` monthly return | `ScenarioSet.ret(t)` | Engine is strictly better: `Fund` derives one realized return from a DB balance; the engine carries `n_scenarios` |
| `CalcEngine` roll-forward | `@var` recursion over `t` | Engine is strictly better: declarative, cached, vectorized |
| `CalcEngine` pool adjustment | — | **Missing, and not currently expressible** — see §7 |
| `np.around(..., 2)` inside the recursion | — | Deliberately absent; see §6 |
| MySQL round trip per step | in-memory arrays | Replaced |

## 6. Defects and risks found

Listed because they are the failure modes the engine's golden-test discipline
exists to prevent, not as criticism of a working system.

**Correctness**

1. `Person.joint_survivor_none` (`person.py:53`) returns `0.0` when the value
   is falsy and **falls off the end returning `None` otherwise**. Under
   pydantic v1 the validator's return value replaces the field, so *every
   non-zero* `joint_survivor_percent` becomes `None`. `joint_annuity_factor`
   then fails its `== 0` guard and raises `TypeError` on `None * ndarray`.
   The joint path is unreachable with a live spouse benefit.
2. `RateTable.rates_list` (`rate_table.py:24`) has the same shape: it returns
   the converted array only when `rates` is a `list`, so constructing a
   `RateTable` from an `ndarray` silently sets `rates = None`.
3. `MortalityTable.mortality_lookup` binds `result` only inside the
   `dict_depth == 2` and `== 3` branches — any other depth raises
   `UnboundLocalError`. An empty improvement dict with `use_improvement=True`
   raises `ValueError` from `max()` on an empty sequence inside `dict_depth`.
4. `use_blended_rate` blends the base `q_x` across sexes but still applies
   the **sex-specific** improvement scale, so a blended rate is improved
   inconsistently.
5. `annuity_factors_future_value` divides by `survival_factors`, which reach
   exactly `0` at the end of the table — silent `inf`/`nan` in the tail.
6. `annuity_factor`'s certain-period slice `survival_factors[0:round(freq *
   certain_years, 0)]` only works because both operands are `int`; a float
   `certain_years` makes `round(..., 0)` return a float and the slice raises.
7. `np.around(..., decimals=2)` is applied **inside** the roll-forward
   recursion. Money rounding is a presentation policy; embedding it in the
   recursion makes results depend on payment frequency and destroys the
   possibility of bitwise reconciliation.
8. **The pool does not balance when contributions arrive at a valuation.**
   Contributions are in the adjustment's denominator (`prospective` is built
   from the pension *after* the new money has been converted) but the
   adjustment is then applied only to `monthly_pay_old`. Writing `C` for
   total contributions and `α` for the adjustment,

   ```
   Σ prospective_new = Σ retrospective - α · C
   ```

   so §4's self-balancing property holds exactly only when `C = 0` or
   `α = 0`. Either put new money in both the numerator and the denominator
   or in neither; as written it absorbs part of the period's experience
   without receiving it. `tests/test_vpla_reconciliation.py` asserts this
   identity exactly, so the size of the leak is measured rather than
   assumed.
9. Latent double count in the same step: for a member with
   `monthly_pay_old == 0` the `if monthly_pay_old == 0` branch keeps the
   pension already computed by `update_values_no_valuation` — which
   *includes* `additional_contribution / annuity_factor / freq` — and the
   following `account_value_old > 0` block then adds that same term again.
   It is unreachable for a genuinely new member (`account_value_old == 0`)
   and harmless for a deceased one (zeroed immediately after), but a member
   holding an account value with no pension in payment converts their
   contribution twice.

15. **The fractional-age split is not a probability** — but it does not
   matter in practice. When a payment period straddles a birthday,
   `mortality_period` *adds* the two parts:
   `pct_first/(1 - q_first·pct_before)·q_first + pct_second·q_second`. Past
   roughly `q = 0.8` that sum exceeds 1, so `1 - q` goes negative and
   `survival_factors`, being a cumulative product, goes negative with it.
   `data/CPM2014.json` reaches `q = 1.0` at its last tabulated age (115) and
   holds it flat above, so the condition is reachable.

   Measured on CPM2014 rather than argued, because the arithmetic looks
   worse than it is:

   | Valuation | Survival curve |
   |---|---|
   | On the member's anniversary | Reaches **exactly zero** at age 116 and never goes negative — `q = 1` kills the cohort before the split can overflow |
   | Off anniversary | Survival is already ~1e-8 by the time the overflow bites; the negative excursion peaks at ~1e-10, and the curve reaches exactly zero at the limiting age regardless |

   The effect on an annuity factor is **~1e-9 relative at worst** (annual,
   4%; ~2e-10 monthly), which is below the summation-order differences
   already accepted between the engine and the original. So this is a
   latent-correctness note, not a material error: nobody's factor is wrong
   because of it.

   The correct combination is
   `1 - (1 - q_first)^{pct_first} · (1 - q_second)^{pct_second}`, or the
   equivalent conditional product; the additive form is only valid while
   both parts are small, which at every age that carries any weight they
   are. **Changing it is not worth moving SOA-validated numbers for.** The
   engine reproduces the rate exactly and clips only the accumulated
   survival, so a probability cannot leave the basis outside [0, 1] — see
   docs/rfc-002-basis.md.

**Governance and operability**

10. `CalcClass.update_annuities` / `update_account_values` wrap the entire
   calculation in `except Exception: print(e); return False`. A failed
   valuation is indistinguishable from a partially-written one, and the
   traceback is lost.
11. `print()` in the calculation path (`calculation_engine.py:123` prints the
   pool adjustment) is the only record of the single most important number
   the system produces.
12. Mortality tables are fetched from S3 (`vpla-code` bucket) inside a
    pydantic **validator**, so constructing a `MortalityTable` performs
    network I/O and the test suite cannot run without AWS credentials — the
    committed golden tests are not reproducible offline.
13. There is no run registry: a valuation overwrites the member table for
    that date. The inputs that produced a given pension cannot be recovered.

**Performance**

14. `survival_factors` allocates `120 * freq` periods regardless of attained
    age and calls `mortality_period` — with `relativedelta` date arithmetic —
    once per period, per person, per call. `joint_annuity_factor` then builds
    the full `annuity_factors` vector (itself O(n²)) on top. Annuity factors
    are recomputed per member in a Python `iterrows()` loop.

## 7. What this opens up in the engine

Two findings are architectural, not incidental.

### 7.1 The pool adjustment is a cross-model-point reduction

> **Resolved.** The DSL gained a `@pool` variable kind (RFC-001), and
> `engine/library/variable_payout_annuity.py` is the product built on it —
> reconciled against `valuation_step` in the reference below, and holding
> both defining properties of the pool exactly. The paragraph that follows
> is the original finding, kept because it is what motivated the design.


Every `@var` in the current DSL is a pure function of `t`, *this* model
point, assumptions, and other variables of the same model. The VPLA
adjustment is `Σ_members retrospective / Σ_members prospective` evaluated
inside the time loop and fed back into each member's next step. It is a
**reduction across the model-point axis at time `t`**, and the DSL has no way
to spell it.

This is the same shape as with-profits bonus declaration, asset-share
crediting, unitised fund pricing, and reinsurance treaty limits — a whole
class of products, not one product. `docs/rfc-001-dsl.md` defers it under
"multi-entity models (policy + fund + reinsurance treaty interacting) —
Phase 2, driven by the VA/VPLA library's needs." This review is that
driver. The likely shape is a `@pool` (or `@aggregate`) variable evaluated
once per `(t, scenario)` over the whole batch, ordered after the per-policy
variables it consumes and before those that consume it — which keeps the
graph acyclic and stays vectorizable. **No VPLA product template is added
here**; adding one before the DSL can express the adjustment would mean
hard-coding the reduction into the executor, which is exactly the kind of
shortcut the accuracy strategy forbids. *(That template now exists, on the
`@pool` variable the paragraph above describes.)*

### 7.2 Layer 0 gaps the VA library will hit

Ordered by how soon the VA/VPLA line needs them:

- **Monthly time axis.** VPLA pays monthly (`freq = 12`); the engine is
  annual. Everything downstream (fractional-age mortality, per-period
  discounting) depends on this landing first.
- **Fractional-age mortality.** `mortality_period`'s UDD conditioning is the
  reference implementation to port once the time axis exists.
- **Yield curve.** A term structure of discount factors, replacing the flat
  `assumptions.interest` scalar.
- **Improvement scales**, 1-D and generational — CPM2014B-shaped input.
- **Second life on the model point**, for joint and reversionary benefits.
  The closed form in §3.3 (`ä_x + j(ä_y - ä_xy)`) is the specification.

## 8. Addendum — what running the code confirmed

§3 and §6 were written from reading the source. The basis has since been
promoted into the engine (docs/rfc-002-basis.md) and reconciled against the
checkout *as it runs*, via `scripts/vpla_parity.py`. Three things that
review could only assert are now measured.

**The core is exactly as described.** 36,000 period mortality rates,
across five table/convention configurations at two payment frequencies on
the real CPM2014 and CPM2014B tables, are **bitwise identical** between
VPLA's `MortalityTable` and the engine's `MortalityBasis`. Annuity factors
agree to 3.2e-14, which is summation order and nothing else. The fractional
age split, the UDD conditioning, the 30/360 rounding, the generational
scale's held-flat tail and the limiting age all reproduce exactly.

**§6.1 is worse than stated.** The `joint_survivor_percent` validator does
not merely mishandle an edge case — it makes the joint annuity
*uncomputable*. Confirmed on the live class: passing `1.0` stores `None`,
the `== 0` guard evaluates False, and the arithmetic raises `TypeError`.
All 36 joint tests in `tests/test_joint_zero_rate.py` error today.

**Two thirds of the committed golden values no longer hold.** Of the 72
factors committed across the two test files:

| | count | what it means |
|---|---:|---|
| Agree with VPLA as it runs | 36 | every single-life case; 0 disagreements with the engine |
| Reproduce the committed constant | 42 | still-live goldens |
| Committed constant stale | 14 | VPLA's own code no longer produces them — 12 are single-life monthly, where the engine matches VPLA to the last bit, so the constants drifted, not the arithmetic |
| Joint constant shadowed | 16 | byte-identical to the corresponding *single-life* constant, the signature of having been recorded while the validator regression sent `joint_annuity_factor` down its `== 0` fall-through |

The 18 **annual** joint constants are the only live evidence for the
reversionary calculation, and the engine's closed form reproduces all
eighteen — a factor VPLA can no longer compute at all.

None of this touches the standing of the calculations themselves; the
mathematics in §3 is sound and reproduces exactly. What it says is that the
*test suite* guarding it has decayed: it cannot run offline (§6.12), a third
of its constants are stale or shadowed, and the regression that broke the
joint path went unnoticed because the tests that would have caught it were
already unrunnable. That is the specific failure the engine's golden-test
discipline exists to prevent, and it is now the case study for it.

## 9. Verdict

Take the **actuarial primitives** as reference implementations — they are
correct, they are validated against published golden values, and §3.3 gives
a closed form worth carrying into the library. Take the **product
specification** of the pool adjustment as the requirement that forces the
DSL's cross-model-point extension. Take **nothing** from the persistence,
UI, or infrastructure layers: the engine's Arrow/Parquet + run-registry
design supersedes all of it, and the defects in §6 are mostly artifacts of
having no golden-test harness to catch them.

Concretely landed alongside this review:

- `tests/vpla_reference.py` — an independent, dependency-free port of
  §3.1–3.3 (annual frequency, on-anniversary valuation), plus the pool
  adjustment of §4.
- `tests/test_vpla_reconciliation.py` — engine vs that reference at 1e-12,
  the reversionary closed form of §3.3, and the pool-balance invariant
  of §4.2.
