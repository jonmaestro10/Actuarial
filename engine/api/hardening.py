r"""Security headers and a rate limit, and what neither of them is.

RFC-079. G2 asks for "an API hardening pass (rate limiting, security
headers)". Both are here and both are **narrower than their names suggest**,
which is the part worth writing down — a control that is believed to do more
than it does is worse than no control, because it stops anyone looking.

The rate limit is a fair-use bound, not a defence
-------------------------------------------------
:class:`RateLimit` is an in-process token bucket keyed by principal. That
makes it useful for exactly one thing: stopping a client that has a bug, or a
tenant whose batch job would otherwise consume every projection thread. It
does **not** survive a second replica — each process keeps its own buckets, so
N replicas admit N times the configured rate — and it is not a DoS control,
because an attacker who has no token never reaches it.

Both facts are in :data:`RATE_LIMIT_SCOPE` rather than in a comment, and the
compliance section quotes that string. A SOC 2 control map that claimed "rate
limiting" without them would be describing a distributed limiter this does not
have.

The headers are the ones that mean something for a JSON API
-----------------------------------------------------------
Most of the familiar header list exists to constrain a *browser* rendering
HTML. This API's product is JSON, and the one page it serves is RFC-032's
demonstration UI. So:

``X-Content-Type-Options: nosniff``
    The one that matters most here. Without it a browser may sniff a JSON
    response as HTML and execute it — the classic path from "returns user
    data" to "runs script in the API's origin".
``Content-Security-Policy: default-src 'none'; frame-ancestors 'none'``
    On API routes, where nothing should ever load a subresource. The UI needs
    its own stylesheet and script, so it gets a policy that permits ``'self'``
    and nothing else.
``Referrer-Policy: no-referrer``
    Run ids are request fingerprints. A referrer header leaks them to whatever
    a user navigates to next.
``Cache-Control: no-store`` on API routes
    Results are a tenant's numbers. A shared cache holding them is a
    cross-tenant read that no route check can see.

**HSTS is deliberately absent.** It is a promise about a *scheme*, and this
process does not know whether it is behind TLS — a Strict-Transport-Security
header emitted from a plain-HTTP deployment behind no proxy is a header that
does nothing, and emitted from one that is, it belongs on the proxy that owns
the certificate. A deployment adds it where the TLS terminates.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock

#: What the rate limit is and is not, in words, quoted by the compliance
#: section. A control's honest scope belongs beside the control.
RATE_LIMIT_SCOPE = (
    "In-process token bucket keyed by principal. Bounds one client's share of "
    "a single process — a looping client, or a tenant's batch job taking every "
    "projection thread. It is NOT a distributed limit: each replica keeps its "
    "own buckets, so N replicas admit N times the configured rate. It is NOT a "
    "denial-of-service control: an unauthenticated caller is rejected by "
    "authentication before reaching it, and a network-level flood never "
    "reaches the process at all. Deployments needing either put a limiter at "
    "the ingress."
)

#: Applied to every response. See the module docstring for why HSTS is not
#: here and why the CSP differs between the API and the UI.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}

#: API routes: nothing should ever load a subresource from a JSON response.
API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"

#: The demonstration page needs its own two assets and nothing else.
UI_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; "
          "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
          "base-uri 'none'")


@dataclass
class RateLimit:
    """A token bucket per key, over a sliding window.

    A deque of timestamps rather than a counter with a reset: a fixed window
    admits ``2 * rate`` across its boundary, which is a real burst and would
    show up as a limiter that "sometimes lets twice as much through".
    """

    #: Requests permitted per :attr:`window` seconds, per key.
    rate: int = 120
    window: float = 60.0
    _seen: dict = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def check(self, key: str, now: float | None = None) -> bool:
        """Record a request and say whether it is within the limit."""
        if self.rate <= 0:
            return True
        now = time.monotonic() if now is None else now
        with self._lock:
            bucket = self._seen.setdefault(key, deque())
            cutoff = now - self.window
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.rate:
                return False
            bucket.append(now)
            return True

    def retry_after(self, key: str, now: float | None = None) -> int:
        """Whole seconds until ``key`` has room again, at least 1.

        Rounded **up**, and never zero: a ``Retry-After: 0`` invites an
        immediate retry that will also fail, which turns a rate limit into a
        busy loop between a well-behaved client and the server.
        """
        now = time.monotonic() if now is None else now
        with self._lock:
            bucket = self._seen.get(key) or deque()
            if len(bucket) < self.rate:
                return 1
            wait = bucket[0] + self.window - now
        return max(1, int(wait) + (1 if wait % 1 else 0))

    def summary(self) -> dict:
        return {"rate": self.rate, "window_seconds": self.window,
                "scope": RATE_LIMIT_SCOPE}
