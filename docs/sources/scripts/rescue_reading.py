"""The minimal reading that rescues Example 3, and what it costs.

Reading S (straightforward -- the one the repo tested, and the unique member
of the 144-reading family that fits Examples 1 and 2):

  row = contract year minus the SC "Upon expiry" year, where an n-year SC
        has its "Upon expiry" year at n+1; the nearest upcoming SC expiry
        governs, otherwise the last one; clipped to +/-3.
  col = C if the year is the year an IGP longer than 1 year expires (the
        renewal year); A if the governing IGP is 1 year or shorter;
        B otherwise.

Reading R (rescue) is S with one extra clause, and only one:

  in a year that would take column B, if the surrender charge has already
  expired, measure the row from the IGP's expiry year instead of the SC's.

Reading R fits all three examples.  This file also measures what R does to
the rest of the grid, which is the question that decides whether it is a
reading or a curve fit.
"""
from itertools import product

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


def clip(x):
    return max(-3, min(3, x))


def igp_schedule(lengths, n):
    """[(start, length)] covering years 1..n+ from a list of IGP lengths."""
    out, y, i = [], 1, 0
    while y <= n + 4:
        L = lengths[min(i, len(lengths) - 1)]
        out.append((y, L))
        y += L
        i += 1
    return out


def column(t, igp):
    expiring = [(s, L) for (s, L) in igp if s + L == t]
    if expiring:
        s, L = expiring[0]
        return "C" if L > 1 else "A"
    cover = [(s, L) for (s, L) in igp if s <= t < s + L]
    if not cover:
        return "A"
    s, L = cover[0]
    return "A" if L <= 1 else "B"


def sc_row(t, sc):
    E = [s + L for (s, L) in sc]          # "Upon expiry" year = start+length
    later = [e for e in E if e >= t]
    return clip(t - (later[0] if later else max(E)))


def igp_row(t, igp):
    X = [s + L for (s, L) in igp]
    later = [x for x in X if x >= t]
    return clip(t - (later[0] if later else max(X)))


def rate(t, sc, igp, reading):
    col = column(t, igp)
    row = sc_row(t, sc)
    if reading == "R" and col == "B" and t > max(s + L for (s, L) in sc):
        row = igp_row(t, igp)
    return T65[(row, col)], row, col


EXAMPLES = [
    ("Example 1", [(1, 3)], igp_schedule([3, 1], 7), 7,
     [1, 1, 1, 75, 10, 7.5, 3]),
    ("Example 2", [(1, 3), (4, 3)], igp_schedule([3], 7), 7,
     [1, 1, 1, 75, 1, 1, 75]),
    ("Example 3", [(1, 3)], [(1, 1), (2, 1), (3, 1), (4, 2), (6, 2), (8, 2)],
     6, [2.5, 2.5, 2.5, 25, 1, 65]),
]


def check():
    for reading in ("S", "R"):
        print(f"--- reading {reading} " + "-" * 50)
        for name, sc, igp, n, target in EXAMPLES:
            got = [rate(t, sc, igp, reading)[0] for t in range(1, n + 1)]
            trace = [f"{LABEL[r]}/{c}" for t in range(1, n + 1)
                     for _, r, c in [rate(t, sc, igp, reading)]]
            ok = got == target
            print(f"  {name}: {'FIT  ' if ok else 'MISS '} {got}")
            if not ok:
                print(f"      target                {target}")
            print(f"      cells: {' | '.join(trace)}")
        print()


def reachability():
    """Which of the 21 printed cells can any contract reach, under each
    reading?  Sweep a wide space of SC and IGP structures."""
    print("--- reachability of the 21 printed cells " + "-" * 27)
    contracts = []
    for sc_len in range(1, 8):
        for renew_sc in (False, True):
            sc = ([(1, sc_len)] if not renew_sc
                  else [(1, sc_len), (1 + sc_len, sc_len)])
            for first in range(1, 8):
                for later in range(1, 8):
                    igp = igp_schedule([first, later], 20)
                    contracts.append((sc, igp))
    for reading in ("S", "R"):
        seen = set()
        for sc, igp in contracts:
            for t in range(1, 21):
                _, r, c = rate(t, sc, igp, reading)
                seen.add((r, c))
        dead = [(r, c) for r in (3, 2, 1, 0, -1, -2, -3) for c in "ABC"
                if (r, c) not in seen]
        print(f"  reading {reading}: {len(seen)}/21 cells reachable")
        if dead:
            for r, c in dead:
                print(f"      UNREACHABLE: {LABEL[r]:<13} column {c}"
                      f"  (printed value {T65[(r, c)]}%)")
        else:
            print("      every printed cell is reachable")
    print()


if __name__ == "__main__":
    check()
    reachability()
