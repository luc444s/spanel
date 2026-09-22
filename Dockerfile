# ── Stage 1: Build frontend ──
FROM node:20-slim AS frontend-build

WORKDIR /app

COPY apps/web/package.json apps/web/package-lock.json* ./
RUN npm install --prefer-offline

COPY apps/web/ ./
COPY vendor/systutor-core/src/systutor/sdk/frontend/ ../../vendor/systutor-core/src/systutor/sdk/frontend/
COPY vendor/systutor-shell/src/ ../../vendor/systutor-shell/src/
COPY vendor/systutor-themes/src/ ../../vendor/systutor-themes/src/
COPY plugins/ ../../plugins/

RUN npm run build

# ── Stage 2: Python backend + static frontend ──
FROM python:3.12-slim AS production

RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client sshpass libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY vendor/systutor-core/pyproject.toml vendor/systutor-core/README.md* ./vendor/systutor-core/
COPY vendor/systutor-core/src/ ./vendor/systutor-core/src/
COPY vendor/systutor-core/app/ ./vendor/systutor-core/app/

RUN pip install --no-cache-dir ./vendor/systutor-core psycopg[binary]

COPY plugins/ ./plugins/

# Remove backend-only files from plugins
RUN find plugins/ -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null; \
    find plugins/ -name "tests" -type d -exec rm -rf {} + 2>/dev/null; \
    find plugins/ -name "*.pyc" -delete 2>/dev/null; \
    true

COPY --from=frontend-build /app/dist/ ./static/

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/vendor/systutor-core
ENV SYSTUTOR_PLUGINS_DIR=/app/plugins
ENV SYSTUTOR_STATIC_DIR=/app/static

EXPOSE 8000

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

CMD ["/app/entrypoint.sh"]
