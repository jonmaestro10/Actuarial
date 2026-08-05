"""Submit a run through the API and check the numbers survived.

    python scripts/api_demo.py

Runs against an in-process client, so it needs no server. What it
demonstrates is RFC-031's point: the digest the API reports is the digest
the engine computed, and the numbers parsed back out of the JSON are the
numbers that went in — bitwise.
"""

from __future__ import annotations

import sys

import numpy as np

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - the extra is optional
    sys.exit("this demo needs the API extra: pip install -e '.[api]'")

from engine.api import create_app
from engine.core.fingerprint import fingerprint

REQUEST = {
    "model": "TermLife",
    "proj_len": 25,
    "outputs": ["pols_if", "claims", "premiums"],
    "assumptions": {"mortality": 0.008, "lapse": 0.05, "interest": 0.03},
    "modelpoints": [
        {"id": f"T{i}", "age_at_entry": 35 + i % 20, "term_years": 25,
         "sum_assured": 100_000.0 + 500 * i, "annual_premium": 750.0 + i,
         "init_pols": 1}
        for i in range(500)
    ],
}


def main() -> int:
    app = create_app(max_workers=1)
    store = app.state.store
    with TestClient(app) as client:
        accepted = client.post("/runs", json=REQUEST)
        print(f"  POST /runs -> {accepted.status_code} "
              f"{accepted.json()['state']}")
        run_id = accepted.json()["run_id"]
        print(f"  run_id {run_id}")

        # Resubmitting the identical question is free.
        again = client.post("/runs", json=dict(reversed(list(REQUEST.items()))))
        print(f"  resubmitted with the keys reordered -> same run: "
              f"{again.json()['run_id'] == run_id}")

        store.wait(run_id, timeout=600)
        status = client.get(f"/runs/{run_id}").json()
        print(f"  GET /runs/{{id}} -> {status['state']}, "
              f"executor {status['executor']}, "
              f"digest {status['results_digest'][:16]}")

        payload = client.get(f"/runs/{run_id}/results").json()
        received = {name: np.asarray(values, dtype=np.float64)
                    for name, values in payload["results"].items()}
        computed = store.get(run_id).arrays

        print(f"  results {sum(a.size for a in received.values()):,} numbers")
        print(f"  bitwise identical to what the engine computed: "
              f"{all(np.array_equal(computed[n], received[n]) for n in computed)}")
        print(f"  digest of what came back == reported digest: "
              f"{fingerprint(received) == payload['results_digest']}")

        markdown = client.get("/models/TermLife/documentation").text
        print(f"  GET /models/TermLife/documentation -> "
              f"{len(markdown):,} chars of Markdown")
    store.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
