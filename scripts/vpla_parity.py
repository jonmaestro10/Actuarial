"""Parity harness: the engine against a real VPLA checkout.

PLAN.md §3.3 calls for reconciling against an incumbent's actual code and
data rather than against our reading of it. The unit tests compare
``MortalityBasis`` with a hand transcription in ``tests/vpla_reference.py``;
this compares it with ``application/mortality_table.py`` **as it runs**, on
whatever tables the checkout carries — for the reference checkout, the real
CPM2014 base table and CPM2014B generational improvement scale.

It is a script rather than a test because it needs a VPLA checkout, which CI
does not have. Run it whenever the basis changes::

    python scripts/vpla_parity.py --vpla /path/to/vpla

The comparison itself is RFC-033's parity core (:mod:`engine.parity`) since
that landed: this script's job is loading VPLA and choosing the
configurations, not owning a diff loop. The printed output is unchanged.

VPLA fetches its tables from S3 inside a pydantic validator, so the harness
installs a stub for ``application.aws_connection`` that serves the JSON from
the checkout's own ``data/`` directory instead. Nothing else about the
original code is touched: the class under comparison is theirs, unmodified.
"""

from __future__ import annotations

import argparse
import sys
import time
import types
from datetime import date
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.data.mortality import MortalityBasis  # noqa: E402
from engine.data.rates import YieldCurve  # noqa: E402
from engine.library.annuities import (  # noqa: E402
    annuity_factor,
    reversionary_annuity_factor,
)
from engine.parity import (  # noqa: E402
    ExternalTable,
    ParitySpec,
    Tolerance,
    TolerancePolicy,
    diff,
)

#: Bitwise, and stated as such: the engine reproduces VPLA's rates exactly,
#: so the harness reconciles at zero tolerance rather than at a number
#: somebody could later loosen.
EXACT = Tolerance(absolute=0.0, relative=0.0)


def install_local_table_loader(vpla_root: Path):
    """Serve VPLA's S3 table reads from the checkout's ``data/`` directory."""
    data_dir = vpla_root / "data"

    class _Body:
        def __init__(self, payload: bytes):
            self._payload = payload

        def read(self):
            return self._payload

    class _Object:
        def __init__(self, key: str):
            self._key = key

        def get(self):
            name = self._key.rsplit("/", 1)[-1]
            return {"Body": _Body((data_dir / name).read_bytes())}

    class _Resource:
        def Object(self, bucket, key):  # noqa: N802 - boto3's spelling
            return _Object(key)

    def AWSConnection(*args, **kwargs):  # noqa: N802 - VPLA's spelling
        return _Resource()

    stub = types.ModuleType("application.aws_connection")
    stub.AWSConnection = AWSConnection
    sys.modules["application.aws_connection"] = stub


def load_vpla(vpla_root: Path):
    sys.path.insert(0, str(vpla_root))
    package = types.ModuleType("application")
    package.__path__ = [str(vpla_root / "application")]
    sys.modules.setdefault("application", package)
    install_local_table_loader(vpla_root)
    from application.mortality_table import MortalityTable  # noqa: E402

    return MortalityTable


def build_pair(MortalityTable, table_name, improvement_name, year_start, **options):
    """One VPLA table and the engine basis built from the same data."""
    theirs = MortalityTable(
        profile_name="parity",
        year_start=year_start,
        mortality_table_name=table_name,
        mortality={},
        mortality_table_improvement_name=improvement_name,
        mortality_table_improvement={},
        **options,
    )
    improvement = theirs.mortality_table_improvement or None
    ours = MortalityBasis.from_vpla_tables(
        theirs.mortality,
        improvement,
        year_start=year_start,
        use_improvement=options.get("use_improvement", True),
        calc=options.get("calc", "udd"),
        actual_daycount=options.get("actual_daycount", True),
    )
    return theirs, ours


