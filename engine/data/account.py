"""Account-value mechanics for interest-sensitive contracts.

PLAN.md §5.2 asks for **universal life / interest-sensitive** products with
"account-value mechanics, secondary guarantees". This module is the account
side of that: the charges that come out, the rate that goes in, and the two
places a contract's own arithmetic overrides the projection — the corridor
and the crediting floor.

What makes this family different from everything before it
----------------------------------------------------------
Every template so far projects a *benefit* fixed by the contract and asks
what it costs. A universal-life account value is the opposite: the benefit
is whatever the account has become, and the account is the running result of
charges the insurer sets and a rate the insurer declares. The liability is
therefore a **state variable**, not a formula in the policy data, and two
consequences follow:

- **The contract can lapse from arithmetic.** When the account cannot meet
  its own charges the policy goes off the books, at a date nobody wrote in
  the model point. This is the first template where a projected number
  decides whether a policy still exists.
- **The crediting floor is a written option.** A minimum guaranteed
  crediting rate costs nothing in a deterministic run at any rate above it,
  and costs real money once returns are allowed to vary. It is the reason
  this family belongs in the stochastic executor rather than beside it.

What the defaults are, and the one that is not neutral
-----------------------------------------------------
An ``AccountBasis()` nobody has configured takes no premium load, charges no
policy fee, applies no corridor and no surrender charge, credits nothing and
carries no secondary guarantee.

**The cost of insurance is the exception, deliberately.** It defaults to one
times the mortality basis — expected mortality, no margin — because the
neutral-looking alternative, a zero COI, is not a neutral default but a
nonsense product: free life cover, an account that grows without paying for
the risk it carries, and a projection that would look plausible while
answering nothing. Nothing predates this module, so there is no earlier
result for a zero default to protect, and a default that has to be
overridden before the model means anything is worse than one that has to be
overridden before the model earns anything.
"""

from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np

CREDITING_MODES = ("declared", "portfolio")

#: Death-benefit corridor percentages from IRC §7702(d)(2), keyed by
#: attained age. The listed ages are the breakpoints; ages between two
#: entries take the lower breakpoint's factor, and ages past the last take
#: 1.00. Reproduced because a corridor that is *nearly* the statutory one
#: fails the test it exists to pass.
SECTION_7702 = {
    0: 2.50, 41: 2.43, 42: 2.36, 43: 2.29, 44: 2.22, 45: 2.15,
    46: 2.09, 47: 2.03, 48: 1.97, 49: 1.91, 50: 1.85,
    51: 1.78, 52: 1.71, 53: 1.64, 54: 1.57, 55: 1.50,
    56: 1.46, 57: 1.42, 58: 1.38, 59: 1.34, 60: 1.30,
    61: 1.28, 62: 1.26, 63: 1.24, 64: 1.22, 65: 1.20,
    66: 1.19, 67: 1.18, 68: 1.17, 69: 1.16, 70: 1.15,
    71: 1.13, 72: 1.11, 73: 1.09, 74: 1.07, 75: 1.05,
    91: 1.04, 92: 1.03, 93: 1.02, 94: 1.01, 95: 1.00,
}


class Corridor:
    """Minimum ratio of death benefit to account value, by attained age.

    US tax law will not let a contract be all savings: to stay life
    insurance under IRC §7702 the death benefit must stay a stated multiple
    of the account value, and the multiple falls with age. A heavily funded
    policy therefore carries *more* cover than its face amount, pays COI on
    the excess, and the projection has to know that — a model that stops at
    ``max(face, account)`` understates both the benefit and its cost.

    The factors are looked up at whole attained ages and applied as
    ``death_benefit >= factor(age) * account_value``.
    """

    def __init__(self, factors: Mapping[int, float] | None = None):
        factors = dict(SECTION_7702 if factors is None else factors)
        if not factors:
            raise ValueError("a corridor needs at least one factor")
        for age, factor in factors.items():
            if factor < 0.0:
                raise ValueError(
                    f"corridor factor {factor} at age {age} is negative"
                )
        self._ages = np.array(sorted(factors), dtype=np.int64)
        self._factors = np.array(
            [factors[age] for age in self._ages], dtype=np.float64
        )

    @classmethod
    def section_7702(cls) -> "Corridor":
        """The statutory table."""
        return cls()

    @classmethod
    def level(cls, factor: float) -> "Corridor":
        """One factor at every age — the shape of a non-US minimum-cover
        rule, and the way to isolate the corridor's effect in a test."""
        return cls({0: factor})

    @classmethod
    def off(cls) -> "Corridor":
        """No corridor at all: the death benefit is the face amount however
        large the account grows.

        No real universal-life contract does this — it would stop being
        insurance — but it is the identity, so a projection that has not
        asked for a corridor keeps its numbers to the last bit.
        """
        return cls({0: 0.0})

    def __repr__(self) -> str:
        return f"Corridor({len(self._ages)} breakpoints)"

    def __fingerprint__(self):
        return {"ages": self._ages.tolist(), "factors": self._factors.tolist()}

    def factor(self, ages):
        """Corridor factor at each attained age.

        ``searchsorted`` gives the step function: an age between two
        breakpoints takes the lower one's factor, which is how a table of
        breakpoints is read.
        """
        ages = np.asarray(ages)
        idx = np.searchsorted(self._ages, ages, side="right") - 1
        return self._factors[np.clip(idx, 0, len(self._factors) - 1)]


