FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libmagic1 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra postgres --no-install-project
COPY src ./src
COPY plugins ./plugins
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev --extra postgres
ENV PATH="/app/.venv/bin:$PATH" DP_AC_DATA_DIR=/data
EXPOSE 8000
CMD ["dpac", "serve", "--host", "0.0.0.0", "--port", "8000"]