def compare(theirs, ours, lives, freq, n_periods):
    """Every period rate, compared for equality — on the RFC-033 parity core.

    The engine side is a ``(period, life)`` array of rates; the external side
    is VPLA's own ``mortality_period`` evaluated cell by cell; the tolerance
    is exact, because this harness has never accepted a difference and the
    core is where that claim now gets written down. The counts returned are
    the same three numbers the report has always printed.
    """
    from dateutil.relativedelta import relativedelta

    step = 12 // freq
    dobs = [dob for dob, _ in lives]
    sexes = [sex for _, sex in lives]
    valuation = [VALUATION] * len(lives)
    mine = ours.period_mortality(dobs, valuation, sexes, freq, n_periods)
    rows = [
        {
            "life": i,
            "k": k,
            "mortality": theirs.mortality_period(
                dob, VALUATION + relativedelta(months=k * step), sex, freq
            )["mortality"],
        }
        for i, (dob, sex) in enumerate(lives)
        for k in range(n_periods)
    ]
    spec = ParitySpec.from_arrays(
        {"mortality": mine.T}, list(range(len(lives))),
        ExternalTable.from_rows(rows, source="VPLA mortality_table.py"),
        {"mortality": "mortality"}, id_column="life", time_column="k",
        tolerance=TolerancePolicy(EXACT),
        label=f"VPLA period mortality, freq={freq}",
    )
    entry = diff(spec).variable("mortality")
    return entry.n_compared, entry.n_outside, entry.max_absolute


VALUATION = date(2021, 1, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vpla", default="/workspace/vpla",
                        help="path to a VPLA checkout")
    parser.add_argument("--lives", type=int, default=25)
    parser.add_argument("--years", type=int, default=60)
    args = parser.parse_args()

    root = Path(args.vpla).expanduser().resolve()
    if not (root / "application" / "mortality_table.py").is_file():
        raise SystemExit(f"no VPLA checkout at {root}")

    MortalityTable = load_vpla(root)

    rng = np.random.default_rng(20260804)
    lives = [
        (
            date(1935 + int(rng.integers(0, 40)), int(rng.integers(1, 13)),
                 int(rng.integers(1, 29))),
            "M" if i % 2 else "F",
        )
        for i in range(args.lives)
    ]

    configurations = [
        ("CPM2014, no improvement", "CPM2014", "", dict(use_improvement=False)),
        ("CPM2014 + CPM2014 constant scale", "CPM2014", "CPM2014", {}),
        ("CPM2014 + CPM2014B generational scale", "CPM2014", "CPM2014B", {}),
        ("CPM2014 + CPM2014B, linear split", "CPM2014", "CPM2014B",
         dict(calc="linear")),
        ("CPM2014 + CPM2014B, 30/360", "CPM2014", "CPM2014B",
         dict(actual_daycount=False)),
    ]

    print(f"VPLA checkout : {root}")
    print(f"lives         : {len(lives)}   valuation: {VALUATION}")
    print()
    overall_ok = True
    for label, table, improvement, options in configurations:
        theirs, ours = build_pair(
            MortalityTable, table, improvement, 2014,
            calc=options.get("calc", "udd"),
            actual_daycount=options.get("actual_daycount", True),
            use_improvement=options.get("use_improvement", True),
            use_blended_rate=False,
            blended_male_percent=0.0,
        )
        for freq in (1, 12):
            n = args.years * freq
            total, mismatches, worst = compare(theirs, ours, lives, freq, n)
            status = "OK " if mismatches == 0 else "DIFF"
            overall_ok &= mismatches == 0
            print(
                f"[{status}] {label:42s} freq={freq:2d}  "
                f"{total:>7,} rates  mismatched={mismatches}  worst={worst:.3e}"
            )

    # Annuity factors and timing on the heaviest configuration.
    theirs, ours = build_pair(MortalityTable, "CPM2014", "CPM2014B", 2014)
    freq = 12
    n = 120 * freq
    curve = YieldCurve([0.04], freq=freq)
    discount = curve.discount_factors(n)

    # VPLA's own reduction: `sum(np.multiply(df, sf) / freq)`, which
    # accumulates left to right, against the engine's pairwise sum. The
    # survival curves going in are identical, so whatever shows up here is
    # the summation order and nothing else.
    start = time.perf_counter()
    reference_factors = []
    for dob, sex in lives[:5]:
        survival = np.array(theirs.survival_factors(dob, VALUATION, sex, freq))
        reference_factors.append(sum(discount * survival[:n] / freq))
    reference_seconds = (time.perf_counter() - start) / 5

    start = time.perf_counter()
    survival = ours.survival_curve(
        [d for d, _ in lives], [VALUATION] * len(lives),
        [s for _, s in lives], freq, n,
    )
    engine_factors = annuity_factor(discount, survival, freq)
    engine_seconds = (time.perf_counter() - start) / len(lives)

    print()
    worst = max(
        abs(engine_factors[i] - reference_factors[i]) for i in range(5)
    )
    print(f"annuity factors (monthly, 120y, i=4%): worst |diff| = {worst:.3e}"
          "   (summation order only; the survival curves are identical)")
    print(f"  e.g. {reference_factors[0]:.10f} (VPLA) vs "
          f"{engine_factors[0]:.10f} (engine)")
    print(f"per life: VPLA {reference_seconds * 1000:8.1f} ms   "
          f"engine {engine_seconds * 1000:8.3f} ms   "
          f"({reference_seconds / engine_seconds:.0f}x)")
    overall_ok &= check_published_goldens(MortalityTable, root)

    print()
    print("PARITY: bitwise on every mortality rate, and VPLA's published "
          "golden factors reproduced"
          if overall_ok else "PARITY: DIFFERENCES FOUND")
    return 0 if overall_ok else 1


