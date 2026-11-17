ARG REGISTRY=ghcr.io
ARG REPOSITORY=nautobot/nautobot
ARG NAUTOBOT_VER=3.2.7
ARG PYTHON_VER=3.12

# Resolve dependencies from pyproject.toml into a fresh lock file
FROM ${REGISTRY}/${REPOSITORY}:${NAUTOBOT_VER}-py${PYTHON_VER} AS lock

ARG POETRY_VERSION=2.5.1
ARG POETRY_PLUGIN_EXPORT_VERSION=1.10.1

USER root
RUN pip install --no-cache-dir \
    "poetry==${POETRY_VERSION}" \
    "poetry-plugin-export==${POETRY_PLUGIN_EXPORT_VERSION}"

WORKDIR /source
COPY pyproject.toml ./
COPY plugins/ ./plugins/
RUN poetry lock && \
    poetry export --format requirements.txt --output requirements.txt

# Final image: install the locked dependencies, without Poetry
FROM ${REGISTRY}/${REPOSITORY}:${NAUTOBOT_VER}-py${PYTHON_VER}

USER root
COPY --from=lock /source/requirements.txt /tmp/requirements.txt
# Same path as the lock stage, since requirements.txt references plugin files by absolute path
COPY plugins/ /source/plugins/
RUN pip install --no-cache-dir -r /tmp/requirements.txt && \
    rm -rf /tmp/requirements.txt /source

COPY --chown=nautobot:nautobot config/nautobot_config.py /opt/nautobot/nautobot_config.py

USER nautobot