class SurrenderCharge:
    """Percentage of the account value withheld on a voluntary surrender,
    by completed policy year.

    A schedule, because a surrender charge exists to recover acquisition
    costs and therefore runs off: ``schedule[0]`` applies during policy year
    one, and every duration past the end of the schedule charges nothing.

    Expressed as a proportion of the account value. The other common form —
    an amount per 1000 of face amount — is a different quantity and belongs
    in a template that reads the face amount, not in a factor applied here.
    """

    def __init__(self, schedule: Sequence[float] = ()):
        schedule = tuple(float(x) for x in schedule)
        for i, rate in enumerate(schedule):
            if not 0.0 <= rate <= 1.0:
                raise ValueError(
                    f"surrender charge {rate} in year {i + 1} outside [0, 1]"
                )
        self._schedule = np.array(schedule, dtype=np.float64)

    @classmethod
    def none(cls) -> "SurrenderCharge":
        """No charge at any duration — an empty schedule, which makes the
        cash value the account value exactly."""
        return cls(())

    @classmethod
    def level(cls, rate: float, years: int) -> "SurrenderCharge":
        return cls([rate] * years)

    @classmethod
    def declining(cls, initial: float, years: int) -> "SurrenderCharge":
        """Straight-line run-off from ``initial`` to zero over ``years``.

        Year one charges ``initial``; the last charged year is
        ``initial / years``. The schedule reaches zero the year *after* it
        ends rather than in its final year, which is the usual shape and
        keeps ``years`` the count of years that carry a charge.
        """
        if years < 1:
            raise ValueError(f"a declining schedule needs years >= 1, got {years}")
        return cls([initial * (years - i) / years for i in range(years)])

    def __repr__(self) -> str:
        return f"SurrenderCharge({self._schedule.tolist()})"

    def __bool__(self) -> bool:
        return bool(self._schedule.size and self._schedule.any())

    def __fingerprint__(self):
        return {"schedule": self._schedule.tolist()}

    @property
    def years(self) -> int:
        return int(self._schedule.size)

    def factor(self, durations):
        """Charge rate at each completed-policy-year duration (0-based).

        Zero past the end of the schedule and zero for an empty one, so a
        contract without a surrender charge surrenders at the full account
        value without the template branching.
        """
        durations = np.asarray(durations)
        if self._schedule.size == 0:
            return np.zeros(np.shape(durations), dtype=np.float64)
        idx = np.clip(durations, 0, self._schedule.size - 1)
        return np.where(
            durations < self._schedule.size, self._schedule[idx], 0.0
        )


