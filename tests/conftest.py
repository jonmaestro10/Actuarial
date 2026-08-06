"""Fixtures shared across test modules.

One home for the published figures the reserving suites are checked against.
They lived in ``tests/test_published_sources.py`` and were imported from
there by ``tests/test_gi_reserving.py``, which worked locally and **not in
CI**: ``tests`` is not an importable package, so a cross-test-module import
depends on how ``sys.path`` happened to be assembled. A conftest is what
pytest provides for exactly this, and it needs no import at all.

Provenance for every number here is
``docs/sources/mack-1993-chain-ladder.md``; the transcription and the
citations stay with the assertions in ``test_published_sources.py``.
"""

from __future__ import annotations

import pytest

# --------------------------------------------------------------------------
# Mack (1993), ASTIN Bulletin 23(2) — the Taylor-Ashe triangle
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

#: Table 3, p. 222 — standard error as a percentage of each reserve.
#: Asserted since C5 (RFC-054); the recording came first, which is the point
#: — the target was published before the implementation existed, so there
#: was nothing to tune it toward.
PUBLISHED_STANDARD_ERROR_PCT = (80, 26, 19, 27, 29, 26, 22, 23, 29)
PUBLISHED_TOTAL_STANDARD_ERROR_PCT = 13


class _Published:
    """The paper's figures, reachable without importing a test module."""

    rows = TAYLOR_ASHE
    factors = PUBLISHED_FACTORS
    reserves = PUBLISHED_RESERVES
    total = PUBLISHED_TOTAL
    standard_error_pct = PUBLISHED_STANDARD_ERROR_PCT
    total_standard_error_pct = PUBLISHED_TOTAL_STANDARD_ERROR_PCT


@pytest.fixture(scope="session")
def published():
    """Mack (1993)'s published figures, as a fixture.

    A fixture rather than a module-level constant somebody imports, because
    ``tests`` is not a package: ``from tests.test_published_sources import
    ...`` resolves locally and raises ``ModuleNotFoundError`` in CI, which is
    how this arrived. Fixtures are the mechanism pytest supplies for sharing
    across test modules and they need no import to work.
    """
    return _Published
