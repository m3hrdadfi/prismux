FROM node:22-alpine AS frontend-builder

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.5-python3.12-bookworm-slim AS python-builder

WORKDIR /app
COPY pyproject.toml ./
COPY app ./app

RUN uv sync --no-dev --no-editable

FROM python:3.12-slim-bookworm AS app-runtime

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=python-builder /app/.venv /app/.venv
COPY app ./app
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations
COPY scripts ./scripts
ENV PATH="/app/.venv/bin:$PATH"

# The migrate service builds only the app-runtime stage above — it runs
# Alembic and never serves the frontend, so it has no reason to depend on
# the Node/npm build below. Keeping that dependency out of its build graph
# avoids running two heavy, concurrent Docker builds (Node + Python) at once
# on `docker compose up --build`, which has been observed to starve the
# Node/npm-ci step under constrained build-host resources.
FROM app-runtime AS proxy

COPY --from=frontend-builder /frontend/dist ./frontend/dist
EXPOSE 8100

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
