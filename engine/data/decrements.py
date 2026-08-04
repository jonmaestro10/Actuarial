"""Multiple decrements: independent rates in, dependent rates out.

An assumption basis states each decrement on its own — the mortality table
says what fraction of lives die *if nothing else can remove them*, the lapse
assumption says what fraction surrender *if nobody dies*. Those are
**independent** (single-decrement) rates. A projection needs **dependent**
(multiple-decrement) rates: how many actually leave by each cause when the
causes compete for the same lives during the same year.

Every template in the engine has so far answered that by applying the
decrements in a fixed order — mortality to everyone, lapse to the survivors.
That is one answer among several, and it is the only one that depends on the
order you happen to write the multiplications in. tests/test_frequency.py
already found the consequence: running the same assumptions monthly instead
of annually leaves the same policies in force at each anniversary but moves
exits from mortality to lapse, because the finer step interleaves the
decrements rather than applying them whole in sequence. That test converges
towards an answer it could not state. This module states it.

Three methods, all exact under their own assumption rather than approximate:

``sequential``
    ``q_j = q'_j Π_{k<j} (1 - q'_k)`` — decrement ``j`` acts on whoever the
    earlier decrements left behind. Order-dependent, and **the default**,
    because it is what every template already does and reproducing it bit
    for bit is what lets the existing golden suite stand.

``udd``
    Uniform distribution of decrement ``j`` in its own single-decrement
    table. Deaths accrue evenly through the year while the other decrements
    thin the population continuously, so

        ``q_j = q'_j ∫₀¹ Π_{k≠j} (1 - s q'_k) ds``

    which for two decrements is the textbook ``q'_1 (1 - q'_2 / 2)``. Not an
    approximation of the sequential answer — an exact consequence of a
    different, and more defensible, statement about when in the year people
    leave. Order-independent.

``constant_force``
    A constant hazard for each cause through the year. Forces add, and total
    exits split in proportion to them:

        ``μ_j = -ln(1 - q'_j)``,  ``q_j = (μ_j / Σ μ) (1 - Π_k (1 - q'_k))``

    Order-independent, and the limit the sequential method converges to as
    the projection frequency rises — which is the loop tests/test_frequency.py
    left open, closed in tests/test_decrements.py.

What every method shares, and what the tests lean on
----------------------------------------------------
Total survival is ``Π_k (1 - q'_k)`` under all three. The methods disagree
about *who* left, never about *how many* — so switching method cannot move
the in-force count at any anniversary, only the split of exits between
causes. That is the invariant, and it is asserted directly.
"""

from __future__ import annotations

from typing import Mapping

import numpy as np

METHODS = ("sequential", "udd", "constant_force")


