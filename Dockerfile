# ── Stage 1: build the SPA ───────────────────────────────────────────────────
# Keep this in lockstep with `nodejs = ">=22,<23"` in pixi.toml so local
# pixi runs and the container build agree on Node major.
FROM node:22-alpine AS frontend
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ .
ARG DOCKER_IMAGE_TAG=dev
ENV VITE_APP_VERSION=${DOCKER_IMAGE_TAG}
RUN npm run build

# ── Stage 2: API + bundled SPA ───────────────────────────────────────────────
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SHELF_FRONTEND_DIR=/app/frontend

# Stamp the build's image tag into the runtime env so api+worker can
# surface "I am running sha-XXXXX" without needing the deployment YAML.
# DOCKER_IMAGE_TAG is set by CI; it falls back to "dev" for local builds.
ARG DOCKER_IMAGE_TAG=dev
ENV SHELF_IMAGE_TAG=${DOCKER_IMAGE_TAG}

WORKDIR /app

# System deps for ocrmypdf (Tesseract-based OCR worker, Phase B).
# tesseract-ocr-eng is the only language pack we ship by default; add
# more via apt (tesseract-ocr-{deu,fra,…}) if multilingual docs become
# common. ghostscript is required for the redo-ocr image transforms;
# unpaper is optional but improves scan quality cheaply.
#
# The sed step rewrites the Debian apt sources from http to https.
# Our Forgejo CI runner's egress blocks outbound port 80, so the
# default http://deb.debian.org URIs fail with "connection refused"
# while pip's PyPI fetches over 443 work fine. HTTPS apt sources have
# been first-class in Debian for years; no apt-transport-https needed.
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g; s|http://security.debian.org|https://security.debian.org|g' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        ghostscript \
        unpaper \
        pngquant \
        qpdf \
    && rm -rf /var/lib/apt/lists/*

COPY backend/pyproject.toml ./
COPY backend/src ./src
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./

# `[ocr]` pulls in ocrmypdf for the Phase B Tesseract worker.
# The extra is split out from default deps because marker-pdf in
# the GPU image's `[gpu]` extra transitively pins pypdfium2==4.30
# while ocrmypdf needs >=5.0; we install only one engine per image.
RUN pip install --upgrade pip && pip install '.[ocr]'

COPY --from=frontend /app/dist /app/frontend

RUN useradd -r -u 1000 shelf
USER 1000

EXPOSE 8000

CMD ["uvicorn", "shelf.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--proxy-headers", \
     "--forwarded-allow-ips=*"]
