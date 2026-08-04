"""Least-squares Monte Carlo: a proxy for the inner projection.

PLAN.md §4.4 lists proxy models among the nested-stochastic tactics, with a
condition attached: *"as an optional, clearly-labeled acceleration with
error estimates"*. That condition is the design. A proxy is an
approximation, and an approximation whose error nobody has measured is
indistinguishable from a mistake.

So this arrives **after** `engine/core/nested.py` rather than instead of it.
The exact nested valuation is what a proxy has to be checked against, and
:func:`proxy_error` does exactly that.

The idea
--------
A full nested run values every outer node with enough inner scenarios to
make each value accurate on its own — hundreds. LSMC does the opposite: it
values every outer node with *very few* inner scenarios, accepts that each
value is nearly worthless individually, and recovers the answer by
regressing those noisy values on the state at the node.

The regression is what averages the noise. With `n_outer` nodes and a
handful of basis functions, each fitted coefficient is informed by every
node, so the surface can be far more accurate than any of the values it was
fitted to. That is the whole trick — and it is also why no in-sample
statistic of the fit can tell you whether it worked. See ``ProxyFit``.

Cost: `n_outer x n_cheap` inner cells instead of `n_outer x n_full`. At 2
against 200 that is a hundredfold, and the accuracy question is whether the
surface survives it.

What it regresses on
--------------------
State — and specifically the state the template itself declares through
``restart_fields``, so a proxy cannot be fitted on a quantity the model does
not carry. For a GMxB that is the fund value and the benefit base; a
contract's guarantee is worth what it is worth because of where those two
stand, and everything else about the node is either constant or already
implied.

Values are fitted **per policy in force** and scaled back afterwards. A
guarantee on two policies is worth twice a guarantee on one, exactly, and
making a polynomial discover that would waste the fit on something already
known.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations_with_replacement
from typing import Any, Callable, Iterable, Sequence

import numpy as np

from engine.core.model import Model
from engine.core.nested import NestedRun, nested_stochastic
from engine.data.scenarios import ScenarioSet


def polynomial_terms(n_states: int, degree: int) -> list:
    """Exponent tuples for a total-degree polynomial, constant first.

    Total degree rather than per-variable: with two states at degree 2 that
    is ``1, x, y, x², xy, y²`` — six terms, including the cross term, which
    is where the interaction between a fund and the guarantee it is measured
    against actually lives.
    """
    terms = [(0,) * n_states]
    for d in range(1, degree + 1):
        for combo in combinations_with_replacement(range(n_states), d):
            exponents = [0] * n_states
            for i in combo:
                exponents[i] += 1
            terms.append(tuple(exponents))
    return terms


@dataclass(frozen=True)
class ProxyFit:
    """A fitted surface for one valuation date, and how well it fits.

    ``residual_std`` and ``r_squared`` describe how far the **noisy node
    values** sit from the surface. That is not how far the surface sits from
    the truth, and the two bear no reliable relationship: measured across
    settings on the GMxB block in RFC-007 the ratio ran from 0.11 to 1.84,
    with no pattern.

    The dangerous direction is the flattering one. At one inner scenario per
    node the residual is the *smallest* of any setting and the surface is
    the *worst* — an in-sample statistic looking excellent while the answer
    is eight times further out than it appears. That is the failure mode
    PLAN §4.4's "with error estimates" exists to prevent, and it is why
    :func:`proxy_error` measures against a reference rather than against the
    fit.
    """

    coefficients: np.ndarray
    terms: tuple
    states: tuple
    centre: np.ndarray
    scale: np.ndarray
    valuation_time: int
    n_nodes: int
    n_inner: int
    r_squared: float
    residual_std: float

    def design(self, values: np.ndarray) -> np.ndarray:
        """The basis evaluated at ``(n_nodes, n_states)`` of state."""
        normalised = (values - self.centre) / self.scale
        columns = [
            np.prod(normalised ** np.asarray(term), axis=1)
            for term in self.terms
        ]
        return np.stack(columns, axis=1)

    def predict(self, state: dict) -> np.ndarray:
        """Value per policy in force, at the states given.

        ``state`` maps each fitted state name to an array of node values.
        """
        missing = [name for name in self.states if name not in state]
        if missing:
            raise KeyError(
                f"proxy needs state {list(self.states)}; missing {missing}"
            )
        columns = np.stack(
            [np.asarray(state[name], dtype=np.float64).reshape(-1)
             for name in self.states],
            axis=1,
        )
        return self.design(columns) @ self.coefficients


@dataclass(frozen=True)
class ProxyValuation:
    """Proxy values at every valuation date, and what they cost to get."""

    fits: dict
    #: ``(n_valuation_times, n_model_points, n_outer)``, matching NestedRun.
    values: np.ndarray
    valuation_times: tuple
    n_model_points: int
    n_outer: int
    n_inner: int
    measure: str

    @property
    def inner_cells(self) -> int:
        return (
            len(self.valuation_times) * self.n_model_points * self.n_outer
            * self.n_inner
        )

    def at(self, valuation_time: int) -> np.ndarray:
        try:
            return self.values[self.valuation_times.index(valuation_time)]
        except ValueError:
            raise KeyError(
                f"no valuation at period {valuation_time}; this proxy covers "
                f"{list(self.valuation_times)}"
            ) from None

    def summary(self) -> str:
        return (
            f"{self.measure} proxy: {self.n_model_points} model points x "
            f"{self.n_outer} outer x {self.n_inner} inner at "
            f"{len(self.valuation_times)} valuation times = "
            f"{self.inner_cells:,} inner cells"
        )


def fit_proxy(
    model_cls: type[Model],
    modelpoints: Iterable[Any],
    assumptions: Any,
    *,
    outer: ScenarioSet,
    inner: Callable[[int, int], ScenarioSet],
    valuation_times: Sequence[int],
    proj_len: int,
    measure: str,
    states: Sequence[str] = ("premium", "gmwb_base"),
    degree: int = 2,
    scale_by: str | None = "init_pols",
    timing: str = "end",
) -> ProxyValuation:
    """Fit a proxy per valuation date from a cheap nested pass.

    ``inner`` is expected to produce **few** scenarios — that is the point.
    ``states`` names ``restart_fields`` entries to regress on, so a proxy can
    only be fitted on state the template actually declares.

    ``scale_by`` names the field the value is proportional to. The fit works
    on value per unit of it and multiplies back, because making a polynomial
    rediscover proportionality wastes degrees of freedom on something
    already known exactly.
    """
    exact = nested_stochastic(
        model_cls, modelpoints, assumptions, outer=outer, inner=inner,
        valuation_times=valuation_times, proj_len=proj_len, measure=measure,
        timing=timing,
    )
    node_states = restart_states(
        model_cls, modelpoints, assumptions, outer=outer,
        valuation_times=exact.valuation_times, proj_len=proj_len,
        states=tuple(states) + ((scale_by,) if scale_by else ()),
    )

    fits, surfaces = {}, []
    for i, t in enumerate(exact.valuation_times):
        columns = np.stack(
            [node_states[t][name].reshape(-1) for name in states], axis=1
        )
        target = exact.values[i].reshape(-1)
        weight = (
            node_states[t][scale_by].reshape(-1) if scale_by
            else np.ones_like(target)
        )
        safe = np.where(weight != 0.0, weight, 1.0)
        fit = _least_squares(columns, target / safe, tuple(states), degree, t,
                             exact.n_inner)
        fits[t] = fit
        surfaces.append(
            (fit.design(columns) @ fit.coefficients * weight).reshape(
                exact.n_model_points, exact.n_outer
            )
        )

    return ProxyValuation(
        fits=fits, values=np.stack(surfaces),
        valuation_times=exact.valuation_times,
        n_model_points=exact.n_model_points, n_outer=exact.n_outer,
        n_inner=exact.n_inner, measure=measure,
    )


def restart_states(model_cls, modelpoints, assumptions, *, outer,
                   valuation_times, proj_len, states) -> dict:
    """The declared state at each valuation date, per ``(model point, path)``.

    Read from ``restart_fields`` rather than from a list of variable names,
    so the regressors are exactly what the template says its state is.
    """
    from engine.core.stochastic import build_stochastic_model
    from engine.data.modelpoints import to_batch

    batch = to_batch(modelpoints)
    model = build_stochastic_model(
        model_cls, batch, assumptions, outer, proj_len
    )
    out = {}
    for t in valuation_times:
        fields = model.restart_fields(t)
        missing = [name for name in states if name not in fields]
        if missing:
            raise KeyError(
                f"{model_cls.__name__}.restart_fields does not carry "
                f"{missing}; it declares {sorted(fields)}"
            )
        out[t] = {
            name: np.broadcast_to(
                np.asarray(fields[name], dtype=np.float64),
                (batch.n, outer.n_scenarios),
            ).copy()
            for name in states
        }
    return out


def _least_squares(columns, target, states, degree, valuation_time, n_inner):
    terms = polynomial_terms(columns.shape[1], degree)
    if len(terms) > columns.shape[0]:
        raise ValueError(
            f"a degree-{degree} polynomial in {len(states)} states needs "
            f"{len(terms)} coefficients but there are only "
            f"{columns.shape[0]} outer nodes to fit them from"
        )
    centre = columns.mean(axis=0)
    scale = columns.std(axis=0)
    # A state that does not vary across nodes carries no information and
    # would divide by zero; leaving it at scale 1 makes its column constant,
    # which the least-squares solve then handles as collinear with the
    # intercept rather than as a NaN.
    scale = np.where(scale > 0.0, scale, 1.0)

    fit = ProxyFit(
        coefficients=np.zeros(len(terms)), terms=tuple(terms), states=states,
        centre=centre, scale=scale, valuation_time=int(valuation_time),
        n_nodes=columns.shape[0], n_inner=n_inner, r_squared=0.0,
        residual_std=0.0,
    )
    design = fit.design(columns)
    coefficients, *_ = np.linalg.lstsq(design, target, rcond=None)
    residuals = target - design @ coefficients
    spread = float(((target - target.mean()) ** 2).sum())
    return ProxyFit(
        coefficients=coefficients, terms=tuple(terms), states=states,
        centre=centre, scale=scale, valuation_time=int(valuation_time),
        n_nodes=columns.shape[0], n_inner=n_inner,
        r_squared=float(1.0 - (residuals ** 2).sum() / spread) if spread else 1.0,
        residual_std=float(residuals.std(ddof=len(terms)))
        if columns.shape[0] > len(terms) else 0.0,
    )


def proxy_error(proxy: ProxyValuation, exact: NestedRun,
                second_opinion: NestedRun | None = None) -> dict:
    """How wrong the proxy is, against a nested run that is not.

    The condition PLAN §4.4 attaches to proxy models, met literally. Errors
    are reported per valuation date and in aggregate, relative to the *mean*
    exact value rather than pointwise — a node whose exact value is near
    zero would otherwise dominate a relative error that says nothing about
    the surface.

    ``speedup`` is the ratio of inner cells, which is what a proxy is bought
    for and therefore what its error has to be weighed against.

    A proxy cannot be measured to be better than the thing measuring it, so
    the reference's own error is reported beside the proxy's:

    ``reference_noise_floor``
        From the reference's per-node standard errors. A **lower bound**,
        and knowingly a loose one: the nested driver values every outer node
        at a date against the *same* inner scenarios, so the node errors are
        correlated and the whole surface shifts together rather than
        averaging out. Treating them as independent — which this figure
        does — understates the aggregate discrepancy, measurably by about
        half on the GMxB block in RFC-007.
    ``reference_noise``
        The real thing, present only when ``second_opinion`` is given: a
        second nested run of the same shape on a different seed, differenced
        against the first. That is what the reference's error actually looks
        like, correlations and all, and it costs a second reference to know.
    """
    if proxy.valuation_times != exact.valuation_times:
        raise ValueError(
            f"proxy covers {proxy.valuation_times}, the reference covers "
            f"{exact.valuation_times}"
        )
    per_date = {}
    for i, t in enumerate(proxy.valuation_times):
        difference = proxy.values[i] - exact.values[i]
        reference = float(np.abs(exact.values[i]).mean())
        per_date[t] = {
            "mean_absolute": float(np.abs(difference).mean()),
            "max_absolute": float(np.abs(difference).max()),
            "relative": float(np.abs(difference).mean() / reference)
            if reference else 0.0,
            "bias": float(difference.mean()),
            "reference_mean": reference,
        }
    reference = float(np.abs(exact.values).mean())
    overall = float(np.abs(proxy.values - exact.values).mean() / reference)
    # For a normal error the mean absolute deviation is sigma * sqrt(2/pi);
    # averaging that over nodes gives the reference's own contribution to
    # the difference measured above, in the same units.
    floor = float(exact.stderr.mean() * np.sqrt(2.0 / np.pi) / reference)
    measured = None
    if second_opinion is not None:
        if second_opinion.valuation_times != exact.valuation_times:
            raise ValueError("the second opinion values different dates")
        measured = float(
            np.abs(second_opinion.values - exact.values).mean() / reference
        )
    against = measured if measured is not None else floor
    return {
        "by_date": per_date,
        "relative": overall,
        "worst_relative": max(d["relative"] for d in per_date.values()),
        "speedup": exact.inner_cells / proxy.inner_cells,
        "reference_noise_floor": floor,
        "reference_noise": measured,
        "at_measurement_floor": overall <= 1.5 * against,
    }
