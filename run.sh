#!/usr/bin/env sh
# ============================================================================
#  UML Design Studio - Linux / macOS launcher
#
#      ./run.sh
#
#  On the FIRST run it creates a private virtual environment under .venv and
#  installs PyQt6 into it; after that it just starts the application.
#
#  Nothing is installed system-wide: everything lands in .venv next to this
#  file, and deleting that folder undoes it completely.
#
#  If the file is not executable yet:  chmod +x run.sh
# ============================================================================
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
VENV="$HERE/.venv"
VPY="$VENV/bin/python"

die() {
    printf '\n%s\n\n' "$1" >&2
    exit 1
}

if [ ! -x "$VPY" ]; then
    # -- Find an interpreter to build the environment with. ------------------
    BOOT=""
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            BOOT="$candidate"
            break
        fi
    done
    [ -n "$BOOT" ] || die "Python 3 was not found.

  Debian/Ubuntu : sudo apt install python3 python3-venv python3-pip
  Fedora        : sudo dnf install python3 python3-pip
  Arch          : sudo pacman -S python python-pip
  macOS         : brew install python"

    printf '\n  First run: setting up the environment (this happens once).\n\n'

    "$BOOT" -m venv "$VENV" || die "Could not create the virtual environment in:
    $VENV

  On Debian/Ubuntu the venv module ships separately:
    sudo apt install python3-venv"

    "$VPY" -m pip install --upgrade pip
    "$VPY" -m pip install -r "$HERE/requirements.txt" || die "Installing the dependencies failed.

  Qt needs a few system libraries that pip does not provide. On
  Debian/Ubuntu the one most often missing is:
    sudo apt install libxcb-cursor0 libgl1 libegl1"

    printf '\n  Setup finished. Starting UML Design Studio...\n\n'
fi

exec "$VPY" "$HERE/main.py" "$@"
