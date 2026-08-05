"""Counterparty default risk: the module with a cliff in it.

RFC-027 assembled ``SCR = BSCR + SCR_op + Adj`` and took every Basic SCR
module as an input. RFC-014 built life underwriting and RFC-026 market
risk. This is the third of the five, and the last one PLAN §5.3 names:

    **Counterparty default and operational risk**, neither of which is a
    projection.

Operational risk went in with RFC-027 (Article 204). This is the other
half, and "not a projection" is right — it is a variance calculation on a
list of exposures, which makes it the only Basic SCR module that is neither
a scenario nor a factor table.

Every number is transcribed from Commission Delegated Regulation (EU)
2015/35, consolidated version ``02015R0035 — EN — 30.07.2020 — 007.001``,
Articles 189 to 202. Commission Delegated Regulation (EU) 2026/269 does not
amend them.

The shape of it
---------------
Two exposure classes. **Type 1** is the small, lumpy, rated stuff —
reinsurance recoverables, derivatives, cash at bank, deposits with cedants —
where the standard formula builds an actual loss distribution from
probabilities of default and losses-given-default, takes its standard
deviation, and multiplies. **Type 2** is the diffuse remainder —
intermediary receivables, policyholder debtors, mortgage loans — charged
with two flat factors. Article 189(1) aggregates them::

    SCR_def = sqrt(SCR_def,1² + 1.5 · SCR_def,1 · SCR_def,2 + SCR_def,2²)

which is an ordinary two-risk aggregation at a correlation of **0.75**,
written with the 2 already multiplied in.

The cliff
---------
Article 200 turns the standard deviation into capital in three bands::

    σ ≤ 7%  · ΣLGD   ->  SCR_def,1 = 3σ
    σ ≤ 20% · ΣLGD   ->  SCR_def,1 = 5σ
    σ >  20% · ΣLGD  ->  SCR_def,1 = ΣLGD

The upper boundary is continuous — ``5 × 20% = 100%`` of ΣLGD, which is
exactly what the third band gives. **The lower one is not.** At σ = 7% of
ΣLGD the first band gives 21% of ΣLGD and the second gives 35%, so an
arbitrarily small change in the portfolio moves the requirement by 14
percentage points of the total loss-given-default — a **66.7% increase**.
RFC-026 found a 10 basis point discontinuity in the spread table and called
it a defect worth reporting; this one is nearly seven hundred times larger
and is load-bearing.

Where the multiplier comes from is the concentration of the book, so which
side of the cliff an undertaking sits on is decided by how many
counterparties it has and how evenly the exposure is spread — not by how
creditworthy they are.

Solvency ratios are credit quality steps
----------------------------------------
Article 199(3) assigns a probability of default to an unrated insurer from
its own solvency ratio. RFC-026 implemented Article 186(2), which does the
same thing for the concentration sub-module's risk factor. The two tables
have different grids, and on the five ratios they share they agree exactly
— each one maps to a credit quality step's parameter in both sub-modules:

===========  ==================  ==========================
ratio        Art 199 PD          equal to credit quality step
===========  ==================  ==========================
196%         0.01%               1
175%         0.05%               2
122%         0.24%               3
95%          1.2%                4
75%          4.2%                5 and 6
===========  ==================  ==========================

So "122% covered" is not a number someone picked. It is the standard
formula's definition of a BBB counterparty, and it says the same thing in
both places it appears.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

#: Article 199(2): the probability of default by credit quality step 0 to 6.
PROBABILITY_OF_DEFAULT = (0.00002, 0.0001, 0.0005, 0.0024, 0.012, 0.042,
                          0.042)

#: Article 199(3): the same thing for an unrated insurer meeting its
#: Minimum Capital Requirement, from its own solvency ratio. The published
#: table runs from 196% down to 75%; it is held here in increasing order so
#: that it interpolates, and held flat outside the range as the article
#: requires.
SOLVENCY_RATIO = (0.75, 0.95, 1.00, 1.22, 1.25, 1.50, 1.75, 1.96)
SOLVENCY_RATIO_PD = (0.042, 0.012, 0.005, 0.0024, 0.002, 0.001, 0.0005,
                     0.0001)

#: Article 199(6) and (7): an equivalent third-country insurer, or a bank
#: meeting its own solvency requirements, with no ECAI assessment.
REGULATED_UNRATED_PD = 0.005
#: Article 199(9): everything else.
RESIDUAL_PD = 0.042
#: Article 199(4): an insurer that does not meet its Minimum Capital
#: Requirement.
FAILING_MCR_PD = 0.042

#: Article 200(1) to (3): the band edges as a share of the total
#: loss-given-default, and the multiplier in each of the first two bands.
LOWER_BAND = 0.07
UPPER_BAND = 0.20
LOWER_MULTIPLIER = 3.0
UPPER_MULTIPLIER = 5.0

#: Article 202: type 2 exposures. Receivables from intermediaries overdue by
#: more than three months take 90%; everything else takes 15%.
OVERDUE_RECEIVABLE_FACTOR = 0.90
TYPE_2_FACTOR = 0.15

#: Article 189(1), written out: the coefficient is ``2ρ`` with ``ρ = 0.75``.
TYPE_CORRELATION = 0.75

#: Article 197(7): the collateral factors. Where the undertaking's share of
#: the insolvency estate does **not** already reflect that it holds the
#: collateral, all four are 100%; otherwise they are these. They are
#: defaults here rather than constants, because 197(7) makes the choice a
#: property of the arrangement and not of the standard.
COLLATERAL_FACTORS = {"F": 0.50, "F1": 0.18, "F2": 0.16, "F3": 0.90}


def probability_of_default(credit_quality_step) -> np.ndarray:
    """Article 199(2): the PD for a rated single name exposure."""
    step = np.asarray(credit_quality_step)
    if np.any((step < 0) | (step > 6)):
        raise ValueError("credit quality steps run from 0 to 6")
    return np.asarray(PROBABILITY_OF_DEFAULT)[step.astype(np.intp)]


def insurer_probability_of_default(solvency_ratio, *, meets_mcr: bool = True,
                                   disclosed: bool = True) -> np.ndarray:
    """Article 199(3) to (5): the PD for an unrated insurer.

    Below a 75% ratio the PD is 4.2% and above 196% it is 0.01%, with
    linear interpolation between the eight tabulated points. An undertaking
    failing its Minimum Capital Requirement takes 4.2% under 199(4), and
    one that has not yet published a solvency and financial condition
    report is treated as though its ratio were 100% under 199(5) — which
    is 0.5%, the same figure 199(6) and (7) give an equivalent third-country
    insurer or a bank.
    """
    ratio = np.asarray(solvency_ratio, dtype=np.float64)
    if not disclosed:
        return np.full(ratio.shape, 0.005)
    if not meets_mcr:
        return np.full(ratio.shape, FAILING_MCR_PD)
    return np.interp(ratio, np.asarray(SOLVENCY_RATIO),
                     np.asarray(SOLVENCY_RATIO_PD))


def reinsurance_lgd(recoverables, risk_mitigation, collateral=0.0, *,
                    factor: float | None = None,
                    heavily_collateralised: bool = False) -> np.ndarray:
    """Article 192(2): the loss-given-default on a reinsurance arrangement.

    ``max(50%·(Recoverables + 50%·RM_re) − F·Collateral, 0)`` — half the
    recoverable is assumed lost, and half of the risk-mitigating effect of
    the treaty is counted alongside it, because losing the cover costs the
    underwriting relief as well as the balance.

    Where the counterparty is an insurer with 60% or more of its assets
    subject to collateral arrangements, the second subparagraph replaces
    the 50% with **90%** — a heavily collateralised reinsurer is treated as
    *worse*, not better, because the collateral its other cedants hold is
    exactly what is not available to this one.
    """
    recoverables = np.asarray(recoverables, dtype=np.float64)
    risk_mitigation = np.asarray(risk_mitigation, dtype=np.float64)
    collateral = np.asarray(collateral, dtype=np.float64)
    share = 0.90 if heavily_collateralised else 0.50
    if factor is None:
        factor = COLLATERAL_FACTORS["F1" if heavily_collateralised else "F"]
    gross = share * (recoverables + 0.50 * risk_mitigation)
    return np.maximum(gross - factor * collateral, 0.0)


def derivative_lgd(value, risk_mitigation, collateral=0.0, *,
                   share: float = 0.90, factor: float | None = None,
                   collateral_at_half: bool = False) -> np.ndarray:
    """Article 192(3) to (3c): the loss-given-default on a derivative.

    ``share`` is the article that applies: 18% for a derivative within
    Article 192a(1), 16% within 192a(2), and 90% otherwise. The first three
    take ``50%·F·Value`` against the collateral and the last takes
    ``F‴·Collateral``, which is what ``collateral_at_half`` selects.

    The spread is the whole point — a cleared derivative under Article
    192a(1) is charged **18%** where an uncleared one outside Regulation
    (EU) 648/2012's Article 11 is charged 90%, five times as much for the
    same exposure.
    """
    value = np.asarray(value, dtype=np.float64)
    risk_mitigation = np.asarray(risk_mitigation, dtype=np.float64)
    collateral = np.asarray(collateral, dtype=np.float64)
    if factor is None:
        factor = COLLATERAL_FACTORS["F3"]
    gross = share * (value + 0.50 * risk_mitigation)
    offset = (0.50 * factor * collateral if collateral_at_half
              else factor * collateral)
    return np.maximum(gross - offset, 0.0)


def _intra_coefficient(pd: np.ndarray) -> np.ndarray:
    """Article 201(3): ``1.5·PD(1−PD) / (2.5 − PD)``."""
    return 1.5 * pd * (1.0 - pd) / (2.5 - pd)


def _inter_coefficient(pd_j: np.ndarray, pd_k: np.ndarray) -> np.ndarray:
    """Article 201(2): the cross term's coefficient."""
    return (pd_j * (1.0 - pd_j) * pd_k * (1.0 - pd_k)
            / (1.25 * (pd_j + pd_k) - pd_j * pd_k))


