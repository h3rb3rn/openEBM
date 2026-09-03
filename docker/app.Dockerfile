# ─── Stage 1: compile Tailwind CSS at build time (no CDN at runtime) ────────
FROM node:20-slim AS frontend-build

WORKDIR /build
COPY package.json tailwind.config.js ./
RUN npm install --no-fund --no-audit
COPY src/app/templates ./src/app/templates
COPY src/app/static/js ./src/app/static/js
COPY src/app/static/css ./src/app/static/css
RUN npm run build:css


# ─── Stage 2: application image ─────────────────────────────────────────────
FROM python:3.12-slim

LABEL maintainer="EBM Analyzer" \
      description="FastAPI Application Container"

# System packages: curl for healthchecks, postgresql-client so the
# entrypoint can set the restricted app role's password at startup
# (see app-entrypoint.sh)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Requirements first for optimal layer caching
COPY src/app/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Application code
COPY src/app ./src/app
COPY src/integration ./src/integration

# Alembic migrations (schema managed here, not by the app at runtime)
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic

# Locally-built CSS replaces the Tailwind CDN script — no external
# stylesheet/script fetch at runtime (air-gap requirement).
COPY --from=frontend-build /build/src/app/static/css/tailwind.css ./src/app/static/css/tailwind.css

COPY docker/app-entrypoint.sh /app-entrypoint.sh
RUN chmod +x /app-entrypoint.sh

ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["/app-entrypoint.sh"]
