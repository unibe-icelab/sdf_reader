#!/bin/sh
set -eu

token_file="${CODEX_WS_TOKEN_FILE:-/workspace/.devcontainer/codex-ws-token}"
server_port="${CODEX_APP_SERVER_PORT:-4500}"

umask 077

if [ -n "${CODEX_WS_TOKEN:-}" ]; then
  printf '%s\n' "$CODEX_WS_TOKEN" > "$token_file"
elif [ ! -s "$token_file" ]; then
  openssl rand -hex 32 > "$token_file"
fi

chmod 600 "$token_file"

if ! codex login status >/dev/null 2>&1; then
  echo "Codex is not logged in; starting the app server so a client can connect." >&2
  echo "To log in from the command line, run:" >&2
  echo "docker compose -f .devcontainer/compose.yaml run --rm app codex login --device-auth" >&2
fi

exec codex app-server \
  --listen "ws://0.0.0.0:${server_port}" \
  --ws-auth capability-token \
  --ws-token-file "$token_file"