def loss_variance(probabilities, losses) -> tuple:
    """Article 201: ``V = V_inter + V_intra`` for type 1 exposures.

    ``V_inter`` is a double sum over the *distinct* probabilities of
    default, weighted by the total loss-given-default at each; ``V_intra``
    sums over exposures. The grouping in Article 201(3) is exact rather
    than an approximation — summing ``LGD²`` within each probability group
    and then over groups is the same as summing over every exposure — so
    this evaluates it directly.

    Returns ``(V_inter, V_intra)``, because their split is the whole story
    of a book: ``V_intra`` is concentration in individual names and
    ``V_inter`` is how much the names move together.
    """
    pd = np.asarray(probabilities, dtype=np.float64).ravel()
    lgd = np.asarray(losses, dtype=np.float64).ravel()
    if pd.shape != lgd.shape:
        raise ValueError(
            f"{pd.size} probabilities against {lgd.size} losses-given-default"
        )
    if np.any((pd < 0.0) | (pd > 1.0)):
        raise ValueError("probabilities of default must lie in [0, 1]")
    if pd.size == 0:
        return 0.0, 0.0
    distinct, inverse = np.unique(pd, return_inverse=True)
    totals = np.bincount(inverse, weights=lgd, minlength=distinct.size)
    grid_j, grid_k = np.meshgrid(distinct, distinct, indexing="ij")
    with np.errstate(invalid="ignore", divide="ignore"):
        coefficients = _inter_coefficient(grid_j, grid_k)
    # A probability of zero contributes nothing and divides by zero doing
    # it, which is a removable singularity rather than an error: Article
    # 199(8) assigns 0% to central-bank-grade counterparties and they carry
    # no variance.
    coefficients = np.nan_to_num(coefficients, nan=0.0, posinf=0.0,
                                 neginf=0.0)
    inter = float(totals @ coefficients @ totals)
    intra = float((_intra_coefficient(pd) * lgd * lgd).sum())
    return inter, intra


