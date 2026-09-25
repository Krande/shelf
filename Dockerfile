# syntax=docker/dockerfile:1.7

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
RUN --mount=type=cache,target=/root/.cache/rattler,sharing=locked \
    pixi install --locked --environment prod

# Which Tesseract languages to keep. conda-forge's tesseract ships all 125
# models — 340 MB, where the apt package this replaced installed English alone
# at 4 MB. The rest are deleted here rather than carried into the runtime
# layer; add a language by listing it (and rebuilding), e.g. "eng osd deu".
# `osd` is orientation-and-script detection, which ocrmypdf uses to fix rotated
# scans, so it earns its 11 MB.
ARG TESSERACT_LANGS="eng osd"

# Strip what a runtime has no use for, before the COPY that carries the
# environment into the final image. Headers, static libraries, documentation
# and GObject introspection XML are build-time artefacts; everything removed
# here is inert at runtime, and `configs/`, `tessconfigs/` and `pdf.ttf` are
# deliberately kept — tesseract's PDF renderer, which is how ocrmypdf produces
# a searchable file, reads all three.
RUN set -eu; \
    env_dir=/app/.pixi/envs/prod; \
    keep=""; \
    for lang in ${TESSERACT_LANGS}; do keep="${keep} -not -name ${lang}.traineddata"; done; \
    find "${env_dir}/share/tessdata" -maxdepth 1 -name '*.traineddata' ${keep} -delete; \
    rm -rf "${env_dir}/include" \
           "${env_dir}/share/doc" \
           "${env_dir}/share/man" \
           "${env_dir}/share/info" \
           "${env_dir}/share/gir-1.0"; \
    find "${env_dir}" -name '*.a' -delete

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
# so moving it elsewhere breaks things that surface much later. OpenSSL is the
# one to know about — it bakes OPENSSLDIR to the build prefix, so an env copied
# to a different path silently loses its CA bundle and every TLS verification
# from Python starts failing.
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
# Belt and braces for the OPENSSLDIR coupling described above: named
# explicitly, so a future move of the environment fails visibly at build time
# instead of turning into unverifiable TLS at runtime.
ENV SSL_CERT_FILE=/app/.pixi/envs/prod/ssl/cacert.pem \
    SSL_CERT_DIR=/app/.pixi/envs/prod/ssl/certs

# The system trust store, which is *not* the same thing as the one above.
#
# Python reads the environment's own bundle; everything else in the pod reads
# /etc/ssl/certs/ca-certificates.crt. The old runtime base (python:3.12-slim)
# shipped that file, debian:bookworm-slim does not, and dropping it broke every
# non-Python TLS client — the injected vault-env sidecar is Go, so it failed
# Vault login with "certificate signed by unknown authority" while the API's own
# HTTPS worked fine.
#
# Sourced from the conda-forge ca-certificates package already in the locked
# environment rather than from apt: same Mozilla root set, one trust store for
# the whole image, and no unpinned package in an image whose entire point is
# that pixi.lock decides what is in it.
RUN mkdir -p /etc/ssl/certs \
    && cp /app/.pixi/envs/prod/ssl/cacert.pem /etc/ssl/certs/ca-certificates.crt

# WORKDIR is backend/ so the chart's bare `alembic upgrade head` finds
# alembic.ini beside it, exactly as it did when that directory was the image
# root.
WORKDIR /app/backend

RUN useradd -r -u 1000 shelf
USER 1000

# Fail the build, not a pod at 3am, if the environment cannot actually run what
# the chart asks of it: the app imports, the async engine has its greenlet, the
# OCR binaries are present with the language data they were pruned to, and both
# trust stores are in place — the system one because a missing
# ca-certificates.crt is invisible until a Go sidecar tries to speak TLS.
#
# Every dependency is imported by name, not just the ones on shelf.main's path,
# because a conda-forge package can differ from its PyPI namesake in what it
# exposes: conda-forge's pymupdf provides `fitz` and not `pymupdf`, so a module
# that imports the modern name would fail only when its consumer first ran.
RUN python -c "import shelf.main, shelf.worker.extract, sqlalchemy.ext.asyncio" \
 && python -c "import fastapi, uvicorn, sqlalchemy, asyncpg, alembic, pydantic, \
pydantic_settings, authlib, joserfc, httpx, multipart, itsdangerous, pypdf, \
nats, fitz, obstore, greenlet, ocrmypdf" \
 && alembic --help > /dev/null \
 && gs --version > /dev/null \
 && qpdf --version > /dev/null \
 && unpaper --version > /dev/null \
 && ocrmypdf --version > /dev/null \
 && tesseract --list-langs 2>&1 | grep -qx eng \
 && test -f /app/.pixi/envs/prod/share/tessdata/pdf.ttf \
 && test -s /etc/ssl/certs/ca-certificates.crt \
 && test -s "$SSL_CERT_FILE" \
 && python -c "import ssl; ssl.create_default_context().load_default_certs()"

EXPOSE 8000

CMD ["uvicorn", "shelf.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--proxy-headers", \
     "--forwarded-allow-ips=*"]