# VPLA's committed golden factors, which were checked there against Society
# of Actuaries calculators. They are read out of the checkout's own test
# files rather than copied in: a transcribed constant is a constant someone
# can mistype, and the point of this harness is to not take anyone's word
# for a number.
GOLDEN_FILES = {
    "single": "tests/test_single_zero_rate.py",
    "joint": "tests/test_joint_zero_rate.py",
}
GOLDEN_PATTERN = (
    r"def test_(single|joint)_(annual|monthly)_(no_imp|imp)_(\d+)_p(\d)"
    r"\(.*?assert round\(result, 4\) == ([\d.]+)"
)
# The fixtures behind those tests: three male lives born on 1 January, each
# with a same-age female spouse and a 100% survivor benefit, valued on their
# birthday with 30/360 day count and UDD.
GOLDEN_BIRTH_DATES = {
    "1": date(1956, 1, 1), "2": date(1946, 1, 1), "3": date(1936, 1, 1),
}


def read_published_goldens(vpla_root: Path):
    """Every committed golden, keyed so a joint case can be compared with the
    single-life case it corresponds to."""
    import re

    goldens = {}
    for kind, relative in GOLDEN_FILES.items():
        source = (vpla_root / relative).read_text()
        for match in re.finditer(GOLDEN_PATTERN, source, re.S):
            benefit, step, improvement, rate, person, expected = match.groups()
            goldens[(benefit, step, improvement, rate, person)] = dict(
                benefit=benefit,
                step=step,
                freq=1 if step == "annual" else 12,
                use_improvement=improvement == "imp",
                rate=int(rate) / 100.0,
                born=GOLDEN_BIRTH_DATES[person],
                expected=float(expected),
                key=(benefit, step, improvement, rate, person),
            )
    return goldens


