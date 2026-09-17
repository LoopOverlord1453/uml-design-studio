"""UML Design Studio - entry point.

    python main.py

Or use the launcher, which sets the environment up on the first run:

    run.bat      (Windows)
    ./run.sh     (Linux / macOS)
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    import importlib.util

    if importlib.util.find_spec("PyQt6.QtWidgets") is None:
        sys.stderr.write(
            "PyQt6 is not installed.\n\n"
            "The easiest way is the launcher, which builds a private\n"
            "environment and installs it for you:\n\n"
            "    run.bat      (Windows)\n"
            "    ./run.sh     (Linux / macOS)\n\n"
            "Or install it by hand:\n"
            "    python -m pip install -r requirements.txt\n")
        return 2

    from app.ui.main_window import run
    return run()


if __name__ == "__main__":
    sys.exit(main())
