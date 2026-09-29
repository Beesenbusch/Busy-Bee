"""Launcher without a console window: double-click, or run with pythonw.exe."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from busybee.app import main  # noqa: E402

main()
