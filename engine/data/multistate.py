"""Multi-state Markov models: transitions, not just exits.

PLAN.md §5.2 asks for a multi-state Markov engine, for the health and
protection family — critical illness, disability income, waiver of premium.
It is the natural step past RFC-004's multiple decrements, and the
difference is one word: **recovery**.

A decrement model is a one-way star. Everyone starts in one live state and
leaves it, and the only question RFC-004 answers is which exit they take. A
multi-state model is a general graph: a healthy life can fall sick, a sick
life can recover, and both can die. Once a transition can run backwards, the
population in a state is no longer a decreasing sequence and the
"survivorship" framing stops working entirely.

What replaces it is a transition matrix and one matrix multiply per period:

    ``occupancy(t + 1) = occupancy(t) @ P``

That is the Chapman-Kolmogorov forward equation, and it is the whole engine.

No new DSL primitive
--------------------
None is needed, which is worth saying because it was not obvious. A
template writes one ``@var`` per state and the forward equation falls out as
ordinary formulas::

    @var
    def healthy(self, t):
        if t == 0:
            return self.mp.init_pols * 1.0
        return (self.healthy(t - 1) * self.p(t - 1, "healthy", "healthy")
                + self.sick(t - 1) * self.p(t - 1, "sick", "healthy"))

Each state is a variable that reads the previous period of every state that
can reach it. The dependency graph handles it; there is nothing here the
``@var`` contract did not already support.

The invariant
-------------
**Rows sum to one.** Everybody in a state at the start of a period is
somewhere at the end of it, including still there. That is checked on
construction rather than assumed, because a matrix whose rows sum to 0.999
loses a tenth of a percent of the population every period and looks like
mortality while doing it.

The consequence is the invariant the templates are held to: total occupancy
across all states is conserved for the whole projection, exactly.

Sub-annual, and the trap in it
------------------------------
An annual transition matrix does **not** become a monthly one by dividing.
The monthly matrix is the twelfth **matrix root**: the ``M`` with
``M**12 == P``. Element-wise division of a matrix with recovery in it is not
even close, because it ignores every path that goes out and comes back
within the year.

Worse, a valid annual matrix need not have a valid monthly one. The root
computed from the eigendecomposition can come back with negative entries —
a "probability" below zero — and then no Markov chain on that time step
reproduces the annual matrix at all. This is the **embedding problem**, it
is a real property of the data rather than a numerical artefact, and
:meth:`TransitionMatrix.root` raises rather than handing back a matrix with
negative probabilities in it.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

import numpy as np

#: Rows must sum to 1 within this. Loose enough for a table typed to six
#: decimal places, tight enough that a genuine leak is caught.
ROW_TOLERANCE = 1e-9


class StateSpace:
    """Named states, some of which may be absorbing.

    Order is the order given, and it is the order of the matrix — so a table
    read from a file lines up with the states it was written for.
    """

    def __init__(self, names: Sequence[str], absorbing: Iterable[str] = ()):
        names = tuple(names)
        if len(names) < 2:
            raise ValueError(
                f"a multi-state model needs at least two states, got {names}"
            )
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate state names in {names}")
        self.names = names
        self.index = {name: i for i, name in enumerate(names)}
        unknown = [s for s in absorbing if s not in self.index]
        if unknown:
            raise ValueError(f"unknown absorbing state(s) {unknown}")
        self.absorbing = frozenset(absorbing)

    def __len__(self) -> int:
        return len(self.names)

    def __repr__(self) -> str:
        return f"StateSpace({list(self.names)}, absorbing={sorted(self.absorbing)})"

    def __eq__(self, other) -> bool:
        return (isinstance(other, StateSpace) and self.names == other.names
                and self.absorbing == other.absorbing)

    def __fingerprint__(self):
        return {"names": list(self.names), "absorbing": sorted(self.absorbing)}

    def of(self, name: str) -> int:
        try:
            return self.index[name]
        except KeyError:
            raise KeyError(
                f"no state {name!r}; this model has {list(self.names)}"
            ) from None


class TransitionMatrix:
    """``P[from, to]`` for one period, optionally one matrix per age.

    ``matrix`` is ``(n_states, n_states)`` for an age-independent model, or
    ``(n_ages, n_states, n_states)`` with ``min_age`` naming the first row.
    Ages above the last are held flat, as the mortality basis does.
    """

    def __init__(self, matrix, states: StateSpace, *, min_age: int = 0,
                 tolerance: float = ROW_TOLERANCE):
        arr = np.asarray(matrix, dtype=np.float64)
        n = len(states)
        if arr.shape[-2:] != (n, n):
            raise ValueError(
                f"matrix is {arr.shape}, expected (..., {n}, {n}) for states "
                f"{list(states.names)}"
            )
        if arr.ndim not in (2, 3):
            raise ValueError(f"matrix must be 2-D or 3-D, got {arr.ndim}-D")
        if np.any(arr < 0.0):
            raise ValueError("transition probabilities below zero")
        if np.any(arr > 1.0):
            raise ValueError("transition probabilities above one")
        sums = arr.sum(axis=-1)
        worst = float(np.abs(sums - 1.0).max())
        if worst > tolerance:
            bad = np.unravel_index(np.abs(sums - 1.0).argmax(), sums.shape)
            where = (f"state {states.names[bad[-1]]}" if arr.ndim == 2
                     else f"age {min_age + bad[0]}, state {states.names[bad[-1]]}")
            raise ValueError(
                f"transition matrix rows must sum to 1; {where} sums to "
                f"{float(sums[bad]):.12g}, off by {worst:.3g}. Everybody in "
                "a state at the start of a period is somewhere at the end of "
                "it, including still there."
            )
        for name in states.absorbing:
            i = states.of(name)
            if not np.allclose(arr[..., i, i], 1.0, atol=tolerance):
                raise ValueError(
                    f"state {name!r} is declared absorbing but its row lets "
                    "the population leave it"
                )
        self.matrix = arr
        self.states = states
        self.min_age = int(min_age)
        self.age_dependent = arr.ndim == 3

    def __repr__(self) -> str:
        shape = "by age" if self.age_dependent else "flat"
        return (f"TransitionMatrix({len(self.states)} states, {shape}, "
                f"{list(self.states.names)})")

    def __fingerprint__(self):
        return {"matrix": self.matrix, "states": self.states,
                "min_age": self.min_age}

    def at(self, age=None) -> np.ndarray:
        """The matrix applying at an attained age.

        Ages above the last tabulated one are held flat, the same convention
        the mortality basis uses; below the first raises, because
        extrapolating a transition intensity downwards is never the right
        silent default.
        """
        if not self.age_dependent:
            return self.matrix
        if age is None:
            raise ValueError("this matrix varies by age; pass one")
        ages = np.asarray(age, dtype=np.int64)
        if np.any(ages < self.min_age):
            raise KeyError(
                f"age(s) below the first tabulated age {self.min_age}"
            )
        index = np.minimum(ages - self.min_age, self.matrix.shape[0] - 1)
        return self.matrix[index]

    def p(self, source: str, target: str, age=None):
        """One transition probability, by state name."""
        matrix = self.at(age)
        return matrix[..., self.states.of(source), self.states.of(target)]

    # --- composition ------------------------------------------------------

    def step(self, occupancy, age=None) -> np.ndarray:
        """One period forward: ``occupancy @ P``.

        ``occupancy`` is ``(..., n_states)``. This is the Chapman-Kolmogorov
        forward equation and it is the whole engine.
        """
        occupancy = np.asarray(occupancy, dtype=np.float64)
        matrix = self.at(age)
        if matrix.ndim == 2:
            return occupancy @ matrix
        return np.einsum("...i,...ij->...j", occupancy, matrix)

    def power(self, k: int) -> "TransitionMatrix":
        """``P`` applied ``k`` times, still a transition matrix."""
        if k < 1:
            raise ValueError(f"power {k} must be >= 1")
        return TransitionMatrix(
            np.linalg.matrix_power(self.matrix, k), self.states,
            min_age=self.min_age,
        )

    def root(self, m: int, *, tolerance: float = 1e-9) -> "TransitionMatrix":
        """The ``m``-th matrix root: the ``M`` with ``M ** m == P``.

        What an annual transition matrix becomes when the projection runs
        monthly. **Not** the matrix divided by ``m``, which ignores every
        path that leaves a state and returns within the year — the whole
        thing a multi-state model exists to capture.

        Raises when the root is not a valid transition matrix. That is not a
        numerical failure to be smoothed over: a stochastic matrix need not
        have a stochastic ``m``-th root at all, and when it does not, no
        Markov chain on that time step reproduces the annual matrix. The
        **embedding problem**, and the honest answer is that the annual
        matrix cannot be run monthly rather than a matrix with a negative
        probability in it.
        """
        if m < 1:
            raise ValueError(f"root {m} must be >= 1")
        if m == 1:
            return self
        stack = self.matrix if self.age_dependent else self.matrix[None]
        roots = np.empty_like(stack)
        for i, block in enumerate(stack):
            values, vectors = np.linalg.eig(block)
            if np.any(np.abs(values) < 1e-14):
                raise ValueError(
                    f"the {m}-th root of this matrix is not defined: it is "
                    "singular, so the transition it describes cannot be "
                    "undone to a shorter step"
                )
            powered = vectors @ np.diag(values.astype(complex) ** (1.0 / m))
            candidate = powered @ np.linalg.inv(vectors)
            if np.abs(candidate.imag).max() > tolerance:
                raise ValueError(
                    f"the {m}-th root of this matrix is complex, so there is "
                    "no Markov chain on that time step reproducing it "
                    "(the embedding problem)"
                )
            roots[i] = candidate.real
        # Row sums are exact in theory and off by rounding in practice, so
        # they are renormalised — but only after the sign check, because a
        # negative entry is a modelling failure and normalising would hide it.
        worst = float(roots.min())
        if worst < -tolerance:
            raise ValueError(
                f"the {m}-th root of this matrix has a negative probability "
                f"({worst:.3g}), so no Markov chain on that time step "
                "reproduces it. This is the embedding problem, and it is a "
                "property of the annual matrix rather than of the "
                "arithmetic: it cannot be run at this frequency."
            )
        roots = np.clip(roots, 0.0, None)
        roots /= roots.sum(axis=-1, keepdims=True)
        return TransitionMatrix(
            roots if self.age_dependent else roots[0], self.states,
            min_age=self.min_age,
        )

    # --- construction -----------------------------------------------------

    @classmethod
    def from_rates(cls, rates: Mapping, states: StateSpace, **kwargs
                   ) -> "TransitionMatrix":
        """Build from ``{(from, to): probability}``, filling the diagonal.

        The diagonal is what is left over, which is both how such a table is
        usually quoted and the only way to be sure the rows sum to one:
        stating the stay-put probability separately invites it to disagree
        with the transitions beside it.
        """
        n = len(states)
        matrix = np.zeros((n, n), dtype=np.float64)
        for (source, target), value in rates.items():
            i, j = states.of(source), states.of(target)
            if i == j:
                raise ValueError(
                    f"do not state the diagonal ({source!r} to itself); it "
                    "is whatever the transitions out leave behind"
                )
            matrix[i, j] = value
        leaving = matrix.sum(axis=1)
        if np.any(leaving > 1.0 + ROW_TOLERANCE):
            worst = states.names[int(leaving.argmax())]
            raise ValueError(
                f"transitions out of {worst!r} sum to {leaving.max():.6g}, "
                "which is more than the whole state"
            )
        np.fill_diagonal(matrix, 1.0 - leaving)
        return cls(matrix, states, **kwargs)


def occupancy(start, matrix: TransitionMatrix, periods: int,
              ages=None) -> np.ndarray:
    """Occupancy over time: ``(periods + 1, ..., n_states)``.

    The forward recursion run to a horizon, for a caller that wants the
    whole path in one call rather than a ``@var`` per state. Templates use
    the ``@var`` form; this is for closed-form checks and for anyone
    reasoning about the chain itself.
    """
    current = np.asarray(start, dtype=np.float64)
    out = np.empty((periods + 1, *current.shape), dtype=np.float64)
    out[0] = current
    for t in range(periods):
        age = None if ages is None else ages[t]
        current = matrix.step(current, age)
        out[t + 1] = current
    return out
