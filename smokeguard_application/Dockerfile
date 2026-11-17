FROM python:3.13-slim

# libgomp1 is required by the TensorFlow CPU wheel; tzdata keeps the
# server-local month grouping and PDF timestamps meaningful (TZ is set by
# docker-compose.yml).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 tzdata \
    && rm -rf /var/lib/apt/lists/*

# Pinned uv (matches the lockfile workflow used locally).
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependency layer first so source edits do not reinstall TensorFlow.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY models ./models
COPY scripts ./scripts
# The PDF report reads this logo from disk (report_logo_path).
COPY frontend/public ./frontend/public

# Non-root runtime user. The named volume mounted at /data inherits this
# ownership on first creation, so the SQLite stores stay writable.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
    && mkdir -p /data \
    && chown app:app /data
USER app

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=12 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
