"""Two texts, one block: what an amendment actually did, clause by clause.

Execution plan §8, item F2. :mod:`engine.report.market_risk` already ships
two dated calibrations — Commission Delegated Regulation (EU) 2015/35 and
(EU) 2026/269 — and every divergence between them as a named setting on
``LEGISLATIVE_OPTIONS``. This module is the report over that: run one block
under both texts, and attribute the movement to the clauses that caused it.

That is the answer to the vendors' quarterly-library-update moat. A vendor
ships a new library and the number changes; here a regulation change is a
**diffable artifact** — per-module deltas, per-clause drivers, and a
residual that says how much of the movement belongs to no single clause.

The clauses are not independent, and pretending otherwise is the trap
--------------------------------------------------------------------
An amendment is rarely one thing. 2026/269 moves the interest tables,
deletes Article 166(2)'s minimum, replaces Article 167(2)'s negative-rate
rule, widens Article 172(4)'s corridor and splits Article 164(3)'s spread
correlation out — five clauses arriving on one date. Throw them one at a
time and the effects **do not add up to the effect of throwing them
together**: RFC-026 measured +1.35 against +6.49 on the same block.

So a per-clause table on its own is a decomposition that does not
reconcile, and the failure mode is specific and quiet — somebody reads the
largest row and calls it the driver. :class:`RegulationDiff` therefore
carries :attr:`~RegulationDiff.interaction` as a first-class number and
:meth:`~RegulationDiff.reconciles` asserts that the clauses plus the
interaction equal the total, exactly. A residual that is large relative to
the clauses is not an error; it is the finding, and it means the amendment
has to be read as a package.

Forward and backward, because they disagree
-------------------------------------------
There are two natural one-at-a-time decompositions and they give different
numbers:

- **forward** — ``f(baseline + clause) − f(baseline)``: what this clause
  does to the world as it stands;
- **backward** — ``f(amended) − f(amended − clause)``: what this clause
  contributes to the world as it will be.

For a perfectly additive amendment they are equal. The gap between them is
this clause's interaction with all the others, which is why both are
reported per clause rather than one being chosen and called *the* effect.
A clause whose forward effect is small and whose backward effect is large
does nothing on its own and a great deal once the rest of the amendment has
landed, and that is the single most useful thing this report can say.

What this module does not do
----------------------------
It does not know what a calibration *is*. ``run`` is a callable taking a
calibration and returning anything with ``scr`` and ``modules``, so this
works for market risk today and for the next dated set without change. What
it does need is that the two texts expose their divergences as named
settings — the ``options``/``variant`` pair
:class:`~engine.report.market_risk.MarketRiskCalibration` carries. A regime
that bakes its parameters in can be diffed for its total and not for its
drivers, and this module says so rather than inventing an attribution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np


class RegDiffError(ValueError):
    """A comparison this module will not report.

    Two texts that do not differ, a pair that cannot be varied one clause at
    a time, or a run that answers with something other than a position.
    """


@dataclass(frozen=True)
class ClauseEffect:
    """One named divergence between two texts, measured both ways.

    ``forward`` throws this clause alone onto the baseline; ``backward``
    takes it alone off the amended text. They agree only where the clause
    does not interact with the rest of the amendment, and
    :attr:`interaction` is how far from that it is.
    """

    setting: str
    description: str
    baseline: object
    amended: object
    forward: float
    backward: float

    @property
    def interaction(self) -> float:
        """``backward − forward``: what the rest of the amendment does to
        this clause's effect.

        Zero for a clause that acts alone. Large and positive for one that
        is inert on its own and bites once the others have landed — which
        is the case a per-clause table with one column would hide.
        """
        return self.backward - self.forward

    def __fingerprint__(self):
        return {"setting": self.setting, "description": self.description,
                "baseline": repr(self.baseline), "amended": repr(self.amended),
                "forward": self.forward, "backward": self.backward}


@dataclass(frozen=True)
class RegulationDiff:
    """What changed, by module and by clause, with the residual named."""

    baseline_name: str
    amended_name: str
    baseline_total: float
    amended_total: float
    by_module: dict
    clauses: tuple

    @property
    def total_change(self) -> float:
        return self.amended_total - self.baseline_total

    @property
    def relative_change(self) -> float:
        """The movement as a fraction of the baseline, or 0 from nothing."""
        if self.baseline_total == 0.0:
            return 0.0
        return self.total_change / self.baseline_total

    @property
    def sum_of_clauses(self) -> float:
        """The clauses' forward effects added up.

        **Not** the total change, except by coincidence. The gap is
        :attr:`interaction`, and the whole reason this property exists
        separately is so the two can be compared rather than confused.
        """
        return float(sum(c.forward for c in self.clauses))

    @property
    def interaction(self) -> float:
        """The part of the movement no single clause accounts for."""
        return self.total_change - self.sum_of_clauses

    @property
    def driver(self) -> str | None:
        """The clause with the largest forward effect by magnitude.

        ``None`` where the interaction exceeds every clause, because then
        there is no driver — the amendment moved the number as a package
        and naming one clause would be reporting an artefact of the
        decomposition rather than a fact about the text.
        """
        if not self.clauses:
            return None
        largest = max(self.clauses, key=lambda c: abs(c.forward))
        if abs(self.interaction) > abs(largest.forward):
            return None
        return largest.setting

    def module_changes(self) -> dict:
        """Per sub-module movement, amended less baseline."""
        return {name: after - before
                for name, (before, after) in self.by_module.items()}

    def reconciles(self, tolerance: float = 1e-9) -> bool:
        """Clauses plus interaction equal the total change.

        True by construction — the interaction is defined as the residual —
        and asserted anyway, because the property that makes the residual
        meaningful is that nothing else has been dropped on the way.
        """
        scale = max(1.0, abs(self.baseline_total), abs(self.amended_total))
        return abs(self.sum_of_clauses + self.interaction
                   - self.total_change) <= tolerance * scale

    def to_dict(self) -> dict:
        return {
            "baseline": self.baseline_name,
            "amended": self.amended_name,
            "baseline_total": self.baseline_total,
            "amended_total": self.amended_total,
            "total_change": self.total_change,
            "relative_change": self.relative_change,
            "sum_of_clauses": self.sum_of_clauses,
            "interaction": self.interaction,
            "driver": self.driver,
            "by_module": {name: {"baseline": before, "amended": after,
                                 "change": after - before}
                          for name, (before, after) in self.by_module.items()},
            "clauses": [{"setting": c.setting, "description": c.description,
                         "baseline": repr(c.baseline),
                         "amended": repr(c.amended),
                         "forward": c.forward, "backward": c.backward,
                         "interaction": c.interaction}
                        for c in self.clauses],
        }

    def __repr__(self) -> str:
        return (f"RegulationDiff({self.baseline_name} → {self.amended_name}: "
                f"{self.total_change:+,.2f}, {len(self.clauses)} clause(s), "
                f"interaction {self.interaction:+,.2f})")

    def __fingerprint__(self):
        return self.to_dict()


def divergent_settings(baseline, amended) -> dict:
    """The named settings on which two dated texts disagree.

    ``{setting: (baseline value, amended value)}``, from each text's own
    ``options()``. A setting one text has and the other does not is a
    disagreement about the shape of the regime rather than about a
    parameter, and it is refused rather than diffed.
    """
    before, after = baseline.options(), amended.options()
    if set(before) != set(after):
        missing = sorted(set(before) ^ set(after))
        raise RegDiffError(
            f"the two texts do not describe the same settings: {missing} "
            f"appears in one and not the other. That is a difference in what "
            f"the regime *is*, not in what it is calibrated to, and this "
            f"module has no way to throw such a clause one at a time."
        )
    return {name: (before[name], after[name])
            for name in sorted(before) if before[name] != after[name]}


def regulation_diff(run: Callable, baseline, amended, *,
                    descriptions: dict | None = None) -> RegulationDiff:
    """Run one block under two dated texts and attribute the movement.

    ``run`` takes a calibration and returns a position — anything carrying
    ``scr`` and a ``modules`` mapping, which is what
    :func:`engine.report.market_risk.market_risk` returns. It is called
    ``2 + 2n`` times for ``n`` divergent clauses, twice per clause because
    forward and backward disagree and both are worth having.

    ``descriptions`` supplies the prose for each setting, normally
    :data:`engine.report.market_risk.LEGISLATIVE_OPTIONS`. A clause with no
    description is reported with its bare name rather than refused — the
    number is the point and an undocumented setting is the caller's
    problem, not a reason to withhold the diff.
    """
    if baseline.name == amended.name:
        raise RegDiffError(
            f"both sides of the diff are {baseline.name!r}. A regime "
            f"compared with itself reports no change, which is true and "
            f"useless; pass the two texts you meant."
        )
    divergent = divergent_settings(baseline, amended)
    if not divergent:
        raise RegDiffError(
            f"{baseline.name!r} and {amended.name!r} are different texts "
            f"with identical settings. Either the amendment did not touch "
            f"anything this module parameterises — in which case the "
            f"calibrations are the wrong place to look for it — or one of "
            f"them was built from the other by name alone."
        )

    before = run(baseline)
    after = run(amended)
    modules = sorted(set(before.modules) | set(after.modules))
    by_module = {name: (float(before.modules.get(name, 0.0)),
                        float(after.modules.get(name, 0.0)))
                 for name in modules}

    base_total = float(before.scr)
    amended_total = float(after.scr)
    prose = descriptions or {}
    clauses = []
    for setting, (was, becomes) in divergent.items():
        one_on = run(baseline.variant(**{setting: becomes}))
        one_off = run(amended.variant(**{setting: was}))
        clauses.append(ClauseEffect(
            setting=setting,
            description=prose.get(setting, ""),
            baseline=was, amended=becomes,
            forward=float(one_on.scr) - base_total,
            backward=amended_total - float(one_off.scr),
        ))

    return RegulationDiff(
        baseline_name=baseline.name, amended_name=amended.name,
        baseline_total=base_total, amended_total=amended_total,
        by_module=by_module, clauses=tuple(clauses),
    )


def market_risk_diff(baseline=None, amended=None, **position) -> RegulationDiff:
    """:func:`regulation_diff` over the Solvency II market risk module.

    The convenience wrapper, because that is the one dated pair in the
    library today. ``position`` is passed straight to
    :func:`engine.report.market_risk.market_risk`, minus the calibration,
    which is what this varies.
    """
    from engine.report.market_risk import (
        DELEGATED_2015, DELEGATED_2026, LEGISLATIVE_OPTIONS, market_risk,
    )

    baseline = baseline if baseline is not None else DELEGATED_2015
    amended = amended if amended is not None else DELEGATED_2026
    if "calibration" in position:
        raise RegDiffError(
            "the calibration is what this diff varies; pass baseline= and "
            "amended= instead of pinning one"
        )
    return regulation_diff(
        lambda calibration: market_risk(calibration=calibration, **position),
        baseline, amended, descriptions=LEGISLATIVE_OPTIONS,
    )


def clause_table(diff: RegulationDiff) -> np.ndarray:
    """``(n_clauses, 3)`` of forward, backward and interaction.

    A plain array for a report writer to format, ordered as
    ``diff.clauses``. Kept NumPy-only, per §1.4 — nothing in
    :mod:`engine.report` reaches for a dataframe.
    """
    if not diff.clauses:
        return np.zeros((0, 3), dtype=np.float64)
    return np.array([[c.forward, c.backward, c.interaction]
                     for c in diff.clauses], dtype=np.float64)
