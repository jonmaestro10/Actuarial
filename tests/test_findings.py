"""The findings catalogue: every page has a script, and every script runs.

A finding that lives only in an RFC is an assertion. A finding with a script
beside it that CI runs is a **demonstration** — a reviewer can re-run it
against the current engine instead of trusting a paragraph, and if the engine
changes so that a finding stops reproducing, the build says so instead of the
document quietly becoming false.

This module holds three things:

- **The correspondence.** ``docs/findings/<slug>.md`` and
  ``scripts/findings/<slug>.py`` are one-to-one. A page without a script is
  an unbacked claim; a script without a page is a demonstration nobody can
  read. Either fails here.
- **The demonstrations.** Each script's ``demonstrate()`` is run and its
  specific claim asserted *here* rather than inside the script. A script that
  asserted its own claim would pass in CI while proving nothing, because it
  would be checking itself.
- **The floor.** The catalogue may not quietly empty out, and the set of
  slugs is pinned, so deleting a finding is a decision rather than an
  accident.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts" / "findings"
PAGES = ROOT / "docs" / "findings"

#: Pinned, so a finding cannot be dropped without editing this line. The
#: catalogue is meant to grow; shrinking it is the thing worth noticing.
CATALOGUED = {
    "aos-ordering",
    "hoist-boundary",
    "counterparty-band-cliff",
    "pool-of-one",
    "reduction-order",
    "representation-error",
    "vm22-contract-year-bands",
}


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(f"_finding_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def script_paths() -> list[Path]:
    return sorted(p for p in SCRIPTS.glob("*.py")
                  if not p.name.startswith("_"))


def test_the_catalogue_is_not_empty_and_has_not_shrunk():
    """A parametrised test over an empty directory collects nothing and is
    not a passing test. This is the guard that the parametrised cases below
    are actually running over something."""
    paths = script_paths()
    assert paths, "no finding scripts found — the catalogue has emptied out"
    slugs = {_load(p).FINDING.slug for p in paths}
    assert slugs == CATALOGUED, (
        f"catalogue changed: {slugs ^ CATALOGUED}. Adding a finding is "
        f"welcome and wants a line in CATALOGUED; removing one should be "
        f"deliberate."
    )


@pytest.mark.parametrize("path", script_paths(), ids=lambda p: p.stem)
def test_every_script_has_a_page_and_declares_itself(path):
    """The correspondence, in the direction that catches an unbacked page.

    The slug is the join between the two, so it is asserted against the
    filename as well: a script whose ``FINDING.slug`` drifted from its own
    name would satisfy the page check while pointing at someone else's page.
    """
    module = _load(path)
    finding = module.FINDING

    assert finding.slug == path.stem.replace("_", "-")
    assert finding.claim and finding.claim.rstrip().endswith(".")
    assert finding.source.startswith("docs/")
    assert (ROOT / finding.source).exists(), finding.source

    page = PAGES / f"{finding.slug}.md"
    assert page.exists(), f"{finding.slug} has no page at {page}"
    text = page.read_text()
    # The page must name its own script and its own source RFC, because a
    # reader who cannot re-run the demonstration is back to trusting prose —
    # which is the state this catalogue exists to leave.
    assert f"scripts/findings/{path.name}" in text, (
        f"{page.name} does not name the script that backs it, so a reader "
        f"cannot re-run the demonstration"
    )
    assert Path(finding.source).name in text, (
        f"{page.name} does not link {finding.source}"
    )
    assert text.lstrip().startswith("# ")
    assert "**Claim.**" in text


def test_every_page_has_a_script():
    """The other direction. A page whose script was deleted is a claim with
    nothing behind it, which is exactly what this catalogue exists not to
    be."""
    pages = {p.stem for p in PAGES.glob("*.md") if p.stem != "README"}
    scripts = {p.stem.replace("_", "-") for p in script_paths()}
    assert pages == scripts, f"pages without scripts: {pages - scripts}"


@pytest.mark.parametrize("path", script_paths(), ids=lambda p: p.stem)
def test_every_demonstration_still_runs(path):
    """Every script executes against the current engine and returns numbers.

    This is the regression half of the catalogue: a finding that stops
    reproducing has changed, and a changed finding is news whether the change
    is a fix or a defect."""
    numbers = _load(path).demonstrate()
    assert isinstance(numbers, dict) and numbers


# --------------------------------------------------------------------------
# The claims themselves, asserted here rather than inside the scripts
# --------------------------------------------------------------------------

def _numbers(stem: str) -> dict:
    return _load(SCRIPTS / f"{stem}.py").demonstrate()


def test_the_counterparty_cliff_is_a_cliff_and_the_upper_bound_is_not():
    """Article 200's lower boundary jumps by 14 percentage points of ΣLGD —
    3σ against 5σ at σ = 7% — while the upper one is continuous by
    construction, because 5 × 20% is exactly 100%. Somebody chose 5 and 20%
    so they would meet, and did not do the same at the bottom."""
    n = _numbers("counterparty_band_cliff")
    assert n["relative_jump"] == pytest.approx(2 / 3, abs=0.01)
    assert n["lower_boundary_gap_as_fraction_of_lgd"] == pytest.approx(0.14)
    assert n["upper_boundary_gap_as_fraction_of_lgd"] == pytest.approx(0.0)
    before, after = n["sigma_over_lgd_either_side"]
    assert before > 0.07 > after
    # The book barely moved; the requirement moved by a third.
    assert abs(before - after) < 1e-4


def test_a_pool_of_one_returns_a_plausible_and_wrong_number():
    """The shape of the finding is not that it errors — it is that it does
    **not**. Every policy sees a pool of itself, the run completes, and the
    number is the right order of magnitude and wrong by tens of per cent."""
    n = _numbers("pool_of_one")
    assert n["refused_by"], "the engine no longer refuses this"
    assert n["plausible"], "the wrong answer is meant to look ordinary"
    assert n["worst_relative_difference"] > 0.1
    assert n["policies_in_block"] > 1


def test_the_two_contract_year_bandings_disagree_at_the_boundary_they_share():
    """Contract year 11 opens Table 6.9's third band and the substandard
    tables' second. A band index computed against the wrong list is in range
    and reads a real cell — 170% where the table says 225%."""
    n = _numbers("vm22_contract_year_bands")
    assert n["shared_boundaries"] == [1, 11]
    assert n["band_index_of_year_11"]["table_6_9"] == 2
    assert n["band_index_of_year_11"]["tables_6_10_and_6_11"] == 1
    cell = n["female_aged_62_contract_year_11"]
    assert cell["correct"] == pytest.approx(2.25)
    assert cell["read_with_the_other_banding"] == pytest.approx(1.70)
    assert cell["understatement"] == pytest.approx(0.244, abs=0.001)


def test_the_representation_error_is_there_before_any_arithmetic():
    """``0.035`` is not 3.5%, and the difference compounds. Also carries the
    measured bound between the float engine and 34-digit decimal."""
    n = _numbers("representation_error")
    assert n["as_written"] == "0.035"
    assert n["as_stored"].startswith("0.03500000000000000333")
    assert n["compounded_40_years"]["relative_gap"] > 1e-17
    assert 0 < n["float_engine_vs_34_digit_decimal"]["worst_relative"] < 1e-14


def test_a_reduction_has_no_length_at_which_it_is_safe():
    """The tempting mitigation — reduce only short blocks — has no threshold.
    The first disagreement is at twelve elements and plenty of longer lengths
    still agree, so 'small enough' is not a property anything can test."""
    n = _numbers("reduction_order")
    assert n["first_disagreement_at"] < 20
    assert n["lengths_above_the_first_that_still_agree"] > 0, (
        "every length above the first disagreed, which would make a "
        "threshold rule viable and this finding wrong"
    )
    assert n["is_never_compiled"]
    assert not n["at_scale"]["100000"]["identical"]


def test_the_surplus_split_depends_on_the_order_it_is_peeled():
    """Every driver's attributed contribution has a range across orderings,
    and the range is material rather than a rounding artefact. Shapley sits
    inside it — it is a choice among many, not the true answer."""
    n = _numbers("aos_ordering")
    assert n["orders_evaluated"] == 6
    for name, row in n["per_driver"].items():
        assert row["spread"] > 0, name
        assert (row["lowest_over_all_orders"] <= row["shapley"]
                <= row["highest_over_all_orders"]), name
    # Material: at least one driver's range is a tenth of its own size.
    assert any(row["spread"] / abs(row["shapley"]) > 0.1
               for row in n["per_driver"].values())
    assert all(v > 0 for v in n["order_sensitivity"].values())
