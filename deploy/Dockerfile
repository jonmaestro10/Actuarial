# The API and worker image.
#
# One image, not two. The worker is the same process as the API in this
# deployment — RunStore's pool is in-process — so a separate worker image
# would be the same bytes under a different name, and two images that must
# stay in lockstep are two images that eventually do not. RFC-075's
# out-of-process dispatch is the seam where that changes, and it will want a
# second *command*, not a second image.
FROM python:3.12-slim AS base

# uvicorn is in the [api] extra. The extras installed here are asserted
# against pyproject.toml by tests/test_deploy.py, because an image that
# installs an extra the project does not define fails at runtime with an
# import error that looks like a code bug.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependency layer first, so a source change does not reinstall NumPy.
COPY pyproject.toml README.md ./
COPY engine/__init__.py ./engine/__init__.py
RUN pip install --no-cache-dir -e ".[api,data,excel]"

COPY engine ./engine
COPY scripts ./scripts

# Non-root, and it owns nothing it writes to. The registry root and the
# principals file arrive as mounts; a process that could rewrite its own
# access-control file is one bug away from granting itself roles.
RUN useradd --system --uid 10001 --create-home actuary \
    && mkdir -p /var/lib/actuarial \
    && chown actuary:actuary /var/lib/actuarial
USER actuary

EXPOSE 8000

# The healthcheck hits the one route with no role requirement, which is why
# that route has no role requirement: a load balancer has no token and an
# unreachable health check is an outage.
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status==200 else 1)"

CMD ["uvicorn", "engine.api.deployment:app_from_env", "--factory", \
     "--host", "0.0.0.0", "--port", "8000"]
