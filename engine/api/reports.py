"""The reporting overlay over HTTP, and what an overlay's request must say.

:mod:`engine.report` is PLAN §5.3 — products **times** frameworks — and the
bridge it is built on is
:meth:`engine.report.ifrs17.Group.from_run`: the accounting reads a
projection's own output series and re-derives nothing. That bridge is what
makes an HTTP surface possible at all. A request does not have to carry
cashflows; it names series that a completed run already holds, and the
numbers never leave the process to come back in.

Which is the whole design here. ``POST /runs/{id}/reports/ifrs17`` is not a
calculator taking a payload of cashflows — it is a *view* of a run that
already exists, so it inherits that run's identity, its assumptions and its
digest. Two clients asking for the same measurement of the same run are
asking about the same numbers, in the sense RFC-003 made computable.

Why only the GMM
----------------
:mod:`engine.report` carries eleven overlays and this exposes one. The
others are not harder to write, they are harder to *ask for*: Solvency II's
SCR takes a dictionary of stressed module results, the market-risk module
takes an asset portfolio, embedded value takes a capital basis. None of
those is a projection's output series, so none of them has a bridge like
``from_run``, and exposing them would mean inventing a serialisation
format for a portfolio — which is exactly the trade
:mod:`engine.api.catalogue` declined for assumptions. The GMM is here
because the bridge existed first.

The frequency trap
------------------
A :class:`~engine.data.rates.YieldCurve` defaults to twelve periods a year
and :class:`~engine.data.assumptions.Assumptions` defaults to one, and the
CSM accretes at ``(1 + rate) ** (1 / curve.freq) - 1`` per period. Build the
curve without looking at the run and an annual projection accretes a
month of interest per year: the roll-forward still balances, the closing
CSM is still zero, and every number in between is wrong by a factor nothing
in the output announces. So the curve is built at the *run's* frequency,
read from the request the run was submitted with.

What the response is for
------------------------
Everything :class:`~engine.report.ifrs17.Measurement` holds, plus the one
number that says whether to believe it: ``total_profit`` against the
group's undiscounted net cash. Accounting moves profit between periods and
cannot create it, so those two agree over a run-off — and a reconciliation
returned alongside the statement is the same move as returning the results
digest alongside the results.
"""

from __future__ import annotations

import numpy as np

from engine.api.catalogue import InvalidRequestError
from engine.data.rates import YieldCurve
from engine.report.ifrs17 import (
    CoverageUnits, Group, RiskAdjustment, measure,
)


def _series(result, names, n: int | None = None) -> np.ndarray:
    """A named series aggregated across the block, or a sum of several.

    Through the result object rather than by re-summing the stored arrays,
    so the aggregation is the executor's own — see
    :attr:`engine.api.store.Run.result`.
    """
    total = None
    for name in names:
        values = np.asarray(result.aggregate(name), dtype=np.float64)
        total = values if total is None else total + values
    if total is None:  # pragma: no cover - _names refuses an empty list
        raise InvalidRequestError("at least one series name is required")
    return total if n is None else total[:n]


def _names(spec, field: str, known) -> list:
    """Series names from a request field, accepting a bare one.

    Checked against what the run actually holds. A run carries the outputs
    it was submitted with, so naming a variable the *model* has but the
    *run* did not keep is the commonest way to write this request wrong,
    and it is worth saying so rather than raising a ``KeyError`` from
    inside the aggregation.
    """
    if isinstance(spec, str):
        names = [spec]
    elif isinstance(spec, list) and spec and all(isinstance(s, str)
                                                 for s in spec):
        names = list(spec)
    else:
        raise InvalidRequestError(
            f"{field} must be an output series name or a list of them"
        )
    missing = [name for name in names if name not in known]
    if missing:
        raise InvalidRequestError(
            f"{field}: the run holds no series {missing}; it carries "
            f"{sorted(known)}. A run keeps the outputs it was submitted "
            "with, so measuring a series means asking for it at submission"
        )
    return names


