FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-install-project

COPY src/ ./src/
COPY tests/ ./tests/
COPY alembic.ini ./
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/
RUN uv sync --locked

# Development scaffold; add a service entry point when implementing the endpoint.
CMD ["python", "--version"]
