"""Enumerate readings of VM-22 Table 6.5 against its Guidance Note's three examples.

Throwaway. Nothing here is imported by the repo.

Table 6.5 as printed (verified against a coordinate-based extraction of
PDF page 259 / chapter page 22-34, 1 Jan 2026 edition):

  rows, top to bottom: 3+ after, 2 after, 1 after, Upon, 1 to, 2 to, 3+ to
  cols: A = "In Years where IGP <= 1 Year*"
        B = "In Years where IGP > 1 Year, and not in Year of IGP Expiry"
        C = "In Year of an IGP Expiry after IGP > 1 Year"

Row offsets are signed: +n = "n yrs after expiry", 0 = "Upon expiry",
-n = "n yrs to expiry"; clipped to +/-3.
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

# ---------------------------------------------------------------- contracts
# Each contract: sc = [(start_year, length)], igp = [(start_year, length)]
# built out to n_years. The IGP schedules encode the reading of the *prose*
# of each example, which is itself a degree of freedom for Example 3.

def ex1(n=7):
    return dict(name="Example 1", n=n, target=[1, 1, 1, 75, 10, 7.5, 3],
                sc=[(1, 3)],
                igp=[(1, 3)] + [(y, 1) for y in range(4, n + 2)])

def ex2(n=7):
    return dict(name="Example 2", n=n, target=[1, 1, 1, 75, 1, 1, 75],
                sc=[(1, 3), (4, 3)],
                igp=[(1, 3), (4, 3), (7, 3)])

def ex3_annual_then_two(n=6):
    # 1-yr IGP renewing annually through the SC period, then a 2-yr IGP at
    # the SC expiry.  Required if years 1-3 are to be column A.
    return dict(name="Example 3 (annual IGPs through SC, 2-yr from yr 4)",
                n=n, target=[2.5, 2.5, 2.5, 25, 1, 65],
                sc=[(1, 3)],
                igp=[(1, 1), (2, 1), (3, 1), (4, 2), (6, 2), (8, 2)])

def ex3_two_at_year_two(n=6):
    # The literal-most reading of the prose: initial 1-yr IGP, then straight
    # into the 2-yr IGP at year 2.
    return dict(name="Example 3 (2-yr IGP from yr 2)",
                n=n, target=[2.5, 2.5, 2.5, 25, 1, 65],
                sc=[(1, 3)],
                igp=[(1, 1), (2, 2), (4, 2), (6, 2), (8, 2)])

def ex3_two_at_year_five(n=6):
    # 2-yr IGP begins the year *after* the renewal year.
    return dict(name="Example 3 (2-yr IGP from yr 5)",
                n=n, target=[2.5, 2.5, 2.5, 25, 1, 65],
                sc=[(1, 3)],
                igp=[(1, 1), (2, 1), (3, 1), (4, 1), (5, 2), (7, 2)])

# ------------------------------------------------------------------ reading
AXES = dict(
    # "Upon expiry" year = start + length - 1 + k.  k=1 -> the year after the
    # last surrender-charge year; k=0 -> the last surrender-charge year.
    sc_k=[0, 1],
    # Which surrender-charge expiry event governs a given contract year.
    sc_pick=["next_or_last", "last_or_next"],
    # Which contract year is "the Year of an IGP Expiry".
    igp_expiry=["renewal_year", "last_year"],
    # In a year that both ends one IGP and starts the next, which IGP's
    # length decides the column.
    renewal_governed_by=["expiring", "new"],
    # Column for a year inside a multi-year IGP that is not an expiry year.
    nonfinal_col=["B", "A", "C"],
    # What the row axis does once the surrender charge is gone for good.
    row_after_sc=["continue", "igp_based", "freeze_at_plus1"],
)


def sc_expiry_years(sc, k):
    return [s + L - 1 + k for (s, L) in sc]


def governing_row(t, r, sc, igp):
    """Signed row offset for contract year t."""
    E = sc_expiry_years(sc, r["sc_k"])
    last_sc_year = max(s + L - 1 for (s, L) in sc)
    if r["row_after_sc"] != "continue" and t > max(E):
        if r["row_after_sc"] == "freeze_at_plus1":
            return +1
        if r["row_after_sc"] == "igp_based":
            X = [s + L - 1 + r["sc_k"] for (s, L) in igp]
            later = [e for e in X if e >= t]
            e = later[0] if later else max(X)
            return max(-3, min(3, t - e))
    if r["sc_pick"] == "next_or_last":
        later = [e for e in E if e >= t]
        e = later[0] if later else max(E)
    else:  # last_or_next
        earlier = [e for e in E if e <= t]
        e = earlier[-1] if earlier else min(E)
    return max(-3, min(3, t - e))


def governing_col(t, r, igp):
    """Column letter for contract year t."""
    # expiry year of IGP j
    def xyear(s, L):
        return s + L if r["igp_expiry"] == "renewal_year" else s + L - 1

    expiring = [(s, L) for (s, L) in igp if xyear(s, L) == t]
    containing = [(s, L) for (s, L) in igp if s <= t < s + L]
    if expiring and r["renewal_governed_by"] == "expiring":
        s, L = expiring[0]
        if L > 1:
            return "C"
        return "A"
    if containing:
        s, L = containing[0]
        if L <= 1:
            return "A"
        if xyear(s, L) == t:
            return "C"
        return r["nonfinal_col"]
    if expiring:                       # renewal_governed_by == "new", no cover
        s, L = expiring[0]
        return "A" if L <= 1 else r["nonfinal_col"]
    return "A"


def sequence(contract, r):
    out = []
    for t in range(1, contract["n"] + 1):
        row = governing_row(t, r, contract["sc"], contract["igp"])
        col = governing_col(t, r, contract["igp"])
        out.append(T65[(row, col)])
    return out


def readings():
    keys = list(AXES)
    for combo in product(*(AXES[k] for k in keys)):
        yield dict(zip(keys, combo))


def main():
    ex3_variants = [ex3_annual_then_two(), ex3_two_at_year_two(),
                    ex3_two_at_year_five()]
    contracts = [ex1(), ex2()] + ex3_variants
    results = []
    for r in readings():
        row = {"reading": r}
        for c in contracts:
            got = sequence(c, r)
            row[c["name"]] = (got == c["target"], got)
        results.append(row)

    # -- which readings fit Examples 1 and 2 --------------------------------
    fit12 = [x for x in results
             if x["Example 1"][0] and x["Example 2"][0]]
    fitall = [x for x in results
              if x["Example 1"][0] and x["Example 2"][0]
              and any(x[v["name"]][0] for v in ex3_variants)]
    fit3only = [x for x in results
                if any(x[v["name"]][0] for v in ex3_variants)]

    print(f"total readings enumerated: {len(results)}")
    print(f"  fit Example 1: {sum(x['Example 1'][0] for x in results)}")
    print(f"  fit Example 2: {sum(x['Example 2'][0] for x in results)}")
    for v in ex3_variants:
        print(f"  fit {v['name']}: "
              f"{sum(x[v['name']][0] for x in results)}")
    print(f"  fit Ex1 AND Ex2: {len(fit12)}")
    print(f"  fit any Ex3 variant: {len(fit3only)}")
    print(f"  fit all three: {len(fitall)}")
    print()

    print("Readings fitting Examples 1 and 2 (and what they give for Ex 3):")
    for x in fit12:
        print("  reading:", {k: v for k, v in x["reading"].items()})
        for v in ex3_variants:
            ok, got = x[v["name"]]
            print(f"     {v['name']}: {'FIT' if ok else 'miss'} {got}")
    print()

    # -- pairwise coverage --------------------------------------------------
    names = ["Example 1", "Example 2"] + [v["name"] for v in ex3_variants]
    from collections import Counter
    sig = Counter()
    for x in results:
        sig[tuple(n for n in names if x[n][0])] += 1
    print("distinct 'which examples fit' signatures across all readings:")
    for k, v in sorted(sig.items(), key=lambda kv: -kv[1]):
        print(f"  {v:4d} readings fit: {k if k else '(none)'}")


if __name__ == "__main__":
    main()
