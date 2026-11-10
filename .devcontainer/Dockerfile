FROM node:24-bookworm-slim AS node

FROM ubuntu:24.04
COPY --from=node /usr/local/ /usr/local/

ARG DEBIAN_FRONTEND=noninteractive
ARG USERNAME=ubuntu

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    VIRTUAL_ENV=/workspace/.venv \
    UV_PROJECT_ENVIRONMENT=/workspace/.venv \
    PATH=/workspace/.venv/bin:${PATH} \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        git \
        openssh-client \
        openssl \
        procps \
        python3 \
        python3-dev \
        python3-pip \
        python3-venv \
        sudo \
    && rm -rf /var/lib/apt/lists/* \
    && echo "${USERNAME} ALL=(root) NOPASSWD:ALL" > "/etc/sudoers.d/${USERNAME}" \
    && chmod 0440 "/etc/sudoers.d/${USERNAME}" \
    && python3 -m pip install --break-system-packages uv \
    && mkdir -p /workspace \
    && uv venv --seed "${VIRTUAL_ENV}" \
    && chmod -R a+rwX "${VIRTUAL_ENV}"

RUN chown -R "${USERNAME}:${USERNAME}" "${VIRTUAL_ENV}"

ARG CODEX_VERSION=latest

RUN npm install --global "@openai/codex@${CODEX_VERSION}" \
    && npm cache clean --force \
    && mkdir -p /home/ubuntu/.codex \
    && chown -R "${USERNAME}:${USERNAME}" /home/ubuntu/.codex

COPY .devcontainer/start-codex-server.sh /usr/local/bin/start-codex-server
RUN chmod 0755 /usr/local/bin/start-codex-server

EXPOSE 4500

USER ${USERNAME}
WORKDIR /workspace

CMD ["sleep", "infinity"]