def _curve(spec, freq: int, field: str) -> YieldCurve:
    """A yield curve from a rate or a list of them, at the run's frequency.

    ``freq`` is not negotiable and is not in the request: it is the
    projection's, because a period of the curve has to be a period of the
    projection. See the module docstring.
    """
    if isinstance(spec, (int, float)) and not isinstance(spec, bool):
        rates = [float(spec)]
    elif isinstance(spec, list) and spec and all(
            isinstance(r, (int, float)) and not isinstance(r, bool)
            for r in spec):
        rates = [float(r) for r in spec]
    else:
        raise InvalidRequestError(
            f"{field} must be a rate, or a list of annual rates one per year"
        )
    try:
        return YieldCurve(rates, freq=freq)
    except ValueError as exc:
        raise InvalidRequestError(f"{field}: {exc}") from exc


def _acquisition(spec, result, known, field: str = "acquisition") -> float:
    """The cost paid at initial recognition.

    A number, or ``{"series": name}`` naming an output whose whole content
    falls at inception — ``initial_expenses``, for a template that has one.

    The series form is checked rather than trusted. A cost the projection
    puts in period four is not an acquisition cashflow, and summing the
    series anyway would move it to period zero and finance it for four
    periods it was never outstanding. RFC-012 found that exact error in the
    module's own unwind, at a cost of ``acquisition * i`` in total profit;
    it is not going to be reintroduced from the request side.
    """
    if spec is None:
        return 0.0
    if isinstance(spec, (int, float)) and not isinstance(spec, bool):
        return float(spec)
    if isinstance(spec, dict) and set(spec) == {"series"}:
        values = _series(result,
                         _names(spec["series"], f"{field}.series", known))
        later = values[1:]
        if np.any(later != 0.0):
            first = int(np.flatnonzero(later)[0]) + 1
            raise InvalidRequestError(
                f"{field}.series {spec['series']!r} is not an initial cost: "
                f"it is non-zero in period {first}. An acquisition cashflow "
                "is paid at initial recognition; naming a series that is "
                "not would move that money to period zero"
            )
        return float(values[0])
    raise InvalidRequestError(
        f"{field} must be a number or {{\"series\": name}}"
    )


def _risk_adjustment(spec, result, known, n: int) -> RiskAdjustment | None:
    """The margin for non-financial risk, and how it runs off.

    Two forms, because the standard says what a risk adjustment *is* and
    pointedly not how to calculate one: ``{"percent_of": series, "margin":
    x}`` is the common parameterisation, and ``{"total": x, "driver":
    series}`` takes the entity's own answer and only asks how it releases.
    """
    if spec is None:
        return None
    if not isinstance(spec, dict):
        raise InvalidRequestError("risk_adjustment must be an object")
    margin = spec.get("margin")
    total = spec.get("total")
    try:
        if "percent_of" in spec:
            if not isinstance(margin, (int, float)) or isinstance(margin, bool):
                raise InvalidRequestError(
                    "risk_adjustment.margin must be a number")
            claims = _series(result,
                             _names(spec["percent_of"],
                                    "risk_adjustment.percent_of", known), n)
            return RiskAdjustment.percent_of(claims, float(margin))
        if "driver" in spec:
            if not isinstance(total, (int, float)) or isinstance(total, bool):
                raise InvalidRequestError(
                    "risk_adjustment.total must be a number")
            driver = _series(result,
                             _names(spec["driver"],
                                    "risk_adjustment.driver", known), n)
            return RiskAdjustment(float(total), driver)
    except ValueError as exc:
        raise InvalidRequestError(f"risk_adjustment: {exc}") from exc
    raise InvalidRequestError(
        'risk_adjustment needs either {"percent_of": series, "margin": x} '
        'or {"total": x, "driver": series}'
    )


