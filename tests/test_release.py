"""What a release promises, and the two gates that keep the promises true.

G3's subject is the thing incumbents sell as "quarterly vendor library updates
on a contractual cadence". The open answer is not a cadence — it is that each
update lands as a **new dated set** beside the old one, with a diff report and
an expected-change note, so a client's prior-period figures stay reproducible
and "what changed and what it does to your numbers" is a published artifact
rather than a support ticket.

Two things have to stay true for that to be worth anything: the calendar must
name every dated set that exists, and a moved number must not reach a release
without a note.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
CALENDAR = ROOT / "docs" / "regulatory-calendar.md"

#: A dated set: a module-level constant ending in a four-digit year.
_DATED = re.compile(r"^([A-Z][A-Z0-9_]*_(?:19|20)\d{2})\s*[:=]", re.M)


def _gate():
    """Import the gate script by path — `scripts/` is not a package."""
    path = ROOT / "scripts" / "changelog_gate.py"
    spec = importlib.util.spec_from_file_location("_changelog_gate", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _gate()


def dated_sets() -> dict:
    """Every dated regulation set in the engine, and where it lives."""
    found = {}
    for path in sorted((ROOT / "engine").rglob("*.py")):
        for name in _DATED.findall(path.read_text(encoding="utf-8")):
            found[name] = str(path.relative_to(ROOT))
    return found


# --------------------------------------------------------------------------
# The calendar
# --------------------------------------------------------------------------

def test_every_dated_set_in_the_engine_appears_in_the_calendar():
    """**G3's acceptance criterion.** A set with no row fails the build.

    Without this the calendar is a list of the sets somebody remembered, which
    is the same document as a list of all of them right up until it is not —
    and the moment it stops being complete is the moment a client asks which
    basis their prior period was on.
    """
    calendar = CALENDAR.read_text(encoding="utf-8")
    missing = {name: where for name, where in dated_sets().items()
               if name not in calendar}
    assert not missing, (
        f"dated regulation sets with no calendar row: {missing}. Add a row "
        f"with a review date, or the calendar is a list of the ones somebody "
        f"remembered."
    )


def test_the_scan_finds_something_to_check():
    """Guards the empty-collection trap in the test above.

    A regex that stops matching finds no sets, reports none missing, and
    passes — asserting that a document lists all of nothing.
    """
    found = dated_sets()
    assert len(found) >= 3, (
        f"the dated-set scan found only {sorted(found)}; if the naming "
        f"convention moved, the completeness check above is now vacuous"
    )


def test_every_calendar_row_carries_a_review_date():
    """Guards a calendar entry that records a set and not when to look again.

    A review date is the whole difference between a calendar and an inventory.
    """
    rows = [line for line in CALENDAR.read_text().splitlines()
            if line.startswith("| `") and "|" in line[3:]]
    assert rows, "the calendar has no set rows at all"
    for row in rows:
        assert re.search(r"\d{4}-\d{2}-\d{2}", row), (
            f"calendar row has no review date: {row.strip()[:80]}"
        )


def test_the_calendar_says_a_dated_set_is_never_removed():
    """Guards the promise that makes dating the sets worth doing.

    If a superseded set could be deleted, a client could not reproduce the
    valuation they filed under it — and then dating them buys nothing over
    editing them in place.
    """
    text = (CHANGELOG.read_text() + CALENDAR.read_text()).lower()
    assert "never deprecated" in text or "never be removed" in text
    assert "prior-period" in text or "prior period" in text


# --------------------------------------------------------------------------
# The changelog gate
# --------------------------------------------------------------------------

def test_a_moved_golden_value_without_a_changelog_entry_is_refused():
    """**The other acceptance criterion**, on a synthetic diff.

    Driven through `moved_numbers` rather than through git, so the test does
    not depend on the repository's history — a check that only works on a
    branch with the right shape is a check that stops working in CI.
    """
    diff = (
        "+++ b/tests/test_reserves.py\n"
        "@@ -1 +1 @@\n"
        "-    assert reserve == pytest.approx(12345.678)\n"
        "+    assert reserve == pytest.approx(12999.001)\n"
    )
    moved = gate.moved_numbers(diff)
    assert moved == {"tests/test_reserves.py": ["12345.678", "12999.001"]}, (
        "both sides of the diff must count: deleting the assertion that "
        "pinned a reserve changes what the suite guarantees as much as "
        "changing it does"
    )


def test_an_ordinary_test_edit_does_not_trip_the_gate():
    """Guards a gate that fires on everything and gets switched off.

    The suite is edited constantly for reasons that move no number. If those
    edits demanded a changelog entry, the entry would become a reflex and stop
    carrying information.
    """
    diff = (
        "+++ b/tests/test_reserves.py\n"
        "@@ -1 +1 @@\n"
        "-def test_the_reserve_is_positive():\n"
        "+def test_the_reserve_is_never_negative():\n"
        "+    # renamed for clarity; range(5) below is unchanged\n"
    )
    assert gate.moved_numbers(diff) == {}


def test_a_bare_integer_is_not_a_golden_value():
    """Guards the tuning that keeps the gate usable.

    `range(5)`, `proj_len=20` and an index are integers. A gate that fired on
    each of them would fire on every diff, and a gate that fires on every diff
    is one nobody reads.
    """
    diff = ("+++ b/tests/test_x.py\n"
            "@@ -1 +1 @@\n"
            "+    for t in range(60):\n"
            "+    point = {'age_at_entry': 40, 'term_years': 20}\n")
    assert gate.moved_numbers(diff) == {}


def test_a_change_outside_tests_is_not_a_moved_answer():
    """Guards the gate asking the wrong question.

    The engine changing is the ordinary case. What matters is whether the
    *answers* changed, and the answers are pinned in `tests/`.
    """
    diff = ("+++ b/engine/library/term_life.py\n"
            "@@ -1 +1 @@\n"
            "-    return 0.0410\n"
            "+    return 0.0415\n")
    assert gate.moved_numbers(diff) == {}


def test_an_exponent_literal_counts_because_tolerances_are_golden_too():
    """A tolerance chosen to make a test pass is not a tolerance.

    Loosening `1e-12` to `1e-9` moves what the suite guarantees without
    touching a single expected value, and it is exactly the change that most
    wants a sentence explaining itself.
    """
    diff = ("+++ b/tests/test_x.py\n"
            "@@ -1 +1 @@\n"
            "-    assert abs(a - b) < 1e-12\n"
            "+    assert abs(a - b) < 1e-9\n")
    assert gate.moved_numbers(diff) == {"tests/test_x.py": ["1e-12", "1e-9"]}


def test_the_gate_refuses_rather_than_passes_when_it_cannot_see_the_base():
    """**The one that matters most.**

    A gate that cannot reach its base ref — a shallow clone, a missing remote —
    must not report "no golden value changed" about a comparison it never
    made. That answer is indistinguishable from a clean run, which is this
    repository's oldest failure shape: a check that quietly stopped checking.
    """
    assert gate.main(["--base", "refs/heads/no-such-ref-for-this-test"]) == 2, (
        "an unreachable base ref did not produce the distinct 'could not run' "
        "status; it must differ from both pass (0) and fail (1)"
    )


def test_the_changelog_states_what_a_version_bump_means():
    """Guards semver-by-name only.

    "MAJOR when a number moves for an unchanged input" is the clause a client
    actually needs, and it is not what semver says on its own — an API-compatible
    release that moves a reserve is the event that costs them a re-run.
    """
    text = CHANGELOG.read_text()
    assert "MAJOR" in text and "MINOR" in text and "PATCH" in text
    assert "numeric result changes" in text, (
        "the changelog no longer says that a moved number is a MAJOR event"
    )
    assert "expected-change note" in text


def test_a_new_dated_set_is_a_minor_release_not_a_major_one():
    """Guards the versioning that makes dated sets useful.

    If shipping `DELEGATED_2026` were MAJOR, every client would face an
    upgrade decision over a regulation that does not apply to them yet. It is
    MINOR precisely because the set they are on does not move.
    """
    text = CHANGELOG.read_text()
    assert "MINOR, not MAJOR" in text or "MINOR rather than MAJOR" in text
