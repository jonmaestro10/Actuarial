"""A worker is an ordinary engine instance, and the transport that reaches it.

B2's insight, made concrete: a run is an idempotent, content-addressed
question, so a "worker" needs no job model, no queue and no scheduler. It is
:mod:`engine.api` with the endpoint below, and dispatching to it is a POST.

Two transports live here because the coordinator cannot tell them apart —
:func:`engine.core.dispatch.dispatch` takes ``submit`` as an argument, so
``engine/core`` stays NumPy-only (§1.4) and knows nothing about HTTP:

- :func:`local_submit` evaluates the shard in this process. Not a mock: it is
  the degenerate topology, one worker, and the tests use it to pin that a
  dispatched run over N shards equals an undispatched one.
- :func:`http_submit` POSTs to a remote instance.

Both return the same shape — the shard's arrays plus the worker's
**arithmetic attestation**, which the coordinator compares before reducing.
A worker that will not attest cannot be dispatched to, because the bitwise
claim is exactly a claim about the arithmetic on the far end.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from engine.core.dispatch import Attestation, attest
from engine.core.vector import run_vectorized


def evaluate_shard(payload: dict) -> dict:
    """Run one shard here, and say what this machine's arithmetic does.

    The shard is evaluated by :func:`~engine.core.vector.run_vectorized` —
    the same code path an undispatched run takes, which is what makes the
    concatenation of shards equal to the whole.
    """
    result = run_vectorized(
        payload["model_cls"], payload["modelpoints"], payload["assumptions"],
        payload["proj_len"], outputs=payload.get("outputs"),
    )
    return {"stacked": {name: np.ascontiguousarray(values)
                        for name, values in result._stacked.items()},
            "attestation": attest()}


def local_submit(shard, payload: dict) -> dict:
    """Evaluate in this process — the one-worker topology, not a stub."""
    return evaluate_shard(payload)


def http_submit(worker_urls, *, post=None, timeout: float = 300.0):
    """A transport that POSTs shards to registered engine instances.

    Returns a ``submit`` callable. Shards go to ``worker_urls[index % n]``,
    which is a placement and **not** part of the answer: the reduction is by
    shard index, so where a shard ran cannot move a number — that is the
    property the whole design rests on, and it is what makes a retry free.

    ``post`` is injected so the round trip can be tested without a network.
    """
    urls = list(worker_urls)
    if not urls:
        raise ValueError("no worker URLs supplied")
    if post is None:                       # pragma: no cover - needs [api]
        import httpx

        def post(url: str, json: dict) -> dict:
            response = httpx.post(url, json=json, timeout=timeout)
            response.raise_for_status()
            return response.json()

    def submit(shard, payload: dict) -> dict:
        url = urls[shard.index % len(urls)].rstrip("/") + "/shard"
        answer = post(url, _encode(payload))
        return _decode(answer)

    return submit


def _encode(payload: dict) -> dict:
    """The wire form of a shard request.

    The model points travel as plain lists; the model class travels as a
    catalogue *name*, never as a pickle. A worker that could be made to
    unpickle whatever a coordinator sent it would be a remote code execution
    endpoint wearing a projection engine's clothes.
    """
    batch = payload["modelpoints"]
    return {
        "model": payload["model_cls"].__name__,
        "proj_len": payload["proj_len"],
        "outputs": payload.get("outputs"),
        "fields": {name: np.asarray(values).tolist()
                   for name, values in batch.fields.items()},
        "ids": list(batch.ids),
    }


def _decode(answer: dict) -> dict:
    attestation = answer["attestation"]
    if isinstance(attestation, dict):
        attestation = Attestation(**attestation)
    return {
        "stacked": {name: np.asarray(values, dtype=np.float64)
                    for name, values in answer["stacked"].items()},
        "attestation": attestation,
    }
