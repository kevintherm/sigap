# Sigap backend: admin panel, student portal, MCP gateway, Telegram bot. Started by compose.yaml with Langflow.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
WORKDIR /app

# Dependencies first, so code changes rebuild in seconds.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY sigap ./sigap
COPY data ./data
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

# var/ (database) and outbox/ are mounted from the host by compose.
EXPOSE 8000
CMD ["/app/.venv/bin/sigap-server", "--host", "0.0.0.0", "--port", "8000"]
