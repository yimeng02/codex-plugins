#!/usr/bin/env sh
set -eu
MEMORY_PLUGIN_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
if command -v python3 >/dev/null 2>&1; then
    exec python3 "$MEMORY_PLUGIN_DIR/run.py" serve "$@"
fi
exec python "$MEMORY_PLUGIN_DIR/run.py" serve "$@"
