"""Model point containers.

Phase 0: a validated attribute bag, a dict loader, a struct-of-arrays batch
for the vectorized executor, and Parquet round-tripping (optional pyarrow).
"""

from __future__ import annotations

from typing import Iterable

import numpy as np


class ModelPoint:
    def __init__(self, **fields):
        self.__dict__.update(fields)

    def __repr__(self):
        inner = ", ".join(f"{k}={v!r}" for k, v in self.__dict__.items())
        return f"ModelPoint({inner})"

    def __fingerprint__(self):
        return dict(self.__dict__)


def from_dicts(rows: Iterable[dict]) -> list[ModelPoint]:
    return [ModelPoint(**row) for row in rows]


class ModelPointBatch:
    """Struct-of-arrays view of a homogeneous set of model points.

    Numeric fields become NumPy arrays (ints stay integer dtype so they can
    index tables); non-numeric fields become object arrays. Field names
    ``ids`` and ``n`` are reserved for the batch itself.
    """

    def __init__(self, arrays: dict[str, np.ndarray], ids: list):
        for reserved in ("ids", "n"):
            if reserved in arrays:
                raise ValueError(f"model point field {reserved!r} is reserved")
        self.__dict__.update(arrays)
        self.ids = ids
        self.n = len(ids)

    @property
    def fields(self) -> dict[str, np.ndarray]:
        return {k: v for k, v in self.__dict__.items() if k not in ("ids", "n")}

    def take(self, start: int, stop: int) -> "ModelPointBatch":
        """A contiguous slice of the batch, for chunked execution."""
        return ModelPointBatch(
            {name: values[start:stop] for name, values in self.fields.items()},
            self.ids[start:stop],
        )


def to_batch(modelpoints: Iterable[ModelPoint]) -> ModelPointBatch:
    """Struct-of-arrays view of a set of model points.

    A batch passes straight through, so a caller that has already built one
    — a nested run flattening restarted states, say — is not made to take it
    apart into objects and put it back together.
    """
    if isinstance(modelpoints, ModelPointBatch):
        return modelpoints
    mps = list(modelpoints)
    if not mps:
        raise ValueError("no model points supplied")
    fields = list(mps[0].__dict__)
    for i, mp in enumerate(mps):
        if list(mp.__dict__) != fields:
            raise ValueError(
                f"model point {i} fields {sorted(mp.__dict__)} do not match "
                f"first model point fields {sorted(fields)}"
            )
    ids = [getattr(mp, "id", i) for i, mp in enumerate(mps)]
    arrays = {}
    for field in fields:
        values = [getattr(mp, field) for mp in mps]
        if all(isinstance(v, int) and not isinstance(v, bool) for v in values):
            arrays[field] = np.asarray(values, dtype=np.int64)
        elif all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in values
        ):
            arrays[field] = np.asarray(values, dtype=np.float64)
        else:
            arrays[field] = np.asarray(values, dtype=object)
    return ModelPointBatch(arrays, ids)


def to_parquet(modelpoints: Iterable[ModelPoint], path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    rows = [mp.__dict__ for mp in modelpoints]
    pq.write_table(pa.Table.from_pylist(rows), path)


def from_parquet(path) -> list[ModelPoint]:
    import pyarrow.parquet as pq

    return from_dicts(pq.read_table(path).to_pylist())
