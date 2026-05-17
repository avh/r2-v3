#!/usr/bin/env bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
if [ -f server.env ]; then
  set -a
  source server.env
  set +a
fi

exec python main.py --port "${PORT:-8080}" --reload "$@"