class CreditingBasis:
    """The rate credited to an account value, and the floor under it.

    Two modes, because the industry runs two products under one name:

    ``"declared"``
        The insurer announces a rate and credits it — ``current``, which
        must sit at or above ``guaranteed``. What the assets earned does not
        enter, so this mode prices no option and a stochastic run of it
        differs from a deterministic one only through the benefits.

    ``"portfolio"``
        The account is credited what the backing assets earned, less a
        contractual ``spread``, and **floored at** ``guaranteed``. That
        floor is a put option the insurer has written on its own portfolio:
        worth nothing in any single scenario that stays above it, and worth
        real money across a distribution that does not.

    The two coincide exactly when returns are flat at ``current + spread``,
    which is the sanity check on the pair.
    """

    def __init__(self, *, current: float = 0.0, guaranteed: float = 0.0,
                 spread: float = 0.0, mode: str = "declared"):
        if mode not in CREDITING_MODES:
            raise ValueError(
                f"crediting mode must be one of {CREDITING_MODES}, got {mode!r}"
            )
        if guaranteed < 0.0:
            raise ValueError(f"guaranteed rate {guaranteed} is negative")
        if spread < 0.0:
            raise ValueError(f"spread {spread} is negative")
        if mode == "declared" and current < guaranteed:
            raise ValueError(
                f"declared rate {current} is below the guaranteed rate "
                f"{guaranteed}; an insurer cannot credit less than it promised"
            )
        self.current = current
        self.guaranteed = guaranteed
        self.spread = spread
        self.mode = mode

    def __repr__(self) -> str:
        return (f"CreditingBasis(mode={self.mode!r}, current={self.current}, "
                f"guaranteed={self.guaranteed}, spread={self.spread})")

    def __bool__(self) -> bool:
        return bool(self.current or self.guaranteed or self.spread)

    def __fingerprint__(self):
        return {"current": self.current, "guaranteed": self.guaranteed,
                "spread": self.spread, "mode": self.mode}

    def credited(self, earned, *, freq: int = 1):
        """The rate credited over one projection period.

        ``earned`` is the period return on the backing assets — a scenario
        return in a stochastic run, the valuation interest rate in a
        deterministic one. It is ignored in ``declared`` mode.

        Both conversions are the annual figures unchanged at ``freq = 1``,
        bit for bit, without evaluating a power:

        - the **guaranteed rate accumulates**, so it converts by
          ``(1 + g) ** (1/freq) - 1`` — ``freq`` credits of the periodic
          rate leave the account where one annual credit would. In *exact*
          arithmetic: the binary round trip costs about 5 ulps at
          ``freq = 12``, which compounds to a relative 3e-14 over 25 years.
          That is not the bitwise identity
          :meth:`~engine.data.assumptions.Assumptions.to_periodic` gives at
          ``freq = 1``, and it is worth saying so rather than claiming an
          exactness the floating-point does not deliver;
        - the **spread is a quoted annual deduction taken periodically**, so
          it converts by ``spread / freq``, which is how a contract that
          says "1% a year, deducted monthly" is administered.

        Converting the spread geometrically instead would be defensible
        arithmetic answering a question the policy document does not ask.
        """
        guaranteed = (
            self.guaranteed if freq == 1
            else (1.0 + self.guaranteed) ** (1.0 / freq) - 1.0
        )
        if self.mode == "declared":
            current = (
                self.current if freq == 1
                else (1.0 + self.current) ** (1.0 / freq) - 1.0
            )
            return np.maximum(
                np.zeros_like(np.asarray(earned, dtype=np.float64)) + current,
                guaranteed,
            )
        spread = self.spread if freq == 1 else self.spread / freq
        return np.maximum(
            np.asarray(earned, dtype=np.float64) - spread, guaranteed
        )

    def unfloored(self, earned, *, freq: int = 1):
        """The same rate with the guarantee switched off.

        The difference between this and :meth:`credited`, valued over a
        distribution of ``earned``, **is** the cost of the guarantee — so
        the template exposes both rather than making the reader rebuild one
        of them.
        """
        if self.mode == "declared":
            current = (
                self.current if freq == 1
                else (1.0 + self.current) ** (1.0 / freq) - 1.0
            )
            return np.zeros_like(np.asarray(earned, dtype=np.float64)) + current
        spread = self.spread if freq == 1 else self.spread / freq
        return np.asarray(earned, dtype=np.float64) - spread


class CostOfInsurance:
    """Cost-of-insurance rates, as a loading on the mortality basis.

    A COI rate is not a mortality rate: it is a price, set at issue, that
    the contract may raise up to a guaranteed maximum. Modelling it as
    ``loading * q`` keeps one mortality basis in the model and makes the
    margin explicit — ``loading = 1.0`` charges expected mortality and
    earns nothing on it.

    ``guaranteed_loading`` is the contractual ceiling, used by a secondary
    guarantee's shadow account (which is always tested on guaranteed
    charges) and available to a stress that assumes the insurer charges the
    maximum. It defaults to ``loading``, so a basis nobody has configured
    prices its guarantee on the same rates it charges.
    """

    def __init__(self, *, loading: float = 1.0,
                 guaranteed_loading: float | None = None):
        if loading < 0.0:
            raise ValueError(f"COI loading {loading} is negative")
        if guaranteed_loading is None:
            guaranteed_loading = loading
        elif guaranteed_loading < loading:
            raise ValueError(
                f"guaranteed COI loading {guaranteed_loading} is below the "
                f"current loading {loading}; the guarantee is a ceiling"
            )
        self.loading = loading
        self.guaranteed_loading = guaranteed_loading

    def __repr__(self) -> str:
        return (f"CostOfInsurance(loading={self.loading}, "
                f"guaranteed_loading={self.guaranteed_loading})")

    def __fingerprint__(self):
        return {"loading": self.loading,
                "guaranteed_loading": self.guaranteed_loading}

    def rate(self, q, *, guaranteed: bool = False):
        """COI rate for a period whose mortality rate is ``q``."""
        loading = self.guaranteed_loading if guaranteed else self.loading
        return np.asarray(q, dtype=np.float64) * loading


