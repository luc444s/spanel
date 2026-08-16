FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client sshpass curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY vendor/systutor-core/pyproject.toml vendor/systutor-core/
COPY vendor/systutor-core/src/ vendor/systutor-core/src/
COPY vendor/systutor-core/app/ vendor/systutor-core/app/
COPY plugins/ plugins/

RUN pip install --no-cache-dir ./vendor/systutor-core && \
    pip install --no-cache-dir psycopg[binary]

ENV PYTHONPATH=/app/vendor/systutor-core/src:/app/plugins
ENV SYSTUTOR_PLUGINS_DIR=/app/plugins
ENV SYSTUTOR_ENV=production

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "2"]
