ARG NAUTOBOT_VERSION=3.2
ARG PYTHON_VER=3.12

FROM ghcr.io/nautobot/nautobot:${NAUTOBOT_VERSION}-py${PYTHON_VER}

USER root


# Uninstall Nautobot

# Copy Poetry file and install dependencies
COPY pyproject.toml
RUN INSTALLED_NAUTOBOT_VER=$(pip show nautobot | grep "^Version" | sed "s/Version: //"); \
    poetry add --lock nautobot@${INSTALLED_NAUTOBOT_VER} --python ${PYTHON_VER}


