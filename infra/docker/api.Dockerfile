# API + voice server. Build from the repo root: docker build -f infra/docker/api.Dockerfile .
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra voice --extra observability --no-install-project

COPY callbridge ./callbridge
COPY infra/sql ./infra/sql
COPY evals ./evals
RUN uv sync --frozen --no-dev --extra voice --extra observability

RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/healthz')"
CMD ["/app/.venv/bin/uvicorn", "callbridge.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--ws-ping-interval", "20"]
