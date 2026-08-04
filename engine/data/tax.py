"""Tax hooks.

PLAN.md §5.1 asks for *tax hooks*, and the wording is the design. Tax regimes
differ by jurisdiction more than any other assumption in an actuarial model —
UK I-E, US DAC tax and the tax reserve, Canadian IIT, policyholder versus
shareholder funds — and a library that shipped one of them as "the" tax
calculation would be wrong everywhere else while looking authoritative.

So this is deliberately small: a rate, a base, and an explicit statement of
what happens to a loss. Everything jurisdiction-specific belongs in a model
that uses it, which is what the escape hatch in docs/rfc-001-dsl.md is for.

Two bases, because they behave differently
------------------------------------------
**Tax on profit** is charged on the profit emerging in a period, which can be
negative. **Tax on investment return** is charged on the return credited to a
fund, and reduces what the fund earns rather than what the shareholder keeps.

What happens to a loss is the whole question
--------------------------------------------
A period's profit can be negative — reliably so in the first year of a
policy, where acquisition costs land before any margin has emerged. What the
tax line does about that is a modelling decision with a material effect on
the value of a block, and it has three defensible answers:

``"full"``
    The loss is relieved immediately, against profits made elsewhere in the
    company. Tax is ``rate * profit`` whatever the sign. This is the usual
    assumption for a product line inside a profitable company, and it is the
    only one under which taxing each period and taxing the total give the
    same answer.

``"none"``
    No relief at all. Tax is ``rate * max(profit, 0)``. Conservative, and
    right for an entity with nothing to offset against.

``"carry_forward"``
    A loss is carried forward and set against the next profits, which is
    what most regimes actually allow. Needs a running balance, so it is a
    recursion over ``t`` rather than a function of one period — see
    :meth:`TaxBasis.carry_forward_step`, and ``TermLife.tax_loss_bf`` for the
    template side.

None of the three is a default worth having, so ``relief`` must be one of
them explicitly and ``rate = 0`` — no tax at all — is what a basis does when
nobody has asked for tax.
"""

from __future__ import annotations

import numpy as np

RELIEFS = ("full", "none", "carry_forward")


class TaxBasis:
    """A tax rate, a base, and what happens to a loss.

    ``profit_rate`` taxes emerging profit; ``investment_rate`` taxes the
    return credited to a fund. Both default to zero, so a basis nobody has
    configured cannot move a number — the templates need no branch and every
    projection that predates tax keeps its numbers.
    """

    def __init__(self, *, profit_rate: float = 0.0,
                 investment_rate: float = 0.0, relief: str = "full"):
        for name, rate in (("profit_rate", profit_rate),
                           ("investment_rate", investment_rate)):
            if not 0.0 <= rate < 1.0:
                raise ValueError(f"{name} {rate} outside [0, 1)")
        if relief not in RELIEFS:
            raise ValueError(f"relief must be one of {RELIEFS}, got {relief!r}")
        self.profit_rate = profit_rate
        self.investment_rate = investment_rate
        self.relief = relief

    def __repr__(self) -> str:
        return (f"TaxBasis(profit_rate={self.profit_rate}, "
                f"investment_rate={self.investment_rate}, "
                f"relief={self.relief!r})")

    def __bool__(self) -> bool:
        return bool(self.profit_rate or self.investment_rate)

    def __fingerprint__(self):
        return {"profit_rate": self.profit_rate,
                "investment_rate": self.investment_rate,
                "relief": self.relief}

    # --- profit -----------------------------------------------------------

    def taxable_profit(self, profit, loss_brought_forward=0.0):
        """The part of ``profit`` that is actually taxed this period.

        Under ``"full"`` relief that is the profit itself, negative and all;
        under ``"none"`` it is the profit floored at zero; under
        ``"carry_forward"`` it is the profit less whatever loss is being
        carried, floored at zero.

        ``loss_brought_forward`` is ignored by the first two, so a template
        can pass it unconditionally.
        """
        profit = np.asarray(profit, dtype=np.float64)
        if self.relief == "full":
            return profit
        if self.relief == "none":
            return np.maximum(profit, 0.0)
        return np.maximum(
            profit - np.asarray(loss_brought_forward, dtype=np.float64), 0.0
        )

    def on_profit(self, profit, loss_brought_forward=0.0):
        """Tax charged on a period's profit. Negative is a credit."""
        return self.profit_rate * self.taxable_profit(
            profit, loss_brought_forward
        )

    def carry_forward_step(self, profit, loss_brought_forward=0.0):
        """The loss carried into the next period.

        ``max(loss_bf - profit, 0)``: a profit uses up the carried loss, a
        further loss adds to it, and the balance never goes negative because
        a company cannot carry forward a profit.

        Zero under any other relief basis, so a template's recursion is the
        same expression whichever basis it is handed.
        """
        if self.relief != "carry_forward":
            return 0.0 * np.asarray(profit, dtype=np.float64)
        return np.maximum(
            np.asarray(loss_brought_forward, dtype=np.float64)
            - np.asarray(profit, dtype=np.float64),
            0.0,
        )

    # --- investment return ------------------------------------------------

    def net_investment_return(self, gross_return):
        """A fund's return after tax on it.

        ``rate = 0`` gives ``gross * (1 - 0.0)``, which is exactly the gross
        return for every finite value — so an untaxed fund is unchanged to
        the last bit and needs no branch.
        """
        return np.asarray(gross_return, dtype=np.float64) * (
            1.0 - self.investment_rate
        )
