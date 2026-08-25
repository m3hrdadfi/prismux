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

FROM python:3.12-slim-bookworm

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=python-builder /app/.venv /app/.venv
COPY app ./app
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations
COPY scripts ./scripts
COPY --from=frontend-builder /frontend/dist ./frontend/dist
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8100

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
