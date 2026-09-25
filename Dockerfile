# ── Stage 1: build the SPA ───────────────────────────────────────────────────
# Keep this in lockstep with `nodejs = ">=22,<23"` in pixi.toml's `dev`
# feature so local pixi runs and the container build agree on Node major.
FROM node:22-alpine AS frontend
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ .
ARG DOCKER_IMAGE_TAG=dev
ENV VITE_APP_VERSION=${DOCKER_IMAGE_TAG}
RUN npm run build

# ── Stage 2: solve nothing, install the locked environment ───────────────────
#
# `pixi install --locked` fails rather than re-solving if pixi.lock and
# pixi.toml disagree, which is the whole point of this stage: the image gets
# the exact package versions CI tested, from conda-forge, and a dependency
# release cannot change what ships without a lockfile change to review.
#
# It replaces a `pip install .` against unpinned specs in pyproject.toml. That
# arrangement put the images on their own dependency resolution, which is how
# SQLAlchemy 2.1.0 reached production the day it was published — greenlet no
# longer installed with it, every pod dead on `import sqlalchemy.ext.asyncio`,
# CI green on the lockfile's 2.0.49 throughout.
#
# The apt layer this used to need is gone with it: tesseract, ghostscript,
# unpaper, pngquant and qpdf are conda-forge packages in the `ocr` feature, so
# they are locked like everything else instead of floating with the base image.
FROM ghcr.io/prefix-dev/pixi:0.78.0-bookworm AS build

WORKDIR /app
COPY pixi.toml pixi.lock ./
# `shelf` is a path dependency, so the manifest it names has to be here for the
# install to resolve. It is copied to the path the lock records, which is also
# where the runtime stage keeps it — an editable install points at its source.
COPY backend ./backend
# `--locked` validates the *whole* manifest, not just the environment being
# installed, so the other two path dependencies have to be readable even though
# only the `dev` feature uses them and nothing here installs them. Manifests
# alone would do; copying the directories keeps the COPY lines honest.
COPY migrate ./migrate
COPY cli ./cli
RUN pixi install --locked --environment prod

# ── Stage 3: runtime ─────────────────────────────────────────────────────────
FROM debian:bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SHELF_FRONTEND_DIR=/app/frontend

# Stamp the build's image tag into the runtime env so api+worker can
# surface "I am running sha-XXXXX" without needing the deployment YAML.
# DOCKER_IMAGE_TAG is set by CI; it falls back to "dev" for local builds.
ARG DOCKER_IMAGE_TAG=dev
ENV SHELF_IMAGE_TAG=${DOCKER_IMAGE_TAG}

# The environment is copied to the same absolute path it was built at: a conda
# environment has that prefix compiled into scripts and shared-library headers,
# so moving it elsewhere breaks binaries in ways that surface much later.
COPY --from=build /app/.pixi/envs/prod /app/.pixi/envs/prod
COPY --from=build /app/backend /app/backend
COPY --from=frontend /app/dist /app/frontend

# Activation by ENV rather than by an entrypoint script, because the Helm chart
# overrides the entrypoint: the migration job runs `alembic upgrade head` and
# the worker `python -m shelf.worker` (see deploy/helm/shelf/templates). A
# wrapper script would simply not run for either, so everything they need has
# to be in the environment itself.
ENV PATH=/app/.pixi/envs/prod/bin:$PATH
# Normally set by conda-forge's tesseract activation hook, which nothing
# activates here. ocrmypdf shells out to tesseract, and tesseract without this
# finds no language data at all.
ENV TESSDATA_PREFIX=/app/.pixi/envs/prod/share/tessdata

# WORKDIR is backend/ so the chart's bare `alembic upgrade head` finds
# alembic.ini beside it, exactly as it did when that directory was the image
# root.
WORKDIR /app/backend

RUN useradd -r -u 1000 shelf
USER 1000

# Fail the build, not a pod at 3am, if the environment cannot actually run what
# the chart asks of it: the app imports, the async engine has its greenlet, and
# the OCR binaries are present with English language data.
RUN python -c "import shelf.main, shelf.worker.extract, sqlalchemy.ext.asyncio" \
 && alembic --help > /dev/null \
 && gs --version > /dev/null \
 && qpdf --version > /dev/null \
 && unpaper --version > /dev/null \
 && ocrmypdf --version > /dev/null \
 && tesseract --list-langs 2>&1 | grep -qx eng

EXPOSE 8000

CMD ["uvicorn", "shelf.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--proxy-headers", \
     "--forwarded-allow-ips=*"]
