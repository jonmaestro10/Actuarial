"""Run submission, status and results — the state behind the API.

Kept apart from the HTTP layer on purpose. Everything here is ordinary
Python with no FastAPI in it, so the behaviour that matters — idempotency,
determinism, the lifecycle — is testable without a client and usable from
:func:`engine.run` directly.

A projection is not a request
-----------------------------
A hundred thousand model points over sixty years takes minutes. So
submission is asynchronous: :meth:`RunStore.submit` returns a run identifier
immediately and the work happens on a worker thread. That is not a
performance decision, it is the only honest shape for the thing being
exposed.

Idempotency is free, because the registry already had it
--------------------------------------------------------
:mod:`engine.core.registry` fingerprints a run's *inputs* into a ``run_id``
and its *answer* into a ``results_digest``, and RFC-003 built the whole
thing so that "same question" is a computable property. The API gets
idempotency by using it: submitting the same request twice returns the same
run, and the second submission does no work.

That makes the identifier meaningful rather than arbitrary. A ``run_id`` is
not a ticket number — it is a statement about what was asked, so two clients
that ask the same question anywhere get the same one.

The catch, which is measured rather than assumed: the fingerprint is of the
inputs *as reconstructed*, so a request that differs only in JSON key order,
or in ``1`` against ``1.0``, has to land on the same identifier or the
guarantee is worthless.
"""

from __future__ import annotations

import threading
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable

import numpy as np

from engine.core.fingerprint import fingerprint
from engine.core.registry import RunRecord, RunRegistry, record_run


