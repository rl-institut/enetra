FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Python build stage
FROM python as python-build-stage
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
FROM python as python-run-stage


ARG BUILD_ENVIRONMENT=local
ARG APP_HOME=/app

ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1


# https://docs.astral.sh/uv/guides/integration/docker/
# TODO: Setting for production
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

WORKDIR ${APP_HOME}

# devcontainer dependencies and utils
RUN apt-get update && apt-get install --no-install-recommends -y \
  sudo git bash-completion nano ssh vim

# Create devcontainer user and add it to sudoers
RUN groupadd --gid 1000 dev-user \
  && useradd --uid 1000 --gid dev-user --shell /bin/bash --create-home dev-user \
  && echo dev-user ALL=\(root\) NOPASSWD:ALL > /etc/sudoers.d/dev-user \
  && chmod 0440 /etc/sudoers.d/dev-user

# Install required system dependencies
RUN apt-get update && apt-get install --no-install-recommends -y \
  # Translations dependencies
  gettext \
  # Geospatioal dependencies
  binutils libproj-dev gdal-bin \
  # cleaning up unused files
  && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
  && rm -rf /var/lib/apt/lists/*


RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync

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


# Install dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project
# copy application code to WORKDIR
COPY . ${APP_HOME}

# Sync the project into a new environment, asserting the lockfile is up to date
WORKDIR /app
# Sync the project
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked
CMD ${STARTUP_COMMAND}