@dataclass
class Type1Result:
    """The type 1 requirement, and which of Article 200's bands produced it."""

    capital: float
    sigma: float
    total_lgd: float
    band: str
    variance_inter: float = 0.0
    variance_intra: float = 0.0

    @property
    def ratio(self) -> float:
        """``σ / ΣLGD`` — the quantity Article 200 bands on."""
        return 0.0 if self.total_lgd == 0.0 else self.sigma / self.total_lgd

    def __repr__(self) -> str:
        return (f"Type1Result(capital={self.capital:,.2f}, "
                f"sigma/LGD={self.ratio:.2%}, band={self.band!r})")


def type_1_capital(probabilities, losses) -> Type1Result:
    """Article 200: three bands on ``σ`` as a share of the total LGD.

    The upper boundary is continuous; the lower one is not. Crossing
    ``σ = 7%·ΣLGD`` moves the multiplier from 3 to 5 and the requirement
    from 21% to 35% of the total loss-given-default, with nothing in
    between.
    """
    inter, intra = loss_variance(probabilities, losses)
    sigma = math.sqrt(max(inter + intra, 0.0))
    total = float(np.asarray(losses, dtype=np.float64).sum())
    if total <= 0.0:
        return Type1Result(capital=0.0, sigma=sigma, total_lgd=total,
                           band="empty", variance_inter=inter,
                           variance_intra=intra)
    if sigma <= LOWER_BAND * total:
        capital, band = LOWER_MULTIPLIER * sigma, "3σ"
    elif sigma <= UPPER_BAND * total:
        capital, band = UPPER_MULTIPLIER * sigma, "5σ"
    else:
        capital, band = total, "ΣLGD"
    return Type1Result(capital=capital, sigma=sigma, total_lgd=total,
                       band=band, variance_inter=inter, variance_intra=intra)


