# Install uv
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/


ARG BUILD_ENVIRONMENT=local


# Install apt packages
RUN apt-get update && apt-get install --no-install-recommends -y \
  # dependencies for building Python packages
  build-essential \
  # psycopg2 dependencies
  libpq-dev \
  # git \
  git


# Python 'run' stage
# Using a second stage reduces image size by roughly 200 m
FROM python:3.12-slim AS run-stage
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/


ARG BUILD_ENVIRONMENT=local
ARG APP_HOME=/app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1


# TODO: Using soft caps for memory usage and more might be a good idea
# RULE 7 https://cheatsheetseries.owasp.org/cheatsheets/Docker_Security_Cheat_Sheet.html
# see https://caprover.com/docs/service-update-override.html

# https://docs.astral.sh/uv/guides/integration/docker/
# TODO: Setting for production
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

WORKDIR ${APP_HOME}

# devcontainer dependencies and utils
RUN apt-get update && apt-get install --no-install-recommends -y \
  sudo git bash-completion ssh vim \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*

# Reduce access of the user inside docker
# the user is activated in the end
RUN addgroup --system django \
    && adduser --system --ingroup django django

ENV UV_CACHE_DIR=/opt/uv-cache/
RUN mkdir ${UV_CACHE_DIR}

# Install required system dependencies
RUN apt-get update && apt-get install --no-install-recommends -y \
  # Translations dependencies
  gettext \
  # Geospatioal dependencies
  binutils libproj-dev gdal-bin \
  # cleaning up unused files
  && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
  && rm -rf /var/lib/apt/lists/*


# Copy the start cmd to root
COPY --chown=django:django ./start /start
# Fix issues of line endings between operating systems
RUN sed -i 's/\r$//g' /start
# make start executable
RUN chmod +x /start


# Copy the start cmd to root
COPY --chown=django:django ./start_celery /start_celery
# Fix issues of line endings between operating systems
RUN sed -i 's/\r$//g' /start_celery
# make start executable
RUN chmod +x /start_celery


# Install dependencies, but not the project source.
# since the project source changes most often, this (should) reduces rebuilds of this layer

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project

# copy application code to WORKDIR
# let give rights to django
COPY --chown=django:django . ${APP_HOME}
# Sync the project into a new environment, asserting the lockfile is up to date
WORKDIR /app

# Sync the project
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked

# make django owner of the WORKDIR directory as well.
RUN chown django:django ${APP_HOME}

# run uv needs rights to use the uv cache
RUN chown -R django:django ${UV_CACHE_DIR}
USER django

CMD ${STARTUP_COMMAND}
