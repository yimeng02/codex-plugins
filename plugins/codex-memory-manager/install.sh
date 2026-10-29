#!/usr/bin/env sh
set -eu
MEMORY_INSTALL_ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
if command -v python3 >/dev/null 2>&1; then
    exec python3 "$MEMORY_INSTALL_ROOT/install.py" "$@"
fi
if command -v python >/dev/null 2>&1; then
    exec python "$MEMORY_INSTALL_ROOT/install.py" "$@"
fi
printf '%s\n' 'Python 3.10+ is required. Install Python, then run this script again.' >&2
exit 1
