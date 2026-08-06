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

#: Table 1, p. 221. Cumulative paid claims, ten accident years.
TAYLOR_ASHE = [
    [357848, 1124788, 1735330, 2218270, 2745596, 3319994, 3466336, 3606286,
     3833515, 3901463],
    [352118, 1236139, 2170033, 3353322, 3799067, 4120063, 4647867, 4914039,
     5339085],
    [290507, 1292306, 2218525, 3235179, 3985995, 4132918, 4628910, 4909315],
    [310608, 1418858, 2195047, 3757447, 4029929, 4381982, 4588268],
    [443160, 1136350, 2128333, 2897821, 3402672, 3873311],
    [396132, 1333217, 2180715, 2985752, 3691712],
    [440832, 1288463, 2419861, 3483130],
    [359480, 1421128, 2864498],
    [376686, 1363294],
    [344014],
]

#: p. 221, quoted to three or four significant figures.
PUBLISHED_FACTORS = (3.49, 1.75, 1.46, 1.174, 1.104, 1.086, 1.054, 1.077,
                     1.018)

#: Table 2, p. 221, chain-ladder column, in thousands, accident years 2–10.
PUBLISHED_RESERVES = (95, 470, 710, 985, 1419, 2178, 3920, 4279, 4626)
PUBLISHED_TOTAL = 18681

#: Table 3, p. 222 — standard error as a percentage of each reserve. Not
#: asserted: the repo has no Mack variability (execution plan C5). Recorded
#: so that C5 arrives with a published target rather than a self-made one.
PUBLISHED_STANDARD_ERROR_PCT = (80, 26, 19, 27, 29, 26, 22, 23, 29)
PUBLISHED_TOTAL_STANDARD_ERROR_PCT = 13


@pytest.fixture(scope="module")
def taylor_ashe():
    return ChainLadder(Triangle(TAYLOR_ASHE, cumulative=True), method="volume")


def test_the_development_factors_are_macks(taylor_ashe):
    """Table 1's factors, to the precision the paper prints them.

    Mack quotes three or four significant figures, so that is the tolerance
    — asserting more would be asserting against rounding rather than
    against the method.
    """
    ours = taylor_ashe.factors
    assert len(ours) == len(PUBLISHED_FACTORS)
    for k, (published, got) in enumerate(zip(PUBLISHED_FACTORS, ours),
                                         start=1):
        # 3 s.f. on the first factor (3.49), 4 s.f. on the rest.
        places = 3 if published >= 10 or k == 1 else 4
        assert round(got, places - len(str(int(published)))) == pytest.approx(
            published, abs=5e-3), f"factor {k}"


def test_the_reserves_by_accident_year_are_macks(taylor_ashe):
    """Table 2's chain-ladder column. The paper rounds to the nearest
    thousand, so a reserve of 95 carries half a percent of rounding on its
    own — which is why the tolerance is relative and generous at the small
    end and why the total is checked separately and tightly."""
    ours = taylor_ashe.reserve[1:] / 1000.0
    assert len(ours) == len(PUBLISHED_RESERVES)
    for i, (published, got) in enumerate(zip(PUBLISHED_RESERVES, ours),
                                         start=2):
        tolerance = max(0.5 / published, 1e-4)   # the paper's own rounding
        assert got == pytest.approx(published, rel=tolerance), \
            f"accident year {i}"


def test_the_overall_reserve_is_macks_to_the_rounding_he_published(
        taylor_ashe):
    """The headline number, and the tightest thing this source can hold the
    engine to: 18,681 thousand, rounded to the nearest thousand, so any
    total inside half a thousand of it agrees exactly."""
    total = taylor_ashe.total_reserve / 1000.0
    assert total == pytest.approx(PUBLISHED_TOTAL, abs=0.5)
    # Tighter than the tolerance, on the numbers as they actually come out.
    assert abs(total - PUBLISHED_TOTAL) < 0.2


def test_the_ultimates_add_up_to_the_reserve_plus_what_is_paid(taylor_ashe):
    """An internal identity, checked here rather than in the LIC suite
    because it is what makes the comparison above meaningful: if the
    reserve were not ultimate-less-paid, agreeing with Mack's reserve would
    be agreeing about a different quantity."""
    latest = taylor_ashe.triangle.latest()
    assert taylor_ashe.reserve == pytest.approx(
        taylor_ashe.ultimates - latest, rel=0, abs=1e-9)
    assert taylor_ashe.total_reserve == pytest.approx(
        float(taylor_ashe.ultimates.sum() - latest.sum()), rel=1e-12)


def test_the_simple_average_disagrees_and_that_is_the_point(taylor_ashe):
    """Mack's figures are the volume-weighted chain ladder. The unweighted
    factors are a different estimator and give a different answer on the
    same triangle — so the check above is a check on *the method named*,
    not on chain ladder in general."""
    simple = ChainLadder(Triangle(TAYLOR_ASHE, cumulative=True),
                         method="simple")
    assert simple.total_reserve != taylor_ashe.total_reserve
    # Both are defensible; the paper's own Table 2 spans 16,652 to 22,301
    # across six methods, so a few percent between two of them is expected.
    assert abs(simple.total_reserve - taylor_ashe.total_reserve) \
        / taylor_ashe.total_reserve < 0.25


def test_the_standard_errors_are_recorded_but_nothing_computes_them_yet():
    """The honest half of this source.

    Table 3 is the reason Mack's paper is famous, and the repo cannot
    reproduce it: reserve variability is execution-plan item C5 and is
    unstarted. This asserts that the targets are on record and that nothing
    here quietly claims to have met them.
    """
    assert len(PUBLISHED_STANDARD_ERROR_PCT) == len(PUBLISHED_RESERVES)
    assert PUBLISHED_TOTAL_STANDARD_ERROR_PCT == 13
    import engine.report.incurred_claims as lic

    for absent in ("mack_standard_error", "standard_error", "bootstrap"):
        assert not hasattr(lic, absent), (
            f"{absent} exists now — C5 has landed, so wire it to "
            f"PUBLISHED_STANDARD_ERROR_PCT and delete this test"
        )