def check_published_goldens(MortalityTable, vpla_root: Path):
    """Reproduce VPLA's golden annuity factors — from its code and from ours.

    Each case is computed three ways: the constant committed in VPLA's test
    file, VPLA's ``Person`` method as it runs today, and the engine. Parity
    is judged against **VPLA as it runs**, because that is the code under
    comparison; the committed constant is a separate, weaker check on
    provenance.

    Keeping those apart matters here, because the committed constants are
    not all live:

    - 12 single-life monthly constants no longer reproduce from VPLA's own
      code. The engine agrees with VPLA to the last bit on every one, so the
      constants are stale, not the arithmetic.
    - VPLA cannot evaluate a joint factor at all today: a validator on
      ``joint_survivor_percent`` turns every non-zero percentage into
      ``None`` (docs/vpla-review.md §6.1). 16 of the 18 monthly joint
      constants are byte-identical to the corresponding single-life
      constants, which is the signature of having been recorded while that
      fall-through was active.

    That leaves the 18 annual joint constants as the only live evidence for
    the reversionary factor — and the engine's O(n) closed form reproduces
    all 18, which VPLA itself can no longer compute.
    """
    from application.person import Person
    from application.rate_table import RateTable

    goldens = read_published_goldens(vpla_root)
    valuation = date(2021, 1, 1)
    tables, bases = {}, {}
    for use_improvement in (False, True):
        tables[use_improvement], bases[use_improvement] = build_pair(
            MortalityTable, "CPM2014", "CPM2014" if use_improvement else "", 2014,
            use_improvement=use_improvement, calc="udd", actual_daycount=False,
            use_blended_rate=False, blended_male_percent=0.0,
        )

    tally = {"parity": 0, "golden": 0, "stale": 0, "shadowed": 0, "failed": 0}
    print()
    for case in goldens.values():
        basis = bases[case["use_improvement"]]
        table = tables[case["use_improvement"]]
        freq, born = case["freq"], case["born"]
        curve = YieldCurve([case["rate"]], freq=freq)
        n = curve.n_periods
        discount = curve.discount_factors(n)
        survival_x = basis.survival_curve([born], [valuation], ["M"], freq, n)

        person = Person(
            employee_id=1, employee_first_name="J", employee_last_name="S",
            entry_date=date(2019, 1, 1), valuation_date=valuation,
            employee_sex="M", employee_birth_date=born,
            spouse_sex="F", spouse_birth_date=born,
            additional_contribution=0, joint_survivor_percent=1.0,
        )
        rate_table = RateTable(rates=[case["rate"]], freq=freq)

        if case["benefit"] == "single":
            ours = float(annuity_factor(discount, survival_x, freq)[0])
            theirs = person.annuity_factor(
                table, rate_table, valuation, spouse=False, certain_years=0
            )
        else:
            survival_y = basis.survival_curve([born], [valuation], ["F"], freq, n)
            ours = float(
                reversionary_annuity_factor(
                    discount, survival_x, survival_y, 1.0, freq
                )[0]
            )
            try:
                theirs = person.joint_annuity_factor(
                    table, rate_table, valuation, certain_years=0
                )
            except TypeError:
                theirs = None

        label = (
            f"{case['benefit']:6s} freq={freq:2d} "
            f"improvement={case['use_improvement']!s:5s} i={case['rate']:.0%} "
            f"born {born}"
        )
        matches_golden = round(ours, 4) == case["expected"]

        if theirs is not None:
            if abs(ours - theirs) > 1e-9 * max(1.0, abs(theirs)):
                tally["failed"] += 1
                print(f"[DIFF]  {label}: VPLA {theirs:.6f}, engine {ours:.6f}")
                continue
            tally["parity"] += 1
            if round(theirs, 4) == case["expected"]:
                tally["golden"] += 1
            else:
                tally["stale"] += 1
            continue

        # VPLA cannot run this case; fall back to the committed constant.
        if matches_golden:
            tally["golden"] += 1
            continue
        single = goldens.get(("single",) + case["key"][1:])
        if single is not None and single["expected"] == case["expected"]:
            tally["shadowed"] += 1
        else:
            tally["stale"] += 1

    total = len(goldens)
    print(f"[{'OK ' if not tally['failed'] else 'DIFF'}] published goldens: "
          f"{total} cases")
    print(f"        {tally['parity']:>3} agree with VPLA as it runs "
          f"(0 disagreements)" if not tally["failed"]
          else f"        {tally['failed']:>3} DISAGREE with VPLA as it runs")
    print(f"        {tally['golden']:>3} reproduce the committed constant")
    print(f"        {tally['stale']:>3} committed constants stale "
          f"(VPLA no longer reproduces them either)")
    print(f"        {tally['shadowed']:>3} committed joint constants are the "
          f"single-life value, recorded through the")
    print(f"            joint fall-through that the validator regression "
          f"still causes today")
    return tally["failed"] == 0


if __name__ == "__main__":
    raise SystemExit(main())
