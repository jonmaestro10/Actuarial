"""Second-stage enumeration: which clock drives the row, per column.

Stage 1 (enumerate_readings.py) held the row axis to a single surrender-charge
clock and found exactly one reading fitting Examples 1 and 2, none fitting
Example 3.  Stage 2 relaxes the row axis itself: each column may read its row
off the SC clock or the IGP clock, either always or only once the SC has gone.

Every reading here fits Examples 1 and 2 by construction of the column rule;
the question is which also fit Example 3, and what each costs in cells of the
printed grid that no contract can ever land on.
"""
from itertools import product
from rescue_reading import (T65, LABEL, column, sc_row, igp_row,
                            igp_schedule, EXAMPLES)


def make(clock, when):
    """clock: dict col -> 'sc' | 'igp'.  when: 'always' | 'after_sc'."""
    def rate(t, sc, igp):
        col = column(t, igp)
        row = sc_row(t, sc)
        use_igp = clock[col] == "igp"
        if use_igp and when == "after_sc":
            use_igp = t > max(s + L for (s, L) in sc)
        if use_igp:
            row = igp_row(t, igp)
        return T65[(row, col)], row, col
    return rate


def reach(rate):
    seen = set()
    for sc_len in range(1, 8):
        for renew in (False, True):
            sc = ([(1, sc_len)] if not renew
                  else [(1, sc_len), (1 + sc_len, sc_len)])
            for a in range(1, 8):
                for b in range(1, 8):
                    igp = igp_schedule([a, b], 20)
                    for t in range(1, 21):
                        seen.add(rate(t, sc, igp)[1:])
    return seen


def main():
    rows = []
    for combo in product(["sc", "igp"], repeat=3):
        clock = dict(zip("ABC", combo))
        for when in ("always", "after_sc"):
            if all(v == "sc" for v in combo) and when == "after_sc":
                continue                      # identical to 'always'
            rate = make(clock, when)
            fits, seqs = [], []
            for name, sc, igp, n, target in EXAMPLES:
                got = [rate(t, sc, igp)[0] for t in range(1, n + 1)]
                seqs.append((name, got, got == target))
                fits.append(got == target)
            seen = reach(rate)
            dead = [(r, c) for r in (3, 2, 1, 0, -1, -2, -3) for c in "ABC"
                    if (r, c) not in seen]
            rows.append((clock, when, fits, seqs, dead))

    print(f"{'A':>4} {'B':>4} {'C':>4} {'when':>9} | "
          f"{'Ex1':>4} {'Ex2':>4} {'Ex3':>4} | dead cells")
    print("-" * 78)
    for clock, when, fits, seqs, dead in rows:
        mark = ["FIT " if f else "miss" for f in fits]
        d = (", ".join(f"{LABEL[r]}/{c}" for r, c in dead) or "none")
        print(f"{clock['A']:>4} {clock['B']:>4} {clock['C']:>4} {when:>9} | "
              f"{mark[0]:>4} {mark[1]:>4} {mark[2]:>4} | {len(dead)}: {d}")

    print()
    print("Readings fitting all three, in full:")
    any_fit = False
    for clock, when, fits, seqs, dead in rows:
        if all(fits):
            any_fit = True
            print(f"  row clock A={clock['A']} B={clock['B']} "
                  f"C={clock['C']}, applied {when}")
            for name, got, ok in seqs:
                print(f"      {name}: {got}")
            print(f"      printed cells no contract can reach: {len(dead)}"
                  + ("".join(f"\n         {LABEL[r]:<13} col {c}"
                             f"  ({T65[(r, c)]}%)" for r, c in dead)))
    if not any_fit:
        print("  none")


if __name__ == "__main__":
    main()
