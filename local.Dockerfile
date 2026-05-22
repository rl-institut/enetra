# define an alias for the specific python version used in this file.
FROM ghcr.io/astral-sh/uv:python3.10-bookworm-slim AS python

# Python build stage
FROM python AS python-build-stage

ARG APP_HOME=/app

WORKDIR ${APP_HOME}

# we need to move the virtualenv outside of the $APP_HOME directory because it will be overriden by the docker compose mount
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0 UV_PROJECT_ENVIRONMENT=/venv

# Install apt packages
RUN apt-get update && apt-get install --no-install-recommends -y \
  # dependencies for building Python packages
  build-essential \
  # psycopg dependencies
  libpq-dev \
  gettext \
  wait-for-it\
  # needed for git dependencies
  git \
  binutils libproj-dev gdal-bin


# devcontainer dependencies and utils
RUN apt-get update && apt-get install --no-install-recommends -y \
  sudo bash-completion nano vim which ssh

# Requirements are installed here to ensure they will be cached.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock:rw \
    uv sync --no-install-project


COPY . ${APP_HOME}

# NOTE: No need to installe the project
# RUN --mount=type=cache,target=/root/.cache/uv \
#     --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
#     --mount=type=bind,source=uv.lock,target=uv.lock:rw \
#     uv sync


# Create devcontainer user and add it to sudoers
# RUN groupadd --gid 1000 dev-user \
#   && useradd --uid 1000 --gid dev-user --shell /bin/bash --create-home dev-user \
#   && echo dev-user ALL=\(root\) NOPASSWD:ALL > /etc/sudoers.d/dev-user \
#   && chmod 0440 /etc/sudoers.d/dev-user

ENV PATH="/venv/bin:$PATH"
ENV PYTHONPATH="/venv/lib/python3.10/site-packages:$PYTHONPATH"


# The django user does not get write access to the starscripts, but they are made executable
# Less lines than before means fewer creations of checkpoints
COPY ./start_dev /start_dev
COPY ./start_celery /start_celery
RUN sed -i 's/\r$//g' /start_dev /start_celery && chmod +x /start_dev /start_celery


CMD ${STARTUP_COMMAND}
