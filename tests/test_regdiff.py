"""Two texts, one block, and the residual that must be named.

Execution plan §8, item F2. The report over `engine/report/market_risk.py`'s
two dated calibrations, and the thing it exists to stop: a per-clause table
that does not add up to the movement it purports to explain.

RFC-026 measured that already — 2026/269's five clauses thrown one at a time
sum to +1.35 on a block they move by +6.49 together. So every test here that
touches attribution also checks the residual, because a decomposition whose
error is four times its own total is not a small imprecision, it is the
finding, and a report that omitted it would let a reader name the largest
row as the driver of a movement it barely caused.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.data.rates import YieldCurve
from engine.report.market_risk import (
    CALIBRATIONS,
    DELEGATED_2015,
    DELEGATED_2026,
    LEGISLATIVE_OPTIONS,
    EquityExposure,
    market_risk,
)
from engine.report.regdiff import (
    ClauseEffect,
    RegDiffError,
    RegulationDiff,
    clause_table,
    divergent_settings,
    market_risk_diff,
    regulation_diff,
)
from engine.report.embedded_value import duration_matched_assets

FLAT = YieldCurve.flat(0.03, freq=1)
ANNUITY = np.full(25, 100.0)


def position_kwargs():
    """A balance sheet with all six sub-modules alive.

    Every sub-module has to be non-zero or the per-module report is a table
    of zeros with one row in it, and the aggregation the clauses interact
    through never gets exercised.
    """
    assets = duration_matched_assets(ANNUITY, FLAT, short=5, long=20)
    periods = np.flatnonzero(assets > 0.0) + 1
    factors = FLAT.discount_factors(int(periods.max()) + 1)
    values = assets[periods - 1] * factors[periods]
    return dict(
        assets=assets, liabilities=ANNUITY, curve=FLAT,
        equity=EquityExposure(type1=200.0, type2=50.0), symmetric=0.02,
        property_value=150.0, currency_positions={"USD": 120.0},
        spread=(values, periods.astype(float), np.full(periods.size, 2)),
        concentration=(values, np.full(periods.size, 2), float(values.sum())),
    )


# --------------------------------------------------------------------------
# What the two texts disagree about
# --------------------------------------------------------------------------

def test_the_divergent_clauses_are_the_amendments_own_five():
    """2026/269 moves the interest tables, deletes Article 166(2)'s minimum,
    replaces Article 167(2)'s negative-rate rule, widens Article 172(4)'s
    corridor and splits Article 164(3)'s spread correlation out.

    Discovered from the calibrations rather than listed here, so a sixth
    clause added to the module appears in every diff without this file
    being touched — and so a clause silently dropped fails.

    Note which correlation is *not* here: `interest_correlation`, parameter
    A against equity and property, is 0.5 under both texts. 2026/269 splits
    only the spread cell out as B, so a reader who took "the amendment
    changed Article 164(3)" to mean the whole row would be wrong, and the
    diff says which cell moved."""
    divergent = divergent_settings(DELEGATED_2015, DELEGATED_2026)
    assert set(divergent) == {
        "interest_tables", "minimum_increase", "negative_rates",
        "symmetric_cap", "interest_spread_correlation",
    }
    assert "interest_correlation" not in divergent
    assert divergent["symmetric_cap"] == (0.10, 0.13)
    assert divergent["minimum_increase"] == (0.01, None)
    assert divergent["interest_spread_correlation"] == (0.50, 0.25)
    assert set(divergent) <= set(LEGISLATIVE_OPTIONS)


def test_a_regime_compared_with_itself_is_refused():
    """True and useless, and the shape of a caller who passed the same
    variable twice."""
    with pytest.raises(RegDiffError, match="compared with itself"):
        market_risk_diff(baseline=DELEGATED_2015, amended=DELEGATED_2015,
                         **position_kwargs())


def test_two_texts_with_identical_settings_are_refused():
    """A renamed calibration is not an amendment. Reporting a diff of zero
    would say the amendment did nothing, when what happened is that it does
    not live in the settings this module parameterises — which is a
    different statement and the one worth making."""
    twin = DELEGATED_2015.variant("2015/35 (renamed)")
    with pytest.raises(RegDiffError, match="identical settings"):
        market_risk_diff(baseline=DELEGATED_2015, amended=twin,
                         **position_kwargs())


def test_texts_that_do_not_describe_the_same_settings_are_refused():
    """A setting one text has and the other does not is a disagreement about
    the *shape* of the regime, and there is no way to throw such a clause on
    its own. Refused rather than diffed around."""
    class Lopsided:
        name = "lopsided"

        def options(self):
            return {"symmetric_cap": 0.10}

    with pytest.raises(RegDiffError, match="do not describe the same"):
        divergent_settings(DELEGATED_2015, Lopsided())


# --------------------------------------------------------------------------
# The residual, which is the point
# --------------------------------------------------------------------------

def test_the_clauses_and_the_interaction_add_to_the_whole_movement():
    """The invariant that makes the residual meaningful: nothing was dropped
    on the way. Exact, not approximate — the interaction is *defined* as the
    residual, so a failure here means a clause was counted twice or a total
    came from a different run."""
    diff = market_risk_diff(**position_kwargs())
    assert diff.reconciles()
    assert diff.sum_of_clauses + diff.interaction == pytest.approx(
        diff.total_change, rel=0, abs=1e-9)
    assert diff.amended_total - diff.baseline_total == diff.total_change


def test_the_clauses_do_not_add_up_and_the_report_says_so():
    """**The finding, asserted.** RFC-026 measured 2026/269's clauses at
    +1.35 one at a time against +6.49 together on a block. The same shape
    has to survive here or the report is not measuring what RFC-026
    measured.

    A test that only checked `reconciles()` would pass against a module that
    silently normalised the clauses to sum to the total — which is the
    tempting fix and the wrong one, because it would attribute the
    interaction to whichever clause happened to be largest."""
    diff = market_risk_diff(**position_kwargs())
    assert diff.total_change > 0.0
    assert diff.sum_of_clauses != pytest.approx(diff.total_change, rel=1e-6)
    # The residual is not a rounding: it is a substantial share of the move.
    assert abs(diff.interaction) > 0.1 * abs(diff.total_change)


def test_forward_and_backward_disagree_for_an_interacting_clause():
    """Two natural decompositions, both reported, because they differ.

    `forward` is what a clause does to the world as it stands; `backward` is
    what it contributes once the rest of the amendment has landed. Choosing
    one and calling it *the* effect would hide exactly the clause that is
    inert alone and material in company."""
    diff = market_risk_diff(**position_kwargs())
    gaps = {c.setting: c.interaction for c in diff.clauses}
    assert any(abs(gap) > 1e-6 for gap in gaps.values())
    for clause in diff.clauses:
        assert clause.interaction == clause.backward - clause.forward


def test_a_clause_that_acts_alone_agrees_both_ways():
    """The control. Diff a text against itself-plus-one-clause and there is
    nothing for that clause to interact with, so forward equals backward
    exactly and the interaction is zero.

    Without this the disagreement above could be an artefact of the two
    measurements rather than a fact about the amendment."""
    alone = DELEGATED_2015.variant("2015/35 + wider corridor",
                                   symmetric_cap=0.13)
    diff = market_risk_diff(baseline=DELEGATED_2015, amended=alone,
                            **position_kwargs())
    assert len(diff.clauses) == 1
    only = diff.clauses[0]
    assert only.setting == "symmetric_cap"
    assert only.forward == pytest.approx(only.backward, rel=0, abs=1e-9)
    assert only.interaction == pytest.approx(0.0, rel=0, abs=1e-9)
    assert diff.interaction == pytest.approx(0.0, rel=0, abs=1e-9)
    assert diff.driver == "symmetric_cap"


def test_no_driver_is_named_when_the_package_moved_the_number():
    """`driver` is withheld where the interaction exceeds every clause.

    Naming the largest row there would report an artefact of the
    decomposition as a fact about the text, and the honest answer — that the
    amendment has to be read as a package — is one a `None` can carry and a
    number cannot."""
    packaged = RegulationDiff(
        baseline_name="a", amended_name="b",
        baseline_total=100.0, amended_total=140.0, by_module={},
        clauses=(ClauseEffect("one", "", 0, 1, forward=2.0, backward=20.0),
                 ClauseEffect("two", "", 0, 1, forward=3.0, backward=25.0)),
    )
    assert packaged.sum_of_clauses == 5.0
    assert packaged.interaction == 35.0
    assert packaged.driver is None
    assert packaged.reconciles()

    additive = RegulationDiff(
        baseline_name="a", amended_name="b",
        baseline_total=100.0, amended_total=105.0, by_module={},
        clauses=(ClauseEffect("one", "", 0, 1, forward=2.0, backward=2.0),
                 ClauseEffect("two", "", 0, 1, forward=3.0, backward=3.0)),
    )
    assert additive.interaction == 0.0
    assert additive.driver == "two"


# --------------------------------------------------------------------------
# Per module, and the report itself
# --------------------------------------------------------------------------

def test_every_sub_module_is_reported_on_both_texts():
    """"per-module SCR deltas" — the plan's words. A module that moved and a
    module that did not both have to appear, because the second is the
    answer to "did this amendment touch our property book"."""
    diff = market_risk_diff(**position_kwargs())
    before = market_risk(calibration=DELEGATED_2015, **position_kwargs())
    after = market_risk(calibration=DELEGATED_2026, **position_kwargs())

    assert set(diff.by_module) == set(before.modules) == set(after.modules)
    for name, (base, amended) in diff.by_module.items():
        assert base == pytest.approx(before.modules[name], rel=0, abs=1e-12)
        assert amended == pytest.approx(after.modules[name], rel=0, abs=1e-12)

    changes = diff.module_changes()
    assert changes["interest"] > 0.0             # the rewritten sub-module
    assert changes["property"] == 0.0            # Article 174 is untouched
    assert changes["currency"] == 0.0


def test_the_totals_are_the_positions_own_scr():
    """The diff must not recompute the aggregate its own way. If it did, a
    change to `MarketRiskPosition.scr` would leave the report quietly
    disagreeing with every other number in the system."""
    diff = market_risk_diff(**position_kwargs())
    assert diff.baseline_total == market_risk(
        calibration=DELEGATED_2015, **position_kwargs()).scr
    assert diff.amended_total == market_risk(
        calibration=DELEGATED_2026, **position_kwargs()).scr
    assert diff.relative_change == pytest.approx(
        diff.total_change / diff.baseline_total)


def test_pinning_the_calibration_is_refused():
    """It is the thing being varied. A caller who passed one has misread
    what the function does, and honouring it would report a diff of a text
    against itself under another name."""
    with pytest.raises(RegDiffError, match="what this diff varies"):
        market_risk_diff(calibration=DELEGATED_2015, **position_kwargs())


def test_the_report_carries_its_prose_and_survives_a_round_trip():
    """Every clause names its Article, because a setting name is not a
    citation and the reader of a diff is being asked to believe a number
    about a text they have not opened."""
    diff = market_risk_diff(**position_kwargs())
    out = diff.to_dict()
    assert out["baseline"] == "2015/35" and out["amended"] == "2026/269"
    assert out["interaction"] == diff.interaction
    for clause in out["clauses"]:
        assert "Article" in clause["description"]
    assert set(out["by_module"]) == set(diff.by_module)

    table = clause_table(diff)
    assert table.shape == (len(diff.clauses), 3)
    assert table[:, 0].sum() == pytest.approx(diff.sum_of_clauses)
    assert np.allclose(table[:, 2], table[:, 1] - table[:, 0])


def test_the_diff_is_generic_over_anything_with_modules_and_an_scr():
    """`run` is a callable and the module never asks what a calibration is,
    so the next dated pair needs no change here. Asserted with a toy regime
    rather than assumed from the signature."""
    class Toy:
        def __init__(self, name, a, b):
            self.name, self.a, self.b = name, a, b

        def options(self):
            return {"a": self.a, "b": self.b}

        def variant(self, name=None, **changes):
            return Toy(name or self.name, changes.get("a", self.a),
                       changes.get("b", self.b))

    class Position:
        def __init__(self, value):
            self.modules = {"only": value}
            self.scr = value

    # Deliberately multiplicative, so the clauses cannot be additive.
    diff = regulation_diff(lambda c: Position(c.a * c.b),
                           Toy("before", 2.0, 3.0), Toy("after", 4.0, 5.0))
    assert diff.baseline_total == 6.0 and diff.amended_total == 20.0
    assert diff.total_change == 14.0
    forward = {c.setting: c.forward for c in diff.clauses}
    assert forward == {"a": 6.0, "b": 4.0}       # 12−6 and 10−6
    assert diff.sum_of_clauses == 10.0
    assert diff.interaction == 4.0               # and it is named
    assert diff.reconciles()


def test_both_shipped_calibrations_can_be_diffed_in_either_direction():
    """The report is not built around one being 'the new one'. Reversing the
    pair has to negate the movement, or the attribution has a direction
    baked into it that the caller did not ask for."""
    forward = market_risk_diff(baseline=DELEGATED_2015, amended=DELEGATED_2026,
                               **position_kwargs())
    backward = market_risk_diff(baseline=DELEGATED_2026, amended=DELEGATED_2015,
                                **position_kwargs())
    assert backward.total_change == pytest.approx(-forward.total_change,
                                                  rel=0, abs=1e-9)
    assert len(backward.clauses) == len(forward.clauses)
    assert backward.reconciles()
    assert len(CALIBRATIONS) == 2
