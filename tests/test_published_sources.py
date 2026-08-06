"""The engine against numbers nobody here wrote.

Every other test in this suite checks the engine against itself — against a
closed form derived in the same repository, against a second executor
running the same model code, against a golden value computed by hand by
whoever wrote the test. That is most of what validation can be, and it has
one blind spot it cannot see past: an error in the *understanding* of a
method reproduces perfectly across every implementation of that
understanding.

This file closes a little of that gap. The figures below are published, by
named authors, in documents cited in `docs/sources/`, and none of them were
produced here. A disagreement is either a bug in this engine or a
misreading of a source, and both are worth a morning.

The sources are logged in `docs/sources/README.md` with provenance and
retrieval notes. Where a source is recorded but *unverified* — the primary
text could not be machine-read in the session that recorded it — the log
says so and this file does not assert against it, because an unverified
number dressed as a passing test is worse than no test.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.report.incurred_claims import ChainLadder, Triangle, development_factors

# --------------------------------------------------------------------------
# Mack (1993), ASTIN Bulletin 23(2) — the Taylor–Ashe triangle
# docs/sources/mack-1993-chain-ladder.md
# --------------------------------------------------------------------------

# The figures themselves live in tests/conftest.py, reached through the
# `published` fixture, so the two reserving suites share one transcription
# rather than each keeping a copy. The citations and the assertions stay
# here, which is what this file is for.


@pytest.fixture(scope="module")
def taylor_ashe(published):
    return ChainLadder(Triangle(published.rows, cumulative=True),
                       method="volume")


def test_the_development_factors_are_macks(taylor_ashe, published):
    """Table 1's factors, to the precision the paper prints them.

    Mack quotes three or four significant figures, so that is the tolerance
    — asserting more would be asserting against rounding rather than
    against the method.
    """
    ours = taylor_ashe.factors
    assert len(ours) == len(published.factors)
    for k, (published, got) in enumerate(zip(published.factors, ours),
                                         start=1):
        # 3 s.f. on the first factor (3.49), 4 s.f. on the rest.
        places = 3 if published >= 10 or k == 1 else 4
        assert round(got, places - len(str(int(published)))) == pytest.approx(
            published, abs=5e-3), f"factor {k}"


def test_the_reserves_by_accident_year_are_macks(taylor_ashe, published):
    """Table 2's chain-ladder column. The paper rounds to the nearest
    thousand, so a reserve of 95 carries half a percent of rounding on its
    own — which is why the tolerance is relative and generous at the small
    end and why the total is checked separately and tightly."""
    ours = taylor_ashe.reserve[1:] / 1000.0
    assert len(ours) == len(published.reserves)
    for i, (published, got) in enumerate(zip(published.reserves, ours),
                                         start=2):
        tolerance = max(0.5 / published, 1e-4)   # the paper's own rounding
        assert got == pytest.approx(published, rel=tolerance), \
            f"accident year {i}"


def test_the_overall_reserve_is_macks_to_the_rounding_he_published(
        taylor_ashe, published):
    """The headline number, and the tightest thing this source can hold the
    engine to: 18,681 thousand, rounded to the nearest thousand, so any
    total inside half a thousand of it agrees exactly."""
    total = taylor_ashe.total_reserve / 1000.0
    assert total == pytest.approx(published.total, abs=0.5)
    # Tighter than the tolerance, on the numbers as they actually come out.
    assert abs(total - published.total) < 0.2


def test_the_ultimates_add_up_to_the_reserve_plus_what_is_paid(taylor_ashe, published):
    """An internal identity, checked here rather than in the LIC suite
    because it is what makes the comparison above meaningful: if the
    reserve were not ultimate-less-paid, agreeing with Mack's reserve would
    be agreeing about a different quantity."""
    latest = taylor_ashe.triangle.latest()
    assert taylor_ashe.reserve == pytest.approx(
        taylor_ashe.ultimates - latest, rel=0, abs=1e-9)
    assert taylor_ashe.total_reserve == pytest.approx(
        float(taylor_ashe.ultimates.sum() - latest.sum()), rel=1e-12)


def test_the_simple_average_disagrees_and_that_is_the_point(taylor_ashe, published):
    """Mack's figures are the volume-weighted chain ladder. The unweighted
    factors are a different estimator and give a different answer on the
    same triangle — so the check above is a check on *the method named*,
    not on chain ladder in general."""
    simple = ChainLadder(Triangle(published.rows, cumulative=True),
                         method="simple")
    assert simple.total_reserve != taylor_ashe.total_reserve
    # Both are defensible; the paper's own Table 2 spans 16,652 to 22,301
    # across six methods, so a few percent between two of them is expected.
    assert abs(simple.total_reserve - taylor_ashe.total_reserve) \
        / taylor_ashe.total_reserve < 0.25


def test_the_standard_errors_are_macks(taylor_ashe, published):
    """Table 3, p. 222 — the reason Mack's paper is famous.

    This test used to assert the opposite: that `mack_standard_error` did
    **not** exist, and that the targets were on record with nothing claiming
    to have met them. C5 landed, so it now asserts the thing it was holding
    a place for.

    That ordering is the point and is worth stating. The targets were
    transcribed from the paper before any code could produce them, so there
    was no implementation to tune them toward — which is the difference
    between a published check and a regression test of one's own output.
    """
    from engine.report.incurred_claims import mack_standard_error

    mack = mack_standard_error(taylor_ashe)
    assert len(published.standard_error_pct) == len(published.reserves)

    ours = [round(100 * cv) for cv in mack.coefficient_of_variation[1:]]
    assert tuple(ours) == published.standard_error_pct
    assert round(100 * mack.total_coefficient_of_variation) == \
        published.total_standard_error_pct == 13

    # The paper's own point about the total: it is not the periods added in
    # quadrature, because they share their development factors.
    assert mack.total > mack.quadrature_total


# --------------------------------------------------------------------------
# Solvency II: Article 164(3)'s market risk correlation matrix
# --------------------------------------------------------------------------

#: Article 164(3) as published, order (interest, equity, property, spread,
#: concentration, currency), with the direction-dependent cells written as
#: ``None`` and supplied separately — they are parameter *A*, and writing a
#: number there would be recording one direction as though it were the rule.
#: See docs/sources/solvency2-market-correlation.md for the two published
#: reproductions this is transcribed from.
PUBLISHED_MARKET_CORRELATION = (
    (1.00, None, None, None, 0.00, 0.25),
    (None, 1.00, 0.75, 0.75, 0.00, 0.25),
    (None, 0.75, 1.00, 0.50, 0.00, 0.25),
    (None, 0.75, 0.50, 1.00, 0.00, 0.25),
    (0.00, 0.00, 0.00, 0.00, 1.00, 0.00),
    (0.25, 0.25, 0.25, 0.25, 0.00, 1.00),
)

#: "The parameter A shall be equal to 0 where the capital requirement for
#: interest rate risk set out in Article 165 is the capital requirement
#: referred to in point (a) of that Article" — the upward shock — "In all
#: other cases, the parameter A shall be equal to 0,5."
PUBLISHED_PARAMETER_A = {"up": 0.0, "down": 0.5}


def test_the_market_correlation_matrix_is_the_regulations():
    """Article 164(3), cell by cell, in both interest directions.

    The check `docs/sources/solvency2-market-correlation.md` could not
    support while EUR-Lex returned an empty body to every automated fetch.
    The matrix has since been read from the UK Government's reproduction of
    the Regulation as adopted, with EIOPA's Single Rulebook independently
    confirming parameter A's definition.

    Asserted against the *published* table rather than against the module's
    own constants — restating the module's numbers back at it would only
    check that they had been typed twice, which is the failure mode this
    whole file exists to avoid.
    """
    from engine.report.market_risk import (
        DELEGATED_2015, MARKET_RISKS, market_correlation,
    )

    assert MARKET_RISKS == ("interest", "equity", "property", "spread",
                            "concentration", "currency")
    for direction, a in PUBLISHED_PARAMETER_A.items():
        matrix = market_correlation(DELEGATED_2015,
                                    interest_direction=direction)
        for i, row in enumerate(PUBLISHED_MARKET_CORRELATION):
            for j, published in enumerate(row):
                expected = a if published is None else published
                assert matrix.matrix[i][j] == pytest.approx(
                    expected, rel=0, abs=1e-12), (
                    f"{MARKET_RISKS[i]}/{MARKET_RISKS[j]} is "
                    f"{matrix.matrix[i][j]} on the {direction} scenario; "
                    f"Article 164(3) publishes {expected}")


def test_only_the_spread_cell_moves_under_the_amending_regulation():
    """2026/269 splits Article 164(3)'s spread cell out of *A* as a separate
    parameter *B*, and leaves *A* against equity and property alone.

    "The amendment changed Article 164(3)" reads as though the whole row
    moved. It did not, and a source file that recorded the amendment without
    saying which cell it touched would leave a reader to assume the wider
    change.
    """
    from engine.report.market_risk import (
        DELEGATED_2015, DELEGATED_2026, MARKET_RISKS, market_correlation,
    )

    interest, spread = (MARKET_RISKS.index("interest"),
                        MARKET_RISKS.index("spread"))
    equity = MARKET_RISKS.index("equity")
    before = market_correlation(DELEGATED_2015, interest_direction="down")
    after = market_correlation(DELEGATED_2026, interest_direction="down")

    assert before.matrix[interest][spread] == pytest.approx(0.50)
    assert after.matrix[interest][spread] == pytest.approx(0.25)
    # A, against equity and property, is 0.5 under both texts.
    assert after.matrix[interest][equity] == pytest.approx(
        before.matrix[interest][equity]) == pytest.approx(0.50)
