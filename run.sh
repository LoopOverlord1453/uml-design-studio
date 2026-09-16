#!/usr/bin/env sh
# UML Design Studio - Linux / macOS launcher.
# Uses the in-project virtual environment when there is one, otherwise
# falls back to whatever "python3" resolves to.
set -e
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ -x "$HERE/.venv/bin/python" ]; then
    exec "$HERE/.venv/bin/python" "$HERE/main.py" "$@"
else
    exec python3 "$HERE/main.py" "$@"
fi
