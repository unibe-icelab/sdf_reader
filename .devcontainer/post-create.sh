#!/usr/bin/env bash
set -euo pipefail

cd /workspace

echo "Installing sdf-reader into the uv-managed virtual environment..."
uv pip install --python "${VIRTUAL_ENV}/bin/python" --editable .

echo "sdf-reader development environment is ready."
