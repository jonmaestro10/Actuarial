"""Run a model over a set of model points.

Phase 0 executor: one interpreted model instance per model point, sequential.
The interface (model class + model points + assumptions in, RunResult out) is
the stable contract; the vectorized executor will slot in behind it.
"""

from __future__ import annotations

from typing import Any, Iterable, Type

from engine.core.model import Model
from engine.core.results import RunResult


def run(
    model_cls: Type[Model],
    modelpoints: Iterable[Any],
    assumptions: Any,
    proj_len: int,
    outputs: list[str] | None = None,
) -> RunResult:
    per_mp = []
    mp_ids = []
    for i, mp in enumerate(modelpoints):
        model = model_cls(mp=mp, assumptions=assumptions, proj_len=proj_len)
        per_mp.append(model.run(outputs))
        mp_ids.append(getattr(mp, "id", i))
    if not per_mp:
        raise ValueError("no model points supplied")
    return RunResult(per_mp=per_mp, mp_ids=mp_ids)
