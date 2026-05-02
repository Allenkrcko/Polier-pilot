# syntax=docker/dockerfile:1.7
# ---- Base ----
FROM python:3.11-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Europe/Berlin \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# WeasyPrint + audio (pydub/ffmpeg) + locale + build tools.
# Keep this list tight - everything here lands in the runtime image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        ca-certificates \
        tzdata \
        locales \
        # WeasyPrint runtime deps
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libffi-dev \
        shared-mime-info \
        fonts-dejavu \
        fonts-liberation \
        # ffmpeg for pydub chunking of >25MB audio
        ffmpeg \
        # postgres client lib (for psycopg2/asyncpg builds)
        libpq-dev \
    && rm -rf /var/lib/apt/lists/* \
    && sed -i '/de_DE.UTF-8/s/^# //g' /etc/locale.gen \
    && locale-gen

WORKDIR /app

# ---- Dependencies layer (cached) ----
COPY pyproject.toml /app/
RUN pip install --upgrade pip && \
    pip install -e ".[dev]"

# ---- App source ----
COPY app /app/app
COPY alembic.ini /app/alembic.ini
COPY alembic /app/alembic

# Storage dir for uploaded media (mounted as volume in compose)
RUN mkdir -p /app/storage && chmod 0750 /app/storage

EXPOSE 8000

# Default command runs the API; the worker service overrides this in compose.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
