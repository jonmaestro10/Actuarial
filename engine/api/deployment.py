r"""The factory a container runs, and why the library does not read the
environment itself.

RFC-078. ``create_app`` takes arguments, and that is deliberate: a library
that reads ``os.environ`` is a library whose behaviour depends on something
its caller cannot see in the call. But a container image has to be configured
by *something*, and the something is conventionally the environment. This
module is the seam between those two facts and contains nothing else.

The rule it enforces is the one worth having: **a deployment that meant to be
secured must not silently come up open.** ``docker-compose.yml`` mounts a
principals file and sets :envvar:`ACTUARIAL_PRINCIPALS` to point at it. If
that path is set and unreadable, this raises rather than falling back — the
fallback is an unauthenticated API serving on the port an authenticated one
was supposed to, which is the single worst outcome available here and the one
a helpful default produces.

Environment
-----------
:envvar:`ACTUARIAL_PRINCIPALS`
    Path to RFC-043's principals file. Absent means no authentication, which
    is correct for a local run and wrong for a deployment; set it and tenancy
    (RFC-078) follows from whatever tenants the file names.
:envvar:`ACTUARIAL_REGISTRY`
    Directory for the artifact registry. Absent means in-memory, which does
    not survive a restart.
:envvar:`ACTUARIAL_AUDIT`
    Path to RFC-045's chained audit log.
:envvar:`ACTUARIAL_EVIDENCE`
    Directory the evidence pack is served from.
:envvar:`ACTUARIAL_MAX_WORKERS`
    Projection threads. Default 1.
:envvar:`ACTUARIAL_DEDUPE_ACROSS_TENANTS`
    ``0`` to make identical work from two tenants compute twice, closing the
    liveness signal :func:`~engine.api.tenancy.shared_compute_leak` describes.
:envvar:`ACTUARIAL_UI`
    ``0`` to serve no HTML.
"""

from __future__ import annotations

import os
from pathlib import Path

from engine.api.app import create_app


class DeploymentError(RuntimeError):
    """Configuration that would bring the API up in a state nobody asked for."""


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _flag(name: str, default: bool) -> bool:
    """A boolean from the environment, refusing anything ambiguous.

    ``ACTUARIAL_UI=maybe`` is not False. Treating an unrecognised value as
    the default is how a deployment that set a flag ends up without it.
    """
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    lowered = raw.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise DeploymentError(
        f"{name}={raw!r} is neither true nor false. Use one of "
        f"{sorted(_TRUE | _FALSE)}; this refuses rather than falling back to "
        f"a default the caller plainly did not intend."
    )


def _required_path(name: str) -> Path | None:
    """A path from the environment that must exist if it is named at all."""
    raw = os.environ.get(name)
    if not raw:
        return None
    path = Path(raw)
    if not path.is_file():
        raise DeploymentError(
            f"{name}={raw!r} does not exist. Refusing to start: the "
            f"alternative is an API that comes up without the configuration "
            f"it was told to use, on the port that configuration was meant "
            f"to protect."
        )
    return path


def settings_from_env(environ=None) -> dict:
    """The keyword arguments :func:`create_app` would be given.

    Separated from :func:`app_from_env` so the translation can be tested
    without building an application, and so a deployment that wants to
    override one thing can read the rest.
    """
    if environ is not None:  # pragma: no cover - exercised via monkeypatch
        os.environ.update(environ)

    registry = os.environ.get("ACTUARIAL_REGISTRY") or None
    evidence = os.environ.get("ACTUARIAL_EVIDENCE") or None
    audit = os.environ.get("ACTUARIAL_AUDIT") or None

    workers = os.environ.get("ACTUARIAL_MAX_WORKERS", "1")
    try:
        max_workers = int(workers)
        if max_workers < 1:
            raise ValueError
    except ValueError:
        raise DeploymentError(
            f"ACTUARIAL_MAX_WORKERS={workers!r} is not a positive integer"
        ) from None

    return {
        "principals": _required_path("ACTUARIAL_PRINCIPALS"),
        "artifacts": registry,
        "evidence": evidence,
        "audit": audit,
        "max_workers": max_workers,
        "ui": _flag("ACTUARIAL_UI", True),
        "dedupe_across_tenants": _flag("ACTUARIAL_DEDUPE_ACROSS_TENANTS", True),
    }


def app_from_env():
    """Build the application from the environment. The container's entrypoint.

    ``uvicorn engine.api.deployment:app_from_env --factory``
    """
    return create_app(**settings_from_env())
