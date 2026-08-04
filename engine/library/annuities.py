"""Annuity factors — Layer 0, promoted from VPLA's ``Person``.

Single life, life-and-certain, joint life and reversionary (contingent
survivor) factors, evaluated over a whole block of lives at once. Inputs are
a discount vector from :class:`engine.data.rates.YieldCurve` and survival
curves from :class:`engine.data.mortality.MortalityBasis`, so every factor
here inherits the fractional-age and improvement machinery those carry.

Where the arithmetic differs from VPLA, and why
-----------------------------------------------
Two of these are reorganised rather than transcribed. Both are faster, and
both carry a smaller error bound than the original — which is a statement
about the bound, not a promise about every individual case:

- **Reversionary factors use the closed form.** VPLA accumulates, for every
  period ``i``, the chance the primary dies in that period times the value
  of the survivor's remaining payments — an O(n²) double loop that rebuilds
  a deferred-annuity vector. Exchanging the order of summation collapses it
  to ``ä_x + j(ä_y - ä_xy)`` exactly (docs/vpla-review.md §3.3). At monthly
  frequency over 120 years that is 1,440 operations instead of ~1,036,800,
  and it removes ~10⁶ roundings rather than adding any.
- **Sums are pairwise, not sequential.** NumPy's reduction is pairwise,
  where Python's ``sum`` accumulates left to right, so results differ in the
  last bits or two. Pairwise error grows with the logarithm of the term
  count rather than with the count, which over a 720-term monthly
  projection measures as ~1 unit in the last place against the original's
  ~14. That is a bound, not a per-case guarantee — the original wins the
  occasional individual case — so tests/test_annuity_factors.py asserts it
  where it is true: on the worst and average error across a block, against
  ``math.fsum`` of the same terms.

Everything else — the elementwise ``/ freq`` inside the single-life factor,
the certain period overwriting survival with 1, the joint-life factor not
being divided by ``freq`` at all — is VPLA's behaviour, kept deliberately.
"""

from __future__ import annotations

import numpy as np


def _aligned(discount, survival):
    survival = np.asarray(survival, dtype=np.float64)
    discount = np.asarray(discount, dtype=np.float64)
    n = survival.shape[-1]
    if discount.shape[-1] < n:
        raise ValueError(
            f"discount vector covers {discount.shape[-1]} periods, "
            f"survival needs {n}"
        )
    return discount[..., :n], survival


def certain_periods(certain_years: float, freq: int) -> int:
    """Payment periods covered by a guarantee of ``certain_years``."""
    periods = round(certain_years * freq)
    if periods < 0:
        raise ValueError(f"certain_years {certain_years} is negative")
    return int(periods)


def annuity_factor(discount, survival, freq: int, guaranteed: int = 0):
    """``ä_x = Σ_k v_k · ₖp_x / freq``, one value per life.

    ``survival`` is ``(n_lives, n_periods)`` (or a single curve); a
    guarantee of ``guaranteed`` periods pays whether or not the life
    survives, which is the life-and-certain factor.
    """
    discount, survival = _aligned(discount, survival)
    if guaranteed:
        survival = survival.copy()
        survival[..., :guaranteed] = 1.0
    return np.sum(discount * survival / freq, axis=-1)


def deferred_annuity_values(discount, survival):
    """Time-0 value of the payments from period ``k`` onward, for every ``k``.

    A reverse cumulative sum: O(n) where VPLA recomputes each tail from
    scratch, and it accumulates smallest-first, which is the better
    direction for a decaying series.
    """
    discount, survival = _aligned(discount, survival)
    terms = discount * survival
    return np.cumsum(terms[..., ::-1], axis=-1)[..., ::-1]


def joint_life_factor(discount, survival_x, survival_y):
    """``ä_xy = Σ_k v_k · ₖp_x · ₖp_y`` — payments while **both** live.

    Not divided by ``freq``, matching VPLA. Note this multiplies the two
    survival curves rather than blending the mortality rates first, so it
    differs from the SOA site's joint-life convention — a difference VPLA's
    own docstring records.
    """
    discount, survival_x = _aligned(discount, survival_x)
    survival_y = np.asarray(survival_y, dtype=np.float64)[..., : discount.shape[-1]]
    return np.sum(discount * survival_x * survival_y, axis=-1)


def reversionary_annuity_factor(
    discount, survival_x, survival_y, joint_percent, freq: int
):
    """Primary annuity plus ``joint_percent`` of it continuing to the
    survivor: ``ä_x + j(ä_y - ä_xy)``, under independent lives.

    ``joint_percent = 0`` returns the single-life factor exactly.
    """
    discount, survival_x = _aligned(discount, survival_x)
    survival_y = np.asarray(survival_y, dtype=np.float64)[..., : discount.shape[-1]]
    joint_percent = np.asarray(joint_percent, dtype=np.float64)
    a_x = np.sum(discount * survival_x, axis=-1)
    a_y = np.sum(discount * survival_y, axis=-1)
    a_xy = np.sum(discount * survival_x * survival_y, axis=-1)
    return (a_x + joint_percent * (a_y - a_xy)) / freq


def block_annuity_factors(
    basis,
    curve,
    *,
    dob,
    sex,
    valuation,
    joint_percent=None,
    spouse_dob=None,
    spouse_sex=None,
    certain_years=0.0,
    n_periods: int | None = None,
):
    """Annuity factors for a whole block of lives in one pass.

    This is the vectorized replacement for VPLA's
    ``CalcEngine.calculate_annuity_factors``, which builds a ``Person`` and
    recomputes survival curves inside a ``DataFrame.iterrows()`` loop.

    Lives with a zero (or absent) ``joint_percent`` take the single-life
    factor, so a mixed block needs no branching by the caller. A guarantee
    period applies to the primary annuity, as in VPLA.
    """
    freq = curve.freq
    n = curve.n_periods if n_periods is None else n_periods
    discount = curve.discount_factors(n)
    guaranteed = certain_periods(certain_years, freq)

    survival_x = basis.survival_curve(dob, valuation, sex, freq, n)
    single = annuity_factor(discount, survival_x, freq, guaranteed)
    if joint_percent is None:
        return single

    joint_percent = np.asarray(joint_percent, dtype=np.float64)
    has_spouse = joint_percent > 0.0
    if not np.any(has_spouse):
        return single
    if spouse_dob is None or spouse_sex is None:
        raise ValueError(
            "spouse_dob and spouse_sex are required when any joint_percent > 0"
        )
    survival_y = basis.survival_curve(spouse_dob, valuation, spouse_sex, freq, n)
    if guaranteed:
        survival_x = survival_x.copy()
        survival_x[..., :guaranteed] = 1.0
    joint = reversionary_annuity_factor(
        discount, survival_x, survival_y, joint_percent, freq
    )
    return np.where(has_spouse, joint, single)