class Decrements:
    """How independent decrement rates combine into dependent ones.

    Rates arrive as an **ordered** mapping of name to independent annual
    rate — scalars, or arrays over model points and scenarios. Order is
    read only by ``sequential``; the other two methods are invariant to it,
    which is one of the things worth testing about them.
    """

    def __init__(self, method: str = "sequential"):
        if method not in METHODS:
            raise ValueError(
                f"method must be one of {METHODS}, got {method!r}"
            )
        self.method = method

    def __repr__(self) -> str:
        return f"Decrements({self.method!r})"

    def __fingerprint__(self):
        return {"method": self.method}

    # --- the shared invariant ---------------------------------------------

    @staticmethod
    def survival(rates: Mapping[str, object]):
        """``Π_k (1 - q'_k)`` — the same under every method.

        Accumulated in the mapping's order, left to right, because that is
        the association the templates already evaluate.
        """
        survivors = None
        for rate in rates.values():
            factor = 1.0 - np.asarray(rate, dtype=np.float64)
            survivors = factor if survivors is None else survivors * factor
        if survivors is None:
            raise ValueError("no decrements supplied")
        return survivors

    # --- dependent rates ---------------------------------------------------

    def dependent(self, rates: Mapping[str, object]) -> dict:
        """Probability of leaving by each cause during the year."""
        if not rates:
            raise ValueError("no decrements supplied")
        q = {name: np.asarray(rate, dtype=np.float64)
             for name, rate in rates.items()}
        if any(np.any((v < 0.0) | (v > 1.0)) for v in q.values()):
            raise ValueError("decrement rates outside [0, 1]")
        if len(q) == 1:
            # Nothing to compete with, so every method is the identity —
            # and saying so here rather than letting `constant_force`
            # arrive at it keeps it exact. That path would compute
            # `1 - exp(-(-log1p(-q)))`, a round trip through two
            # transcendentals that costs a bit or two on a rate near zero,
            # for no gain. A single-decrement model — the deferred annuity
            # — is therefore method-invariant to the last bit.
            return dict(q)
        if self.method == "sequential":
            return self._sequential(q)
        if self.method == "udd":
            return self._udd(q)
        return self._constant_force(q)

    @staticmethod
    def _sequential(q: dict) -> dict:
        remaining = None
        out = {}
        for name, rate in q.items():
            out[name] = rate if remaining is None else remaining * rate
            factor = 1.0 - rate
            remaining = factor if remaining is None else remaining * factor
        return out

    @staticmethod
    def _udd(q: dict) -> dict:
        """``q'_j ∫₀¹ Π_{k≠j} (1 - s q'_k) ds``.

        The product is expanded in ``s`` by repeated convolution and
        integrated term by term, so the result is the exact polynomial for
        any number of decrements rather than the two- or three-decrement
        formula quoted in textbooks. With one decrement the product is
        empty and the integral is 1, which is the right answer.
        """
        out = {}
        for name in q:
            # Coefficients of Π_{k != name} (1 - s q'_k) in powers of s.
            coeffs = [np.array(1.0)]
            for other, rate in q.items():
                if other == name:
                    continue
                nxt = [np.zeros(())] * (len(coeffs) + 1)
                for power, c in enumerate(coeffs):
                    nxt[power] = nxt[power] + c
                    nxt[power + 1] = nxt[power + 1] - c * rate
                coeffs = nxt
            integral = sum(c / (power + 1.0) for power, c in enumerate(coeffs))
            out[name] = q[name] * integral
        return out

    @staticmethod
    def _constant_force(q: dict) -> dict:
        """``(μ_j / Σ μ) (1 - Π (1 - q'_k))`` with ``μ_j = -ln(1 - q'_j)``.

        Two degenerate cases are handled rather than left to produce a
        ``nan``, because both occur in real bases:

        - **Every rate zero.** No exits, so every share is zero.
        - **Some rate is exactly 1**, which a mortality table reaches at its
          limiting age. Its force is infinite, so it takes all the exits;
          if more than one decrement is certain the symmetric limit is an
          equal split, and that is what is used.

        Total exits are ``1 - Π (1 - q'_k)`` and not the algebraically equal
        ``-expm1(-Σ μ_k)``, although the latter is the more accurate of the
        two for small rates. The reason is reconciliation: the projection
        rolls its in-force count forward by ``Π (1 - q'_k)``, and exits
        computed from a *different* expression for the same quantity would
        leave the block failing to balance by an ulp. Balancing exactly
        matters more than a relative error on a decrement whose absolute
        error is one ulp of the whole population.
        """
        names = list(q)
        with np.errstate(divide="ignore"):
            forces = np.stack(
                np.broadcast_arrays(
                    *[-np.log1p(-q[name]) for name in names]
                )
            )
        certain = ~np.isfinite(forces)
        finite = np.where(certain, 0.0, forces)
        total = finite.sum(axis=0)
        n_certain = certain.sum(axis=0)

        positive = total > 0.0
        share = np.where(positive, finite / np.where(positive, total, 1.0), 0.0)
        share = np.where(
            n_certain > 0, certain / np.maximum(n_certain, 1), share
        )
        exits = 1.0 - Decrements.survival(q)
        return {name: share[i] * exits for i, name in enumerate(names)}

    # --- counts ------------------------------------------------------------

    def split(self, in_force, rates: Mapping[str, object]):
        """``(exits_by_cause, survivors)`` starting from an in-force count.

        Working in counts rather than rates is deliberate. Under
        ``sequential`` this reduces the in-force figure one decrement at a
        time, which is the identical chain of multiplications the templates
        already evaluate — same operands, same order, same last bit. Going
        through a dependent *rate* and multiplying afterwards would
        re-associate the product and move golden values by an ulp.
        """
        if self.method == "sequential":
            # Deliberately uncoerced: the operands stay exactly what the
            # caller passed, so a scalar interpreter run keeps producing
            # Python floats and an array run keeps producing arrays.
            exits, remaining = {}, in_force
            for name, rate in rates.items():
                exits[name] = remaining * rate
                remaining = remaining * (1.0 - rate)
            return exits, remaining
        in_force = np.asarray(in_force, dtype=np.float64)
        dependent = self.dependent(rates)
        return (
            {name: in_force * rate for name, rate in dependent.items()},
            in_force * self.survival(rates),
        )
