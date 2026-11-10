# sdf-reader development container

The development container uses Ubuntu 24.04 with Node.js 24, Python 3, and the
Codex CLI. `uv` creates the virtual environment at `/workspace/.venv`, and the
library is installed there in editable mode when the container is created. A
named Docker volume keeps this Linux environment separate from a host `.venv`.

## First-time setup

Rebuild and open the dev container. Its post-create script installs the local
Python package with `uv pip install --editable .`. The IDE interpreter is
`/workspace/.venv/bin/python`.

## Codex app server

Codex listens on container port 4500 and is published only on the host loopback
interface at `ws://localhost:4506`. Login state persists in the
`sdf-reader-codex-data` volume. Log in once from the repository root on the host:

```sh
docker compose -f .devcontainer/compose.yaml run --rm app codex login --device-auth
```

The application service starts the Codex app server even when no login is stored,
so a fresh volume does not block WebSocket connections. Its capability token is generated in
`.devcontainer/codex-ws-token`, which is ignored by Git. To supply a stable token
instead, set `CODEX_WS_TOKEN` in the container environment.

## Useful commands

Start the services without the IDE:

```sh
docker compose -f .devcontainer/compose.yaml up --build
```
