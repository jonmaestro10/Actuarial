"""Model-free check: which cells of Table 6.5 *could* produce each example?

No reading is assumed.  For each contract year we take the set of every cell
in the printed grid whose value equals the number the Guidance Note states,
then ask what structure a reading would have to have to pick one from each.

The only structural fact used is the row axis's own printed name --
"Years Before or After Surrender Charge (SC) Expiration".  For a contract
with a single surrender-charge period there is one expiry event, so the row
offset is t - E: it advances by exactly one per contract year and is
therefore non-decreasing.  That is not a choice of reading; it is what the
row header means.
"""
from itertools import product

ROWS = [+3, +2, +1, 0, -1, -2, -3]          # signed offsets, printed order
COLS = ["A", "B", "C"]
T65 = {
    (+3, "A"): 3.0,  (+3, "B"): 2.0, (+3, "C"): 55.0,
    (+2, "A"): 7.5,  (+2, "B"): 2.0, (+2, "C"): 65.0,
    (+1, "A"): 10.0, (+1, "B"): 2.0, (+1, "C"): 75.0,
    (0,  "A"): 25.0, (0,  "B"): 6.0, (0,  "C"): 75.0,
    (-1, "A"): 2.5,  (-1, "B"): 1.0, (-1, "C"): 70.0,
    (-2, "A"): 2.5,  (-2, "B"): 1.0, (-2, "C"): 70.0,
    (-3, "A"): 2.5,  (-3, "B"): 1.0, (-3, "C"): 70.0,
}
LABEL = {+3: "3+ yrs after", +2: "2 yrs after", +1: "1 yr after",
         0: "Upon expiry", -1: "1 yr to", -2: "2 yrs to", -3: "3+ yrs to"}

EXAMPLES = {
    "Example 1": dict(seq=[1, 1, 1, 75, 10, 7.5, 3], n_sc_periods=1),
    "Example 2": dict(seq=[1, 1, 1, 75, 1, 1, 75], n_sc_periods=2),
    "Example 3": dict(seq=[2.5, 2.5, 2.5, 25, 1, 65], n_sc_periods=1),
}


def cells_for(value):
    return [(r, c) for (r, c), v in T65.items() if v == value]


def report_admissible():
    for name, ex in EXAMPLES.items():
        print("=" * 68)
        print(f"{name}: {ex['seq']}   ({ex['n_sc_periods']} SC period(s))")
        for t, v in enumerate(ex["seq"], start=1):
            cs = cells_for(v)
            pretty = ", ".join(f"{LABEL[r]}/{c}" for r, c in sorted(
                cs, key=lambda rc: (-rc[0], rc[1])))
            print(f"  yr {t}: {v:>5}%  -> {len(cs)} cell(s): {pretty}"
                  + ("   <== FORCED" if len(cs) == 1 else ""))


def monotone_assignments(seq, allow_resets):
    """All cell assignments whose row sequence is non-decreasing, permitting
    `allow_resets` downward jumps (one per *additional* SC expiry event)."""
    out = []
    for combo in product(*(cells_for(v) for v in seq)):
        rows = [r for r, _ in combo]
        drops = sum(1 for a, b in zip(rows, rows[1:]) if b < a)
        if drops <= allow_resets:
            out.append(combo)
    return out


def unit_step_assignments(seq, allow_resets):
    """As above but the row must advance by exactly +1 each year except where
    clipped at the +/-3 end rows, or at a permitted reset."""
    out = []
    for combo in product(*(cells_for(v) for v in seq)):
        rows = [r for r, _ in combo]
        drops = 0
        ok = True
        for a, b in zip(rows, rows[1:]):
            if b == a + 1:
                continue
            if a == b == 3 or a == b == -3:      # clipped at an end row
                continue
            if b < a:
                drops += 1
                continue
            ok = False
            break
        if ok and drops <= allow_resets:
            out.append(combo)
    return out


def main():
    report_admissible()
    print()
    print("=" * 68)
    print("Assignments surviving the row axis's own meaning")
    print("=" * 68)
    for name, ex in EXAMPLES.items():
        resets = ex["n_sc_periods"] - 1
        mono = monotone_assignments(ex["seq"], resets)
        unit = unit_step_assignments(ex["seq"], resets)
        print(f"\n{name}  (at most {resets} row reset(s), one per extra SC "
              f"expiry event)")
        print(f"  non-decreasing row sequences : {len(mono)}")
        print(f"  unit-step row sequences      : {len(unit)}")
        for combo in unit:
            print("     ", " | ".join(
                f"yr{t}={LABEL[r]}/{c}" for t, (r, c) in enumerate(combo, 1)))
        if not mono:
            rows_forced = {t: cells_for(v)[0][0]
                           for t, v in enumerate(ex["seq"], 1)
                           if len(cells_for(v)) == 1}
            print("  IMPOSSIBLE. Forced rows:", {t: LABEL[r] for t, r
                                                 in rows_forced.items()})

    # The sandwich, stated explicitly for Example 3.
    print()
    print("=" * 68)
    print("Example 3, the sandwich")
    print("=" * 68)
    seq = EXAMPLES["Example 3"]["seq"]
    for t in (4, 5, 6):
        cs = sorted(cells_for(seq[t - 1]), key=lambda rc: -rc[0])
        print(f"  yr {t} = {seq[t-1]}% : "
              + ", ".join(f"{LABEL[r]}/{c}" for r, c in cs))
    print("  yr 4 and yr 6 are each satisfied by exactly one cell, fixing")
    print("  row(4) = 0 and row(6) = +2.  With one SC expiry event the row")
    print("  advances one per year, so row(5) = +1 -- and no other value is")
    print("  reachable.  Row +1 offers only:")
    for c in COLS:
        print(f"     column {c}: {T65[(+1, c)]}%")
    print(f"  The Guidance Note asks for {seq[4]}%, which appears in the "
          f"grid only at")
    print("     " + ", ".join(f"{LABEL[r]}/{c}"
                              for r, c in sorted(cells_for(seq[4]),
                                                 key=lambda rc: -rc[0])))
    print("  -- all three of them rows *before* expiry, i.e. row(5) < 0,")
    print("  which contradicts row(4) = 0 for any non-decreasing row axis.")


if __name__ == "__main__":
    main()
