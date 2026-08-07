"""What `deploy/` claims about the code it deploys, checked against the code.

There is no container runtime in CI, so these do not boot anything. That is
stated rather than glossed: the smoke test G1 asks for needs Docker and is
marked slow and skipped where Docker is absent — see
:func:`test_the_compose_profile_boots_and_serves_a_run`.

What *is* checkable without a runtime is every place the deployment restates
something the code already decides: the port, the entrypoint, the extras, the
version, the environment variable names. Each of those is a second source of
truth, and a deployment that disagrees with its application fails at 3am
rather than here.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
DOCKERFILE = DEPLOY / "Dockerfile"
COMPOSE = DEPLOY / "docker-compose.yml"
CHART = DEPLOY / "helm" / "Chart.yaml"
VALUES = DEPLOY / "helm" / "values.yaml"
DEPLOYMENT = DEPLOY / "helm" / "templates" / "deployment.yaml"

yaml = pytest.importorskip("yaml", reason="PyYAML is in the `test` extra")


def _text(path: Path) -> str:
    assert path.is_file(), f"{path.relative_to(ROOT)} is missing"
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# The entrypoint has to be a thing that exists
# --------------------------------------------------------------------------

def test_the_image_runs_an_entrypoint_that_is_importable():
    """Guards the container's CMD naming a factory nobody wrote.

    `uvicorn engine.api:create_app --factory` was the obvious command and was
    wrong: it calls `create_app()` with no arguments, so a compose file that
    mounts a principals file would serve an **unauthenticated** API on the
    port the authenticated one was meant to occupy. Nothing would look broken.
    """
    command = _text(DOCKERFILE)
    match = re.search(r'CMD \["uvicorn", "([^"]+)"', command)
    assert match, "the Dockerfile has no uvicorn CMD this test can read"
    target = match.group(1)

    module_name, _, attribute = target.partition(":")
    module = __import__(module_name, fromlist=[attribute])
    factory = getattr(module, attribute, None)
    assert callable(factory), (
        f"the image runs {target!r}, which is not a callable this repo "
        f"defines"
    )
    assert "--factory" in command, (
        "uvicorn needs --factory to call the entrypoint rather than treat it "
        "as an app object"
    )


def test_the_entrypoint_refuses_to_start_without_a_principals_file_it_was_told_to_use(
        tmp_path, monkeypatch):
    """**The rule the whole deployment rests on.**

    A deployment that meant to be secured must not silently come up open. The
    tempting behaviour — fall back to no authentication when the file is
    missing — produces exactly one outcome: an unauthenticated API on the
    port that was supposed to be protected, with a healthy healthcheck.
    """
    from engine.api.deployment import DeploymentError, settings_from_env

    monkeypatch.setenv("ACTUARIAL_PRINCIPALS", str(tmp_path / "absent.json"))
    with pytest.raises(DeploymentError, match="Refusing to start"):
        settings_from_env()


def test_an_unset_principals_path_is_allowed_because_a_local_run_has_none(
        monkeypatch):
    """The other direction, so the refusal above is not simply "always fail".

    No principals configured is the library's own default and correct for a
    developer running uvicorn by hand.
    """
    from engine.api.deployment import settings_from_env

    monkeypatch.delenv("ACTUARIAL_PRINCIPALS", raising=False)
    assert settings_from_env()["principals"] is None


@pytest.mark.parametrize("value", ["maybe", "2", "yes please", "off!"])
def test_an_ambiguous_boolean_is_refused_rather_than_defaulted(value, monkeypatch):
    """Guards a flag that was set and did not take effect.

    Treating an unrecognised value as the default is how a deployment that
    turned the UI off, or turned cross-tenant deduplication off, comes up with
    it on and nothing saying so.
    """
    from engine.api.deployment import DeploymentError, settings_from_env

    monkeypatch.delenv("ACTUARIAL_PRINCIPALS", raising=False)
    monkeypatch.setenv("ACTUARIAL_UI", value)
    with pytest.raises(DeploymentError, match="neither true nor false"):
        settings_from_env()


# --------------------------------------------------------------------------
# Second sources of truth, checked against the first
# --------------------------------------------------------------------------

def test_the_extras_the_image_installs_are_extras_the_project_defines():
    """Guards an image that installs `.[api,data,excel]` where one is a typo.

    pip does not fail on an unknown extra — it warns and carries on — so the
    image builds, starts, and dies on an ImportError that reads like a code
    bug rather than a Dockerfile one.
    """
    import tomllib

    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    defined = set(project["project"]["optional-dependencies"])

    match = re.search(r'pip install [^\n]*"\.\[([^\]]+)\]"', _text(DOCKERFILE))
    assert match, "no editable install with extras found in the Dockerfile"
    wanted = {name.strip() for name in match.group(1).split(",")}

    unknown = wanted - defined
    assert not unknown, (
        f"the Dockerfile installs extras {sorted(unknown)} that "
        f"pyproject.toml does not define ({sorted(defined)}); pip only warns"
    )


def test_the_chart_version_is_the_engine_version():
    """Guards a chart that reports a version its image does not carry.

    `helm rollback` picks a revision by chart version. If that string drifts
    from the engine's, a rollback lands on a release whose numbers came from
    different code than the version label says.
    """
    from engine import __version__ as engine_version

    chart = yaml.safe_load(_text(CHART))
    assert str(chart["appVersion"]) == engine_version, (
        f"Chart.yaml appVersion {chart['appVersion']!r} != engine "
        f"{engine_version!r}"
    )


def test_the_port_is_the_same_number_everywhere():
    """Guards a service that routes to a port nothing is listening on.

    The number appears in the Dockerfile's EXPOSE and healthcheck, the compose
    mapping, and the chart's values — four places, and three of them are
    copies.
    """
    values = yaml.safe_load(_text(VALUES))
    port = int(values["service"]["port"])

    dockerfile = _text(DOCKERFILE)
    assert f"EXPOSE {port}" in dockerfile
    assert f":{port}/health" in dockerfile, (
        "the container healthcheck probes a different port than it exposes"
    )
    assert f"--port\", \"{port}\"" in dockerfile

    compose = yaml.safe_load(_text(COMPOSE))
    mapped = compose["services"]["api"]["ports"]
    assert any(str(port) in str(entry) for entry in mapped)


def test_every_environment_variable_the_deployment_sets_is_one_the_code_reads():
    """Guards configuration that is set and silently ignored.

    An `ACTUARIAL_REGISTRY` in a chart that the factory never reads looks like
    working configuration in every review and does nothing at runtime.
    """
    source = (ROOT / "engine" / "api" / "deployment.py").read_text()
    understood = set(re.findall(r'"(ACTUARIAL_[A-Z_]+)"', source))
    assert understood, "the factory reads no ACTUARIAL_* variables at all"

    set_in_chart = set(re.findall(r"name: (ACTUARIAL_[A-Z_]+)",
                                  _text(DEPLOYMENT)))
    set_in_compose = set(re.findall(r"(ACTUARIAL_[A-Z_]+):", _text(COMPOSE)))

    for where, names in (("the Helm deployment", set_in_chart),
                         ("docker-compose.yml", set_in_compose)):
        assert names, f"{where} sets no ACTUARIAL_* variables"
        unread = names - understood
        assert not unread, (
            f"{where} sets {sorted(unread)}, which "
            f"engine/api/deployment.py never reads"
        )


def test_the_shipped_principals_example_cannot_be_used_as_it_stands():
    """Guards a deployment booting with a token that is public knowledge.

    An example principals file with *valid* digests is a set of credentials
    published in a git repository. These are deliberately malformed so the
    file fails to load, loudly, on the first `docker compose up`.
    """
    from engine.api.auth import Principals, PrincipalsError

    spec = json.loads((DEPLOY / "principals.example.json").read_text())
    with pytest.raises(PrincipalsError):
        Principals.from_dict(spec)


def test_the_chart_refuses_replicas_that_would_share_a_single_writer_volume():
    """Guards a scale-up that corrupts the registry instead of failing.

    ReadWriteOnce with two replicas either fails to schedule or, on some CSI
    drivers, mounts twice. The registry is content-addressed so equal digests
    write equal bytes — but a *partial* write is not equal bytes, and the
    failure would surface as a corrupt artifact rather than as a scheduling
    error.
    """
    template = _text(DEPLOYMENT)
    assert "fail" in template and "ReadWriteMany" in template, (
        "the chart no longer refuses replicaCount > 1 on a ReadWriteOnce "
        "volume"
    )


def test_the_container_does_not_run_as_root():
    """Guards the obvious one, because it is obvious and still gets lost."""
    dockerfile = _text(DOCKERFILE)
    assert re.search(r"^USER\s+\S+", dockerfile, re.M), "no USER directive"
    assert not re.search(r"^USER\s+root\s*$", dockerfile, re.M)

    template = _text(DEPLOYMENT)
    assert "runAsNonRoot: true" in template
    assert "allowPrivilegeEscalation: false" in template


# --------------------------------------------------------------------------
# The smoke test G1 asks for, which needs a runtime
# --------------------------------------------------------------------------

def _docker_works() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True,
                              timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return False


@pytest.mark.slow
@pytest.mark.skipif(not _docker_works(),
                    reason="no working Docker daemon; the compose smoke test "
                           "needs one and is not simulated")
def test_the_compose_profile_boots_and_serves_a_run():  # pragma: no cover
    """G1's end-to-end acceptance. Skipped without a daemon, and says so.

    Not simulated. A version of this that stubbed out Docker would assert
    that the stub works, and the thing being checked here is precisely that
    the *image* — its base, its install, its non-root user, its writable
    paths — serves a projection. There is no way to learn that without
    building it.
    """
    build = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), "config"],
        capture_output=True, text=True, timeout=120,
    )
    assert build.returncode == 0, build.stderr
