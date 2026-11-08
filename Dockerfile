# syntax=docker/dockerfile:1
# The Roost web app (kiosk and approver console). ADR-0015.
#   docker build -t roost .
#   docker compose --profile app up -d     # with Postgres and Jaeger
# The embedding model (bge-m3, ~2.2 GB) is not in the image: it downloads on first start
# into /models, a volume, and is reused after that.

# ── Build: install the locked dependencies into /app/.venv ─────────────────────────
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# Dependencies first, so code changes do not reinstall them.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-install-project

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ── Run: the same Python, the venv and the code; no build tools, not root ───────────
FROM python:3.12-slim
RUN useradd --create-home --uid 10001 roost \
    && mkdir /models \
    && chown roost:roost /models
WORKDIR /app
COPY --from=build --chown=roost:roost /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/models
USER roost
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status != 200)"]
CMD ["uvicorn", "roost.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