def measure_run(run, spec: dict) -> dict:
    """Measure a completed run's block as one group under the GMM.

    ``run`` is an :class:`engine.api.store.Run` that has succeeded. The
    whole block is one group: which contracts share a group is the entity's
    aggregation policy, and a different aggregation is a different run over
    a different subset — the same position
    :meth:`engine.report.ifrs17.Group.from_run` takes.
    """
    if not isinstance(spec, dict):
        raise InvalidRequestError("a report request must be an object")
    result = run.result
    if result is None:  # pragma: no cover - guarded by the caller
        raise InvalidRequestError("the run holds no result to measure")

    unknown = set(spec) - {
        "inflows", "outflows", "periods", "acquisition", "coverage",
        "risk_adjustment", "discount_rate", "locked_in_rate",
    }
    if unknown:
        raise InvalidRequestError(f"unsupported report fields {sorted(unknown)}")

    known = set(run.arrays or ())
    inflows = _names(spec.get("inflows"), "inflows", known)
    outflows = _names(spec.get("outflows"), "outflows", known)

    # The projection runs t = 0 .. proj_len, so a series is proj_len + 1
    # long and the last entry is the run-off — nothing happens in it. The
    # default measures every period but that one; a caller measuring a
    # cohort that matures earlier says so.
    available = _series(result, inflows).size
    periods = spec.get("periods", available - 1)
    if not isinstance(periods, int) or isinstance(periods, bool) \
            or not 1 <= periods <= available:
        raise InvalidRequestError(
            f"periods must be an integer in 1..{available}, got {periods!r}"
        )

    coverage_spec = spec.get("coverage")
    if not isinstance(coverage_spec, dict) or "units" not in coverage_spec:
        raise InvalidRequestError(
            'coverage must be {"units": series, "discount": bool} — the '
            "quantity of service the group provides, which is the choice "
            "that decides when its profit appears"
        )
    discount = coverage_spec.get("discount", False)
    if not isinstance(discount, bool):
        raise InvalidRequestError("coverage.discount must be true or false")

    freq = int((run.request.get("assumptions") or {}).get("freq", 1))
    current = _curve(spec.get("discount_rate", 0.0), freq, "discount_rate")
    locked_in = (
        current if spec.get("locked_in_rate") is None
        else _curve(spec["locked_in_rate"], freq, "locked_in_rate")
    )

    try:
        group = Group.from_run(
            result, inflows=inflows, outflows=outflows, periods=periods,
            acquisition=_acquisition(spec.get("acquisition"), result, known),
        )
        units = CoverageUnits(
            _series(result,
                    _names(coverage_spec["units"], "coverage.units", known),
                    periods),
            discount=discount,
        )
        measurement = measure(
            group, coverage=units, current=current, locked_in=locked_in,
            risk_adjustment=_risk_adjustment(spec.get("risk_adjustment"),
                                             result, known, periods),
        )
    except ValueError as exc:
        # A group that provides no service, a negative risk adjustment, a
        # driver that sums to zero: each is a bad *request* — the run
        # succeeded and this is what was asked of it.
        raise InvalidRequestError(str(exc)) from exc

    net_cash = float(group.inflows.sum() - group.outflows.sum()
                     - group.acquisition)
    total_profit = measurement.total_profit()
    arrays = {name: np.asarray(value).tolist()
              for name, value in vars(measurement).items()}
    return {
        "run_id": run.run_id,
        "results_digest": run.record.results_digest if run.record else None,
        "framework": "ifrs17-gmm",
        "periods": periods,
        "freq": freq,
        "group": {
            "inflows": inflows,
            "outflows": outflows,
            "acquisition": group.acquisition,
            "net_cash": net_cash,
        },
        "onerous": measurement.onerous,
        "total_service_result": measurement.total_service_result(),
        "total_profit": total_profit,
        # The statement's own check, returned rather than asserted: over a
        # run-off, total profit is the group's undiscounted net cash,
        # because the accounting decides which periods report the money and
        # not how much of it there is.
        "reconciliation": {
            "net_cash": net_cash,
            "total_profit": total_profit,
            "difference": total_profit - net_cash,
            "closing_csm": float(measurement.csm[-1]),
        },
        "statement": arrays,
    }