class NoLapseGuarantee:
    """A secondary guarantee, run as a shadow account.

    The contract stays in force while a *second*, notional account stays
    positive, whatever the real one has done. The shadow account is rolled
    forward on its own terms — usually a better crediting rate and cheaper
    charges than the real account gets, because the guarantee is priced to
    be met by a stated premium rather than to accumulate value.

    Nothing about it is ever paid to anybody: it is a test, and the only
    number it produces is a yes or no on whether the policy still exists.
    That is why the shadow account carries no corridor and no surrender
    charge — neither concept applies to an amount that cannot be received.

    ``years = 0`` is off, and off is the exact identity: the shadow account
    is multiplied to zero at every period and can keep no policy alive.
    """

    def __init__(self, *, years: int = 0, premium_load: float = 0.0,
                 policy_fee: float = 0.0,
                 coi: "CostOfInsurance | None" = None,
                 crediting: "CreditingBasis | None" = None):
        if years < 0:
            raise ValueError(f"guarantee period {years} is negative")
        if not 0.0 <= premium_load < 1.0:
            raise ValueError(f"premium load {premium_load} outside [0, 1)")
        if policy_fee < 0.0:
            raise ValueError(f"policy fee {policy_fee} is negative")
        self.years = years
        self.premium_load = premium_load
        self.policy_fee = policy_fee
        self.coi = coi if coi is not None else CostOfInsurance()
        self.crediting = (
            crediting if crediting is not None else CreditingBasis()
        )

    @classmethod
    def off(cls) -> "NoLapseGuarantee":
        return cls()

    def __repr__(self) -> str:
        return f"NoLapseGuarantee(years={self.years})"

    def __bool__(self) -> bool:
        return self.years > 0

    def __fingerprint__(self):
        return {"years": self.years, "premium_load": self.premium_load,
                "policy_fee": self.policy_fee, "coi": self.coi,
                "crediting": self.crediting}


class AccountBasis:
    """Everything that acts on a universal-life account value.

    No load, no fee, no corridor, no surrender charge, a zero crediting
    basis and no secondary guarantee, so no template needs a branch to find
    out whether it was given one — with the single deliberate exception of
    the cost of insurance, which defaults to expected mortality. See the
    module docstring for why a zero COI would be the wrong default.
    """

    def __init__(self, *, premium_load: float = 0.0, policy_fee: float = 0.0,
                 coi: "CostOfInsurance | None" = None,
                 crediting: "CreditingBasis | None" = None,
                 surrender_charge: "SurrenderCharge | None" = None,
                 corridor: "Corridor | None" = None,
                 no_lapse_guarantee: "NoLapseGuarantee | None" = None):
        if not 0.0 <= premium_load < 1.0:
            raise ValueError(f"premium load {premium_load} outside [0, 1)")
        if policy_fee < 0.0:
            raise ValueError(f"policy fee {policy_fee} is negative")
        self.premium_load = premium_load
        #: Annual contractual policy fee. A charge in the contract, not an
        #: expense the insurer incurs — the two are different numbers and
        #: live in different bases (see engine/data/expenses.py).
        self.policy_fee = policy_fee
        self.coi = coi if coi is not None else CostOfInsurance()
        self.crediting = crediting if crediting is not None else CreditingBasis()
        self.surrender_charge = (
            surrender_charge if surrender_charge is not None
            else SurrenderCharge.none()
        )
        self.corridor = corridor if corridor is not None else Corridor.off()
        self.no_lapse_guarantee = (
            no_lapse_guarantee if no_lapse_guarantee is not None
            else NoLapseGuarantee.off()
        )

    def __repr__(self) -> str:
        return (f"AccountBasis(premium_load={self.premium_load}, "
                f"policy_fee={self.policy_fee}, coi={self.coi}, "
                f"crediting={self.crediting}, "
                f"surrender_charge={self.surrender_charge}, "
                f"corridor={self.corridor}, "
                f"no_lapse_guarantee={self.no_lapse_guarantee})")

    def __bool__(self) -> bool:
        return bool(
            self.premium_load or self.policy_fee or self.crediting
            or self.surrender_charge or self.no_lapse_guarantee
        )

    def __fingerprint__(self):
        return {"premium_load": self.premium_load,
                "policy_fee": self.policy_fee,
                "coi": self.coi, "crediting": self.crediting,
                "surrender_charge": self.surrender_charge,
                "corridor": self.corridor,
                "no_lapse_guarantee": self.no_lapse_guarantee}
