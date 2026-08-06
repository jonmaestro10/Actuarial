"""VM-22 §6.C.8.iii bands its second axis two different ways, and the
boundary the two share is the trap rather than the reassurance.

Run: ``python scripts/findings/vm22_contract_year_bands.py``
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np

from engine.report import vm22_prescribed as prescribed
from engine.report.vm22_prescribed import (
    FX_STANDARD_CONTRACT_YEARS,
    FX_SUBSTANDARD_CONTRACT_YEARS,
    fx_factor,
)
from scripts.findings._finding import Finding, show

FINDING = Finding(
    slug="vm22-contract-year-bands",
    claim=("Table 6.9 bands contract years 1-5/6-10/>=11 and Tables 6.10 "
           "and 6.11 band them 1-10/11-20/21-30/>=31. Contract year 11 opens "
           "the third band of one and the second of the others, so a band "
           "index computed against the wrong list stays in range and reads "
           "a real cell of a real table."),
    source="docs/rfc-071-structured-settlement-factors.md",
    regulation=("NAIC Valuation Manual, 1 January 2026 edition, VM-22 "
                "§6.C.8.iii, Tables 6.9 to 6.11"),
)

STANDARD = "structured_settlement_standard"


def demonstrate() -> dict:
    """Compute the wrong reading on purpose, so the gap is reportable."""
    age, year = 62, 11
    right = float(fx_factor(age, "F", category=STANDARD, contract_year=year))

    # The same lookup with the substandard banding applied to Table 6.9.
    wrong_band = int(np.searchsorted(
        np.asarray(FX_SUBSTANDARD_CONTRACT_YEARS), year, side="right")) - 1
    row = [r for r in prescribed._FX_SS_STANDARD if r[0] == age][0]
    wrong = float(row[1 + 2 * wrong_band])

    steps = {
        "table_6_9": {str(y): float(fx_factor(age, "F", category=STANDARD,
                                              contract_year=y))
                      for y in (1, 5, 6, 10, 11, 12)},
        "table_6_10": {str(y): float(fx_factor(
            age, "F", category="structured_settlement_substandard",
            contract_year=y, rate_up_years=5))
            for y in (1, 10, 11, 20, 21, 30, 31)},
    }
    return {
        "standard_bands": list(FX_STANDARD_CONTRACT_YEARS),
        "substandard_bands": list(FX_SUBSTANDARD_CONTRACT_YEARS),
        "shared_boundaries": sorted(set(FX_STANDARD_CONTRACT_YEARS)
                                    & set(FX_SUBSTANDARD_CONTRACT_YEARS)),
        "band_index_of_year_11": {
            "table_6_9": FX_STANDARD_CONTRACT_YEARS.index(11),
            "tables_6_10_and_6_11": FX_SUBSTANDARD_CONTRACT_YEARS.index(11),
        },
        "female_aged_62_contract_year_11": {
            "correct": right,
            "read_with_the_other_banding": wrong,
            "understatement": 1.0 - wrong / right,
        },
        "where_each_table_steps": steps,
    }


if __name__ == "__main__":
    show(FINDING, demonstrate())
