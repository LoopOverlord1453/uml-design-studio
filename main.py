"""UML State Diagram Tool - giris noktasi.

    python main.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    import importlib.util

    if importlib.util.find_spec("PyQt6.QtWidgets") is None:
        sys.stderr.write(
            "PyQt6 is not installed.\n"
            "Install it with:  python -m pip install -r requirements.txt\n")
        return 2

    from app.ui.main_window import run
    return run()


if __name__ == "__main__":
    sys.exit(main())
