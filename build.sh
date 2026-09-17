#!/usr/bin/env sh
# ============================================================================
#  UML Design Studio - build a standalone executable
#
#      ./build.sh
#
#  What comes out depends on the system you run it on:
#
#      Linux   dist/UML-Design-Studio          one ELF file, already executable
#      macOS   dist/UML Design Studio.app      a real app bundle -- double-click
#                                              it and no Terminal appears
#              dist/UML-Design-Studio          the plain binary, for a terminal
#
#  PyInstaller is not a cross-compiler: it bundles the interpreter and the Qt
#  libraries OF THIS MACHINE. To get a Windows .exe, run build.bat on Windows.
#
#  If the file is not executable yet:  chmod +x build.sh
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

    printf '\n  Setting up the environment (this happens once)...\n\n'
    "$BOOT" -m venv "$VENV" || die "Could not create the virtual environment.

  On Debian/Ubuntu the venv module ships separately:
    sudo apt install python3-venv"
    "$VPY" -m pip install --upgrade pip
    "$VPY" -m pip install -r "$HERE/requirements.txt"
fi

printf '\n  Making sure PyInstaller is available...\n'
"$VPY" -m pip install --upgrade pyinstaller

printf '\n  Building...\n\n'
"$VPY" -m PyInstaller --clean --noconfirm "$HERE/UML-Design-Studio.spec"

printf '\n  ------------------------------------------------------------------\n'
case "$(uname -s)" in
    Darwin)
        printf '   Done:  %s\n' "$HERE/dist/UML Design Studio.app"
        printf '  ------------------------------------------------------------------\n\n'
        printf '  Drag the .app into /Applications. macOS will refuse to open it\n'
        printf '  until you allow it once: right-click the app and choose Open, or\n'
        printf '  clear the download flag with\n'
        printf '      xattr -dr com.apple.quarantine "%s"\n\n' \
               "$HERE/dist/UML Design Studio.app"
        ;;
    *)
        printf '   Done:  %s\n' "$HERE/dist/UML-Design-Studio"
        printf '  ------------------------------------------------------------------\n\n'
        printf '  Copy that single file anywhere -- it needs no Python installation.\n\n'
        ;;
esac