class RunState(str, Enum):
    """Where a run is. ``str`` so it serialises as itself."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

    @property
    def terminal(self) -> bool:
        return self in (RunState.SUCCEEDED, RunState.FAILED)


@dataclass
class Run:
    """One submitted run: what was asked, where it got to, and the answer."""

    run_id: str
    request: dict
    state: RunState = RunState.QUEUED
    record: RunRecord | None = None
    arrays: dict | None = None
    #: The executor's own result object, kept so that anything reading this
    #: run afterwards aggregates the way the executor that produced it
    #: aggregates. The two differ: the interpreted executor sums with
    #: :func:`~engine.core.vector.stable_sum` and the vectorized one with
    #: NumPy's pairwise reduction, so re-summing ``arrays`` here would give
    #: a number that is *close to* rather than *equal to* the one the run
    #: reported. In a repo whose accuracy claims are bitwise that is not a
    #: rounding difference, it is a second answer. It costs no memory worth
    #: counting — the arrays it holds are the arrays already held above.
    result: object | None = None
    error: str | None = None
    submitted_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None

    def summary(self) -> dict:
        """Status without the numbers, which is what polling wants."""
        out = {
            "run_id": self.run_id,
            "state": self.state.value,
            "submitted_at": self.submitted_at,
            "finished_at": self.finished_at,
            "model": self.request.get("model"),
            "n_modelpoints": len(self.request.get("modelpoints", ())),
            "proj_len": self.request.get("proj_len"),
            "outputs": list(self.request.get("outputs") or ()),
        }
        if self.record is not None:
            out["results_digest"] = self.record.results_digest
            out["engine_version"] = self.record.engine_version
            out["executor"] = self.record.executor
            out["code_version"] = self.record.code_version
        if self.error is not None:
            out["error"] = self.error
        return out


class RunStore:
    """Submitted runs, their state, and the events that state changes emit.

    ``build`` turns a request dictionary into the arguments
    :func:`engine.core.registry.record_run` needs. Keeping it injectable is
    what lets the store be tested without the whole model catalogue, and
    what lets a deployment restrict which models it will run.
    """

    def __init__(self, build: Callable[[dict], dict], *, max_workers: int = 1,
                 registry: RunRegistry | None = None,
                 on_event: Callable[[dict], None] | None = None):
        self._build = build
        self._runs: dict[str, Run] = {}
        self._futures: dict[str, Future] = {}
        # Reentrant: a state change publishes while holding the lock, so
        # that subscribers see queued before running. _publish takes it
        # again.
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=max_workers,
                                        thread_name_prefix="engine-run")
        self.registry = registry if registry is not None else RunRegistry()
        self._subscribers: list = []
        self._on_event = on_event

    # --- identity ---------------------------------------------------------

    @staticmethod
    def identify(request: dict) -> str:
        """A provisional identifier for a request, before it is run.

        The registry's ``run_id`` is only available once the model class and
        assumptions exist as objects, which is after validation. This is the
        request's own fingerprint, and it is what makes a *resubmission*
        cheap: two identical requests collide here without either being
        built.

        Dictionaries fingerprint by sorted key, so JSON key order does not
        move it — asserted in the tests rather than assumed, because the
        whole idempotency guarantee rests on it.
        """
        return fingerprint(_canonical(request))

    # --- lifecycle --------------------------------------------------------

    def submit(self, request: dict) -> Run:
        """Queue a run, or return the existing one for an identical request."""
        run_id = self.identify(request)
        with self._lock:
            existing = self._runs.get(run_id)
            if existing is not None:
                return existing
            run = Run(run_id=run_id, request=dict(request))
            self._runs[run_id] = run
            # Publish *before* the worker can start, or the queued state is
            # lost to a race and running is published twice.
            self._emit(run)
            self._futures[run_id] = self._pool.submit(self._execute, run_id)
        return run

    def _execute(self, run_id: str) -> None:
        run = self._runs[run_id]
        run.state = RunState.RUNNING
        self._emit(run)
        try:
            kwargs = self._build(run.request)
            result, record = record_run(**kwargs)
            outputs = record.outputs
            run.arrays = {name: np.asarray(result.array(name))
                          for name in outputs}
            run.result = result
            run.record = record
            self.registry.add(record)
            run.state = RunState.SUCCEEDED
        except Exception as exc:  # a failed run is a result, not a crash
            run.error = f"{type(exc).__name__}: {exc}"
            run.state = RunState.FAILED
            run.traceback = traceback.format_exc()
        finally:
            run.finished_at = datetime.now(timezone.utc).isoformat()
            self._emit(run)

    def wait(self, run_id: str, timeout: float | None = None) -> Run:
        """Block until a run reaches a terminal state.

        Not something an HTTP handler should call — it is here so that a
        test, or a script using the store directly, does not have to poll.
        """
        future = self._futures.get(run_id)
        if future is None:
            raise KeyError(run_id)
        future.result(timeout=timeout)
        return self._runs[run_id]

    # --- reading ----------------------------------------------------------

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def list(self, state: RunState | None = None) -> list:
        runs = list(self._runs.values())
        if state is not None:
            runs = [r for r in runs if r.state is state]
        return sorted(runs, key=lambda r: r.submitted_at)

    def __len__(self) -> int:
        return len(self._runs)

    # --- events -----------------------------------------------------------

    def subscribe(self) -> "list":
        """A queue of state changes, for the event stream.

        Returns a list used as a queue rather than a
        :class:`queue.Queue`, because the consumer is an async generator
        that must not block the loop; it polls.
        """
        events: list = []
        with self._lock:
            self._subscribers.append(events)
        return events

    def unsubscribe(self, events) -> None:
        with self._lock:
            if events in self._subscribers:
                self._subscribers.remove(events)

    def _emit(self, run: Run) -> None:
        """Publish a state change, in order.

        Held under the lock so that ``queued`` cannot overtake ``running``.
        ``on_event`` therefore runs on the thread that changed the state and
        must not block — a webhook deployment should enqueue and return, not
        make the HTTP call here.
        """
        event = run.summary()
        with self._lock:
            for queue in self._subscribers:
                queue.append(event)
            if self._on_event is not None:
                # A webhook is an outbound HTTP call, which is a dependency
                # this package does not take. The notifier is injected, so a
                # deployment supplies whatever client it already has.
                self._on_event(event)

    def shutdown(self, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait)


def _canonical(value):
    """Normalise a request so that trivially different JSON agrees.

    ``1`` and ``1.0`` are the same number to every model in this library,
    and JSON does not distinguish them reliably across clients. Integers
    that are exactly representable become floats so that a request written
    either way lands on one identifier. Booleans are left alone — they are
    ``int`` subclasses in Python and are not quantities.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return float(value)
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    return value