def type_2_capital(losses, overdue_receivables: float = 0.0) -> float:
    """Article 202: ``90%·LGD_overdue + 15%·Σ LGD_i``.

    ``overdue_receivables`` is the loss-given-default on receivables from
    intermediaries due for more than three months, and ``losses`` is
    everything else. A receivable that crosses the three-month mark has its
    charge multiplied by **six**, on the same day, with no transition.
    """
    other = float(np.asarray(losses, dtype=np.float64).sum())
    return (OVERDUE_RECEIVABLE_FACTOR * float(overdue_receivables)
            + TYPE_2_FACTOR * other)


@dataclass
class CounterpartyDefault:
    """Article 189(1): the two exposure classes, aggregated."""

    type_1: Type1Result
    type_2: float

    @property
    def capital(self) -> float:
        a, b = self.type_1.capital, self.type_2
        return math.sqrt(a * a + 2.0 * TYPE_CORRELATION * a * b + b * b)

    @property
    def undiversified(self) -> float:
        return self.type_1.capital + self.type_2

    def reconciles(self, tolerance: float = 1e-9) -> bool:
        """The aggregate lies between the larger leg and their sum.

        True for any correlation in [0, 1], and the cheapest possible guard
        on a formula whose published form has the 2 already multiplied into
        the correlation — a reader who takes 1.5 for the correlation itself
        gets an aggregate above the sum.
        """
        scale = max(1.0, self.undiversified)
        return (self.capital >= max(self.type_1.capital, self.type_2)
                - tolerance * scale
                and self.capital <= self.undiversified + tolerance * scale)

    def __repr__(self) -> str:
        return (f"CounterpartyDefault(SCR={self.capital:,.2f}, "
                f"type 1 {self.type_1.capital:,.2f}, "
                f"type 2 {self.type_2:,.2f})")


def counterparty_default(probabilities, losses, *, type_2_losses=(),
                         overdue_receivables: float = 0.0
                         ) -> CounterpartyDefault:
    """The whole module: Articles 199 to 202, aggregated by Article 189(1)."""
    return CounterpartyDefault(
        type_1=type_1_capital(probabilities, losses),
        type_2=type_2_capital(type_2_losses, overdue_receivables),
    )


def band_boundary_jump(total_lgd: float) -> float:
    """What crossing Article 200's lower boundary costs, on a book of
    ``total_lgd``.

    ``(5 − 3) × 7% × ΣLGD``. Reported as a function rather than described,
    because the size of it is the finding.
    """
    return ((UPPER_MULTIPLIER - LOWER_MULTIPLIER) * LOWER_BAND
            * float(total_lgd))
