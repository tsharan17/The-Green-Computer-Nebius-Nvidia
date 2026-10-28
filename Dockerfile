# The Green Computer: one container serving the API and the web UI on port 8000.
#
# STATUS: written but not built in the development environment (no Docker there). Every step it
# performs is exercised by CI (clean install, UI build, migrations, tests, acceptance run).
#
#   docker build -t green-computer .
#   docker compose up --build          # app + PostgreSQL, see docker-compose.yml
#
# Configuration comes from the environment (see .env.example). No secret is baked into the image.

# ---- 1. build the web UI -----------------------------------------------------------------
FROM node:20.18.0-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npx tsc --noEmit && npx vite build --outDir /ui-dist --emptyOutDir

# ---- 2. the application ------------------------------------------------------------------
FROM python:3.12.7-slim AS app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    GC_ENV=production \
    GC_DB_URL=sqlite:////data/green_computer.db \
    GC_DATA_DIR=/data/files
WORKDIR /srv
COPY backend/pyproject.toml ./backend/pyproject.toml
COPY backend/alembic.ini ./backend/alembic.ini
COPY backend/app ./backend/app
# Editable install keeps the source tree in place, so the UI can be dropped into app/static.
RUN pip install -e "./backend[postgres]"
COPY --from=ui /ui-dist ./backend/app/static
# bench/ holds the reference model builder used by local runs, and scripts for GPU jobs.
COPY bench ./bench
RUN useradd --system --create-home --uid 10001 gc && mkdir -p /data/files && chown -R gc /data
USER gc
WORKDIR /srv/backend
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/ready', timeout=4).status == 200 else 1)"
# One worker: the rate limiter and execution-job threads are per process. Scale with replicas
# behind a load balancer only after moving rate limits to a shared store.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]
